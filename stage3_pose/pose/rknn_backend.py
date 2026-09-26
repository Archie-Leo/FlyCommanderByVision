"""RK3576 native YOLOv8n-Pose backend for the frozen PoseFrameV1 contract."""
from __future__ import annotations

import ctypes as c
import hashlib
import math
from pathlib import Path
import statistics
import threading
import time

import numpy as np

from config import BackendConfig
from .backend import PoseBackend
from .types import JointObservation, PersonPose, PoseFrame


MODEL_SHA256 = "95c857fc580814ff08ab5e417b904ea4a926bfdded68104520c540a17c186b9c"
COCO_TO_CANONICAL = {
    "nose": 0,
    "left_shoulder": 5, "right_shoulder": 6,
    "left_elbow": 7, "right_elbow": 8,
    "left_wrist": 9, "right_wrist": 10,
    "left_hip": 11, "right_hip": 12,
    "left_knee": 13, "right_knee": 14,
    "left_ankle": 15, "right_ankle": 16,
}


class _Detection(c.Structure):
    _fields_ = [("detector_bbox_xyxy", c.c_float * 4), ("person_score", c.c_float),
                ("keypoints", (c.c_float * 3) * 17)]


class _Timing(c.Structure):
    _fields_ = [(name, c.c_double) for name in
                ("preprocess_ms", "inference_ms", "decode_ms", "total_ms")]


class NativePoseRuntime:
    """Small ctypes ABI; no raw model tensors cross into Python."""

    def __init__(self, library_path: Path, model_path: Path, core_mask: int | None = None):
        library = Path(library_path).expanduser().resolve()
        if not library.is_file():
            raise FileNotFoundError(f"RKNN Pose native library missing: {library}")
        self.lib = c.CDLL(str(library))
        self.lib.fcv_pose_abi_version.restype = c.c_int
        if self.lib.fcv_pose_abi_version() != 1:
            raise RuntimeError("Unsupported RKNN Pose native ABI")
        self.lib.fcv_pose_last_error.restype = c.c_char_p
        self.lib.fcv_pose_create.argtypes = [c.c_char_p]
        self.lib.fcv_pose_create.restype = c.c_void_p
        self.lib.fcv_pose_infer.argtypes = [c.c_void_p, c.POINTER(c.c_uint8), c.c_int,
                                            c.c_int, c.c_int, c.c_int, c.POINTER(_Detection),
                                            c.c_int, c.POINTER(c.c_int), c.POINTER(_Timing)]
        self.lib.fcv_pose_infer.restype = c.c_int
        self.lib.fcv_pose_destroy.argtypes = [c.c_void_p]
        if core_mask is None or core_mask == 0:
            self.handle = self.lib.fcv_pose_create(str(model_path).encode())
        else:
            self.lib.fcv_pose_create_with_core_mask.argtypes = [c.c_char_p, c.c_int]
            self.lib.fcv_pose_create_with_core_mask.restype = c.c_void_p
            self.handle = self.lib.fcv_pose_create_with_core_mask(
                str(model_path).encode(), int(core_mask))
        if not self.handle:
            raise RuntimeError(self._error())

    def _error(self):
        raw = self.lib.fcv_pose_last_error()
        return raw.decode(errors="replace") if raw else "unknown RKNN Pose error"

    def infer(self, bgr_frame: np.ndarray, max_poses: int):
        if not self.handle:
            raise RuntimeError("RKNN Pose runtime closed")
        frame = np.ascontiguousarray(bgr_frame)
        output = (_Detection * max_poses)()
        count = c.c_int()
        timing = _Timing()
        rc = self.lib.fcv_pose_infer(self.handle, frame.ctypes.data_as(c.POINTER(c.c_uint8)),
                                     frame.shape[1], frame.shape[0], frame.strides[0],
                                     max_poses, output, max_poses, c.byref(count), c.byref(timing))
        if rc != 0 or count.value < 0 or count.value > max_poses:
            raise RuntimeError(self._error())
        detections = [{"person_score": float(output[i].person_score),
                       "detector_bbox_xyxy": tuple(output[i].detector_bbox_xyxy),
                       "keypoints": tuple(tuple(float(v) for v in p)
                                          for p in output[i].keypoints)}
                      for i in range(count.value)]
        times = {name: float(getattr(timing, name)) for name, _ in _Timing._fields_}
        return detections, times

    def close(self):
        if self.handle:
            self.lib.fcv_pose_destroy(self.handle)
            self.handle = None


