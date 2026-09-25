"""RKNN Stage5 feature contract without hardware or model assets."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stage5_v2.reid import prepare_crop
from stage5_v2.reid_rknn import RKNNOSNetEmbedder


class FakeRuntime:
    def __init__(self, output=None, load=0, init=0):
        self.output = np.ones((1, 512), np.float32) if output is None else output
        self.load_result = load
        self.init_result = init
        self.released = False
        self.inputs = None

    def load_rknn(self, path):
        return self.load_result

    def init_runtime(self):
        return self.init_result

    def inference(self, inputs):
        self.inputs = inputs
        return [self.output]

    def release(self):
        self.released = True


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "osnet.rknn"
    path.write_bytes(b"r" * 100_001)
    return path


@pytest.fixture
def frame():
    return np.random.default_rng(42).integers(0, 256, (300, 200, 3), dtype=np.uint8)


def test_missing_model_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        RKNNOSNetEmbedder(tmp_path / "missing.rknn", runtime_factory=FakeRuntime)


def test_hash_mismatch_fails_before_runtime(model):
    with pytest.raises(ValueError, match="SHA256"):
        RKNNOSNetEmbedder(model, "0" * 64, runtime_factory=FakeRuntime)


@pytest.mark.parametrize("load,init", [(1, 0), (0, 1)])
def test_startup_error_releases_runtime(model, load, init):
    runtime = FakeRuntime(load=load, init=init)
    with pytest.raises(RuntimeError):
        RKNNOSNetEmbedder(model, runtime_factory=lambda: runtime)
    assert runtime.released


@pytest.mark.parametrize("output", [np.ones((1, 511), np.float32),
                                    np.full((1, 512), np.nan, np.float32),
                                    np.full((1, 512), np.inf, np.float32),
                                    np.zeros((1, 512), np.float32)])
def test_invalid_output_fails_closed(model, frame, output):
    backend = RKNNOSNetEmbedder(model, runtime_factory=lambda: FakeRuntime(output))
    with pytest.raises(RuntimeError, match="invalid feature"):
        backend.extract(frame, (20, 20, 180, 280))
    backend.close()


def test_normalized_512_feature_and_identical_crop(model, frame):
    runtime = FakeRuntime()
    backend = RKNNOSNetEmbedder(model, runtime_factory=lambda: runtime)
    embedding, quality = backend.extract(frame, (20, 20, 180, 280))
    expected, expected_quality = prepare_crop(frame, (20, 20, 180, 280))
    assert quality == expected_quality
    assert len(embedding) == 512
    assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-6)
    assert runtime.inputs[0].shape == (1, 256, 128, 3)
    assert runtime.inputs[0].flags.c_contiguous
    np.testing.assert_array_equal(runtime.inputs[0][0], expected)
    backend.close()
    assert runtime.released
    with pytest.raises(RuntimeError, match="closed"):
        backend.extract(frame, (20, 20, 180, 280))


def test_low_quality_crop_skips_inference(model):
    runtime = FakeRuntime()
    backend = RKNNOSNetEmbedder(model, runtime_factory=lambda: runtime)
    value, quality = backend.extract(np.zeros((100, 100, 3), np.uint8), (0, 0, 10, 50))
    assert value is None and quality == 0.0
    assert runtime.inputs is None
    backend.close()
