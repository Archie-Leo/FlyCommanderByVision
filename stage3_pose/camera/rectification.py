from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


EXPECTED_CALIBRATION_ID = "20260914_092939_UTC__run_B_exclude_0004_0027"


class LeftRectifier:
    def __init__(self, calibration_path: Path):
        self.path = Path(calibration_path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Run B calibration not found: {self.path}")
        fs = cv2.FileStorage(str(self.path), cv2.FILE_STORAGE_READ)
        if not fs.isOpened():
            raise RuntimeError(f"Cannot open OpenCV calibration YAML: {self.path}")
        try:
            self.width = int(fs.getNode("image_width").real())
            self.height = int(fs.getNode("image_height").real())
            matrices = {name: fs.getNode(name).mat() for name in ("K_left", "D_left", "R1", "P1")}
        finally:
            fs.release()
        if (self.width, self.height) != (1280, 960):
            raise ValueError(f"Calibration eye size must be 1280x960, got {self.width}x{self.height}")
        if any(value is None or value.size == 0 for value in matrices.values()):
            raise ValueError("Calibration is missing K_left/D_left/R1/P1")
        self.map_x, self.map_y = cv2.initUndistortRectifyMap(
            matrices["K_left"], matrices["D_left"], matrices["R1"], matrices["P1"],
            (self.width, self.height), cv2.CV_32FC1
        )
        self.calibration_id = self.path.parent.name

    def rectify(self, left_bgr: np.ndarray) -> np.ndarray:
        if left_bgr.shape[:2] != (self.height, self.width):
            raise ValueError(f"LEFT frame must be {self.width}x{self.height}, got {left_bgr.shape[1]}x{left_bgr.shape[0]}")
        return cv2.remap(left_bgr, self.map_x, self.map_y, cv2.INTER_LINEAR)