class RKNNPoseBackend(PoseBackend):
    name = "rknn_yolov8n_pose_int8"

    def __init__(self, config: BackendConfig, model_path: Path, library_path: Path,
                 expected_sha256: str | None = MODEL_SHA256, runtime_factory=NativePoseRuntime,
                 core_mask: int | None = None):
        self.model_path = Path(model_path).expanduser().resolve()
        if not self.model_path.is_file() or self.model_path.stat().st_size < 100_000:
            raise FileNotFoundError(f"RKNN Pose model missing/invalid: {self.model_path}")
        self.model_sha256 = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        if expected_sha256 and self.model_sha256.lower() != expected_sha256.lower():
            raise ValueError(f"RKNN Pose model SHA256 mismatch: {self.model_sha256}")
        if not 1 <= config.num_poses <= 128:
            raise ValueError("RKNN Pose num_poses must be in 1..128")
        self.max_poses = config.num_poses
        self.runtime = (runtime_factory(library_path, self.model_path) if core_mask is None
                        else runtime_factory(library_path, self.model_path, core_mask=core_mask))
        self.lock = threading.Lock()
        self.last_timestamp_ms = -1
        self.last_timings = {}

    def infer(self, bgr_frame: np.ndarray, timestamp_ms: int, frame_id: int) -> PoseFrame:
        if not isinstance(bgr_frame, np.ndarray) or bgr_frame.dtype != np.uint8 or \
                bgr_frame.ndim != 3 or bgr_frame.shape[2] != 3 or \
                bgr_frame.shape[0] <= 0 or bgr_frame.shape[1] <= 0:
            raise ValueError("RKNN Pose expects BGR uint8 HxWx3 frame")
        height, width = bgr_frame.shape[:2]
        with self.lock:
            if self.runtime is None:
                raise RuntimeError("RKNN Pose backend closed")
            timestamp_ms = max(int(timestamp_ms), self.last_timestamp_ms + 1)
            self.last_timestamp_ms = timestamp_ms
            start = time.perf_counter()
            detections, native_times = self.runtime.infer(bgr_frame, self.max_poses)
            bridge_end = time.perf_counter()
            if len(detections) > self.max_poses:
                raise RuntimeError("RKNN Pose returned more than configured num_poses")
            poses = [self._map_person(detection, timestamp_ms, frame_id, index, width, height)
                     for index, detection in enumerate(detections)]
            end = time.perf_counter()
            self.last_timings = dict(native_times, bridge_ms=max(0.0,
                (bridge_end-start)*1000 - native_times.get("total_ms", 0.0)),
                poseframe_ms=(end-bridge_end)*1000, total_infer_ms=(end-start)*1000)
            return PoseFrame(timestamp_ms, frame_id, width, height, poses,
                             self.name, self.last_timings["total_infer_ms"])

    def _map_person(self, detection, timestamp_ms, frame_id, local_id, width, height):
        keypoints = detection["keypoints"]
        if len(keypoints) != 17 or not math.isfinite(float(detection["person_score"])):
            raise RuntimeError("Invalid RKNN Pose detection schema")
        joints = {}
        xs, ys, scores = [], [], []
        for name, index in COCO_TO_CANONICAL.items():
            point = keypoints[index]
            if len(point) != 3:
                raise RuntimeError("RKNN Pose keypoint must contain x,y,confidence")
            x, y, score = (float(v) for v in point)
            if not all(math.isfinite(v) for v in (x, y, score)) or not 0.0 <= score <= 1.0:
                raise RuntimeError(f"Nonfinite/invalid RKNN Pose keypoint: {name}")
            joints[name] = JointObservation(name, x, y, x/width, y/height,
                                             score, visibility=None, presence=None, valid=True)
            xs.append(x); ys.append(y); scores.append(score)
        bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0., 0., 0., 0.)
        pose_score = statistics.median(scores) if scores else None
        return PersonPose(timestamp_ms, frame_id, local_id, width, height,
                          bbox, pose_score, self.name, joints)

    def close(self) -> None:
        with self.lock:
            runtime, self.runtime = self.runtime, None
            if runtime is not None:
                runtime.close()
