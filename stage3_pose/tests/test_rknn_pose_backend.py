"""Frozen PoseFrameV1 mapping and fail-closed tests without an NPU dependency."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from config import BackendConfig, QualityConfig, NormalizationConfig
from pose.rknn_backend import COCO_TO_CANONICAL, RKNNPoseBackend, NativePoseRuntime
from pose.quality import PoseQualityEvaluator
from pose.normalize import SkeletonNormalizer


def detection(x_offset=0, score=.9):
    points = [(float(200+i*9+x_offset), float(120+i*12), score) for i in range(17)]
    return {"person_score": .8, "detector_bbox_xyxy": (10., 20., 500., 600.),
            "keypoints": points}


class FakeRuntime:
    def __init__(self, detections=()):
        self.detections = list(detections)
        self.closed = False
        self.calls = 0

    def infer(self, frame, max_poses):
        self.calls += 1
        return self.detections[:max_poses], {"preprocess_ms": .1, "inference_ms": .2,
                                            "decode_ms": .3, "total_ms": .6}

    def close(self):
        self.closed = True


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "model.rknn"
    path.write_bytes(b"m" * 100_001)
    return path


@pytest.fixture
def frame():
    return np.zeros((960, 1280, 3), np.uint8)


def backend(model, detections=(), num_poses=4):
    runtime = FakeRuntime(detections)
    pose = RKNNPoseBackend(BackendConfig(num_poses=num_poses), model, Path("/fake.so"),
                           expected_sha256=None, runtime_factory=lambda *_: runtime)
    return pose, runtime


def test_missing_model_and_hash_fail_closed(tmp_path, model):
    with pytest.raises(FileNotFoundError):
        RKNNPoseBackend(BackendConfig(), tmp_path / "missing.rknn", tmp_path / "x.so")
    with pytest.raises(ValueError, match="SHA256"):
        RKNNPoseBackend(BackendConfig(), model, tmp_path / "x.so")


def test_missing_native_library(model, tmp_path):
    with pytest.raises(FileNotFoundError, match="native library"):
        RKNNPoseBackend(BackendConfig(), model, tmp_path / "missing.so",
                        expected_sha256=None)


@pytest.mark.parametrize("bad", [np.zeros((2, 2), np.uint8),
                                     np.zeros((2, 2, 4), np.uint8),
                                     np.zeros((2, 2, 3), np.float32)])
def test_invalid_frame_shape_or_type(model, bad):
    pose, runtime = backend(model)
    with pytest.raises(ValueError, match="BGR uint8"):
        pose.infer(bad, 100, 1)
    assert runtime.calls == 0
    pose.close()


def test_no_person_is_empty_and_close(model, frame):
    pose, runtime = backend(model)
    output = pose.infer(frame, 100, 7)
    assert output.poses == []
    assert output.schema_version == "PoseFrameV1"
    assert (output.timestamp_ms, output.frame_id, output.image_width, output.image_height) == (100, 7, 1280, 960)
    pose.close()
    assert runtime.closed
    with pytest.raises(RuntimeError, match="closed"):
        pose.infer(frame, 101, 8)


def test_one_person_canonical_mapping_bbox_and_score(model, frame):
    pose, _ = backend(model, [detection()])
    output = pose.infer(frame, 100, 7)
    person = output.poses[0]
    assert person.schema_version == "PersonPoseV1"
    assert (person.timestamp_ms, person.frame_id, person.local_detection_id) == (100, 7, 0)
    assert len(person.joints) == 13
    assert set(person.joints) == set(COCO_TO_CANONICAL)
    assert person.joints["left_shoulder"].x_px == 200+5*9
    assert person.joints["right_shoulder"].x_px == 200+6*9
    assert person.joints["left_wrist"].x_px == 200+9*9
    assert person.joints["right_wrist"].x_px == 200+10*9
    assert person.joints["left_hip"].x_px == 200+11*9
    assert person.joints["right_hip"].x_px == 200+12*9
    assert person.joints["left_shoulder"].x_norm_image == pytest.approx(245/1280)
    assert person.joints["left_shoulder"].y_norm_image == pytest.approx(180/960)
    assert person.joints["nose"].visibility is None
    assert person.joints["nose"].presence is None
    assert person.bbox_xyxy == (200., 120., 344., 312.)  # mapped 13, not detector box
    assert person.pose_score == pytest.approx(.9)  # median joint score, not .8 detector
    pose.close()


def test_multi_person_limit_order_and_monotonic_time(model, frame):
    pose, _ = backend(model, [detection(0), detection(500), detection(800)], num_poses=2)
    first = pose.infer(frame, 100, 1)
    second = pose.infer(frame, 100, 2)
    assert len(first.poses) == len(second.poses) == 2
    assert [p.local_detection_id for p in first.poses] == [0, 1]
    assert [p.joints["nose"].x_px for p in first.poses] == [200., 700.]
    assert second.timestamp_ms == 101
    assert all(p.timestamp_ms == 101 and p.frame_id == 2 for p in second.poses)
    pose.close()


def test_low_joint_score_is_preserved_for_existing_quality_gate(model, frame):
    raw = detection()
    raw["keypoints"][9] = (281., 228., .1)
    pose, _ = backend(model, [raw])
    person = pose.infer(frame, 100, 1).poses[0]
    assert person.joints["left_wrist"].valid
    assert person.joints["left_wrist"].confidence == .1
    quality = PoseQualityEvaluator(QualityConfig()).evaluate(person)
    assert not quality.valid and "MISSING_LEFT_WRIST" in quality.reasons
    skeleton = SkeletonNormalizer(NormalizationConfig()).normalize(person, quality)
    assert not skeleton.valid
    pose.close()


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_nonfinite_keypoint_fails_closed(model, frame, bad):
    raw = detection()
    raw["keypoints"][5] = (bad, 180., .9)
    pose, _ = backend(model, [raw])
    with pytest.raises(RuntimeError, match="Nonfinite"):
        pose.infer(frame, 100, 1)
    pose.close()


def test_bad_output_shape_and_excess_persons_fail_closed(model, frame):
    raw = detection()
    raw["keypoints"] = raw["keypoints"][:16]
    pose, _ = backend(model, [raw])
    with pytest.raises(RuntimeError, match="schema"):
        pose.infer(frame, 100, 1)
    pose.close()


def test_native_abi_and_real_bus_when_assets_exist():
    repo = Path(__file__).resolve().parents[2]
    model = Path.home() / "fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/yolov8n-pose-rk3576-int8.rknn"
    image = Path.home() / "fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/bus.jpg"
    library = repo / "stage3_pose/build/rknn_pose/libfcv_rknn_pose.so"
    if not all(p.is_file() for p in (model, image, library)):
        pytest.skip("RK3576 frozen model/native library/bus image unavailable")
    import cv2
    runtime = NativePoseRuntime(library, model)
    try:
        detections, timing = runtime.infer(cv2.imread(str(image)), 4)
        assert len(detections) == 3
        assert all(len(d["keypoints"]) == 17 for d in detections)
        assert timing["inference_ms"] > 0
    finally:
        runtime.close()


def test_native_rectified_size_coordinates_and_repeatability_when_assets_exist():
    repo = Path(__file__).resolve().parents[2]
    model = Path.home() / "fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/yolov8n-pose-rk3576-int8.rknn"
    image = Path.home() / "fcv_third_party/rknn_model_zoo/examples/yolov8_pose/model/bus.jpg"
    library = repo / "stage3_pose/build/rknn_pose/libfcv_rknn_pose.so"
    if not all(p.is_file() for p in (model, image, library)):
        pytest.skip("RK3576 frozen model/native library/bus image unavailable")
    import cv2
    frame = cv2.resize(cv2.imread(str(image)), (1280, 960))
    runtime = NativePoseRuntime(library, model)
    try:
        first, _ = runtime.infer(frame, 4)
        assert len(first) == 3
        for detection in first:
            for x, y, confidence in detection["keypoints"]:
                assert 0 <= x < 1280
                assert 0 <= y < 960
                assert 0 <= confidence <= 1
        for _ in range(4):
            repeated, _ = runtime.infer(frame, 4)
            assert repeated == first
    finally:
        runtime.close()
