#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from config import DepthValidationConfig


REQUIRED_MATRICES = ("K_left", "D_left", "K_right", "D_right", "R1", "R2", "P1", "P2", "Q")
CSV_FIELDS = ("timestamp", "ground_truth_mm", "measured_depth_mm", "absolute_error_mm", "relative_error_percent", "clicked_x", "clicked_y", "median_disparity", "valid_samples")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")


def load_calibration(path: Path) -> Dict[str, Any]:
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_READ)
    if not fs.isOpened():
        raise ValueError(f"Could not open calibration YAML: {path}")
    data: Dict[str, Any] = {}
    try:
        for key in REQUIRED_MATRICES:
            node = fs.getNode(key)
            if node.empty() or node.mat() is None:
                raise ValueError(f"Calibration is missing matrix: {key}")
            data[key] = node.mat()
        for key in ("image_width", "image_height", "baseline_mm"):
            node = fs.getNode(key)
            if node.empty():
                raise ValueError(f"Calibration is missing scalar: {key}")
            data[key] = node.real()
        for key in ("result_status", "source_session", "excluded_pair_indices"):
            node = fs.getNode(key)
            data[key] = "" if node.empty() else node.string()
    finally:
        fs.release()
    if data["K_left"].shape != (3, 3) or data["K_right"].shape != (3, 3):
        raise ValueError("K matrices must be 3x3")
    if data["R1"].shape != (3, 3) or data["R2"].shape != (3, 3):
        raise ValueError("R1/R2 must be 3x3")
    if data["P1"].shape != (3, 4) or data["P2"].shape != (3, 4) or data["Q"].shape != (4, 4):
        raise ValueError("P1/P2/Q shapes must be 3x4, 3x4, 4x4")
    return data


def build_rectify_maps(cal: Dict[str, Any], size: Tuple[int, int]):
    left = cv2.initUndistortRectifyMap(cal["K_left"], cal["D_left"], cal["R1"], cal["P1"], size, cv2.CV_32FC1)
    right = cv2.initUndistortRectifyMap(cal["K_right"], cal["D_right"], cal["R2"], cal["P2"], size, cv2.CV_32FC1)
    return left, right


def build_matcher(cfg: DepthValidationConfig):
    return cv2.StereoSGBM_create(
        minDisparity=cfg.min_disparity,
        numDisparities=cfg.num_disparities,
        blockSize=cfg.block_size,
        P1=cfg.p1,
        P2=cfg.p2,
        disp12MaxDiff=cfg.disp12_max_diff,
        preFilterCap=cfg.pre_filter_cap,
        uniquenessRatio=cfg.uniqueness_ratio,
        speckleWindowSize=cfg.speckle_window_size,
        speckleRange=cfg.speckle_range,
        mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY,
    )


def process_pair(left: np.ndarray, right: np.ndarray, maps, matcher, q: np.ndarray, cfg: DepthValidationConfig):
    rect_left = cv2.remap(left, maps[0][0], maps[0][1], cv2.INTER_LINEAR)
    rect_right = cv2.remap(right, maps[1][0], maps[1][1], cv2.INTER_LINEAR)
    gray_left = cv2.cvtColor(rect_left, cv2.COLOR_BGR2GRAY)
    gray_right = cv2.cvtColor(rect_right, cv2.COLOR_BGR2GRAY)
    raw = matcher.compute(gray_left, gray_right)
    if raw.dtype != np.int16:
        raise RuntimeError(f"Unexpected StereoSGBM output dtype: {raw.dtype}")
    disparity = raw.astype(np.float32) / 16.0
    valid_disparity = np.isfinite(disparity) & (disparity > cfg.min_disparity)
    reprojection_input = disparity.copy()
    reprojection_input[~valid_disparity] = np.nan
    points = cv2.reprojectImageTo3D(reprojection_input, q, handleMissingValues=False)
    z = points[:, :, 2]
    valid_depth = valid_disparity & np.isfinite(z) & (z >= cfg.min_depth_mm) & (z <= cfg.max_depth_mm)
    visual = np.clip((disparity - cfg.min_disparity) * (255.0 / cfg.num_disparities), 0, 255).astype(np.uint8)
    visual = cv2.applyColorMap(visual, cv2.COLORMAP_TURBO)
    visual[~valid_depth] = 0
    return rect_left, rect_right, disparity, points, valid_depth, visual


def sample_depth(x: int, y: int, disparity: np.ndarray, points: np.ndarray, valid: np.ndarray, cfg: DepthValidationConfig) -> Dict[str, Any]:
    if x < 0 or y < 0 or x >= disparity.shape[1] or y >= disparity.shape[0]:
        return {"valid": False, "reason": "click outside image", "x": x, "y": y, "valid_samples": 0}
    radius = cfg.click_window // 2
    x0, x1 = max(0, x - radius), min(disparity.shape[1], x + radius + 1)
    y0, y1 = max(0, y - radius), min(disparity.shape[0], y + radius + 1)
    mask = valid[y0:y1, x0:x1]
    count = int(np.count_nonzero(mask))
    if count < cfg.min_valid_click_samples:
        return {"valid": False, "reason": "too few valid samples", "x": x, "y": y, "valid_samples": count}
    disparities = disparity[y0:y1, x0:x1][mask]
    depths = points[y0:y1, x0:x1, 2][mask]
    return {"valid": True, "x": x, "y": y, "valid_samples": count, "median_disparity": float(np.median(disparities)), "depth_mm": float(np.median(depths))}


def append_csv(path: Path, measurement: Dict[str, Any], ground_truth_mm: Optional[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    measured = float(measurement["depth_mm"])
    absolute = "" if ground_truth_mm is None else abs(measured - ground_truth_mm)
    relative = "" if ground_truth_mm in (None, 0) else 100.0 * float(absolute) / ground_truth_mm
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "ground_truth_mm": "" if ground_truth_mm is None else ground_truth_mm, "measured_depth_mm": measured, "absolute_error_mm": absolute, "relative_error_percent": relative, "clicked_x": measurement["x"], "clicked_y": measurement["y"], "median_disparity": measurement["median_disparity"], "valid_samples": measurement["valid_samples"]}
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def open_camera(cfg: DepthValidationConfig):
    cap = cv2.VideoCapture(cfg.camera_device, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.raw_width); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.raw_height)
    cap.set(cv2.CAP_PROP_FPS, cfg.requested_fps); cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        cap.release(); raise RuntimeError(f"Could not open {cfg.camera_device} with V4L2")
    return cap


class App:
    def __init__(self, cfg: DepthValidationConfig):
        cfg.validate(); self.cfg = cfg; self.cal = load_calibration(cfg.calibration_path)
        if (int(self.cal["image_width"]), int(self.cal["image_height"])) != (cfg.eye_width, cfg.eye_height):
            raise ValueError("Calibration image size does not match configured per-eye size")
        self.maps = build_rectify_maps(self.cal, (cfg.eye_width, cfg.eye_height)); self.matcher = build_matcher(cfg)
        self.disparity = self.points = self.valid = None; self.last_measurement = None; self.ground_truth_mm = None
        self.rect_left = self.rect_right = self.disparity_visual = None; self.csv_path = cfg.output_root / "depth_validation.csv"

    def _mouse(self, event, x, y, flags, userdata):
        if event != cv2.EVENT_LBUTTONDOWN or self.disparity is None: return
        fx, fy = int(round(x / self.cfg.display_scale)), int(round(y / self.cfg.display_scale))
        self.last_measurement = sample_depth(fx, fy, self.disparity, self.points, self.valid, self.cfg)

    def _annotate(self, image: np.ndarray, fps: float, label: str) -> np.ndarray:
        out=image.copy(); green=(0,255,0); white=(255,255,255); red=(0,0,255)
        cv2.putText(out,f"{label} | RUN B | {fps:.1f} FPS | baseline {float(self.cal['baseline_mm']):.2f} mm",(15,30),cv2.FONT_HERSHEY_SIMPLEX,.65,white,2,cv2.LINE_AA)
        if self.last_measurement:
            m=self.last_measurement
            if m.get("valid"):
                text=f"({m['x']},{m['y']}) d={m['median_disparity']:.2f}px Z={m['depth_mm']:.1f}mm / {m['depth_mm']/1000:.3f}m n={m['valid_samples']}"; color=green
                cv2.drawMarker(out,(m['x'],m['y']),green,cv2.MARKER_CROSS,20,2)
            else: text=f"({m['x']},{m['y']}) INVALID DEPTH n={m['valid_samples']}"; color=red
            cv2.putText(out,text,(15,60),cv2.FONT_HERSHEY_SIMPLEX,.58,color,2,cv2.LINE_AA)
        gt="unset" if self.ground_truth_mm is None else f"{self.ground_truth_mm:.1f} mm"
        cv2.putText(out,f"GT: {gt} | [R] record with GT [L] log measured [S] snapshot [Q/ESC] quit",(15,out.shape[0]-16),cv2.FONT_HERSHEY_SIMPLEX,.52,white,1,cv2.LINE_AA)
        return out

    def _save_snapshot(self):
        if self.rect_left is None: return
        folder=self.cfg.output_root/"snapshots"; folder.mkdir(parents=True,exist_ok=True); stamp=utc_stamp()
        cv2.imwrite(str(folder/f"{stamp}_rectified_left.png"),self.rect_left); cv2.imwrite(str(folder/f"{stamp}_rectified_right.png"),self.rect_right); cv2.imwrite(str(folder/f"{stamp}_disparity.png"),self.disparity_visual)
        (folder/f"{stamp}_measurement.json").write_text(json.dumps({"measurement":self.last_measurement,"ground_truth_mm":self.ground_truth_mm,"calibration":str(self.cfg.calibration_path)},indent=2)+"\n",encoding="utf-8")
        print("SNAPSHOT SAVED:",folder,stamp)

    def run(self, smoke_test: bool = False) -> int:
        cap=open_camera(self.cfg); frame_count=0; start=time.monotonic(); window="Disparity / Depth"
        try:
            if not smoke_test:
                cv2.namedWindow(window,cv2.WINDOW_NORMAL); cv2.setMouseCallback(window,self._mouse)
            while True:
                ok,frame=cap.read()
                if not ok: raise RuntimeError("Camera read failed")
                if frame.shape[:2] != (self.cfg.raw_height,self.cfg.raw_width): raise RuntimeError(f"Unexpected frame shape: {frame.shape}")
                left=frame[:,:self.cfg.eye_width]; right=frame[:,self.cfg.eye_width:]
                self.rect_left,self.rect_right,self.disparity,self.points,self.valid,self.disparity_visual=process_pair(left,right,self.maps,self.matcher,self.cal["Q"],self.cfg)
                frame_count+=1
                if smoke_test:
                    print(f"SMOKE PASS frame={frame.shape} left={left.shape} right={right.shape} disparity={self.disparity.dtype} valid={int(self.valid.sum())}")
                    return 0
                fps=frame_count/max(time.monotonic()-start,1e-6)
                l=self._annotate(self.rect_left,fps,"RECTIFIED LEFT"); r=self._annotate(self.rect_right,fps,"RECTIFIED RIGHT"); d=self._annotate(self.disparity_visual,fps,"DISPARITY / DEPTH")
                for image in (l,r):
                    for y in range(0,image.shape[0],self.cfg.epiline_step): cv2.line(image,(0,y),(image.shape[1]-1,y),(0,255,0),1)
                scale=self.cfg.display_scale
                cv2.imshow("Rectified LEFT",cv2.resize(l,None,fx=scale,fy=scale)); cv2.imshow("Rectified RIGHT",cv2.resize(r,None,fx=scale,fy=scale)); cv2.imshow(window,cv2.resize(d,None,fx=scale,fy=scale))
                key=cv2.waitKey(1)&0xFF
                if key in (ord('q'),27): return 0
                if key==ord('s'): self._save_snapshot()
                if key==ord('l') and self.last_measurement and self.last_measurement.get("valid"):
                    append_csv(self.csv_path,self.last_measurement,None); print("MEASUREMENT LOGGED:",self.csv_path)
                if key==ord('r'):
                    try:
                        raw=input("Ground truth distance in mm (e.g. 500; blank cancels): ").strip()
                        if raw:
                            value=float(raw)
                            if not math.isfinite(value) or value<=0: raise ValueError
                            self.ground_truth_mm=value
                            if self.last_measurement and self.last_measurement.get("valid"):
                                append_csv(self.csv_path,self.last_measurement,value); print("GROUND-TRUTH MEASUREMENT LOGGED:",self.csv_path)
                    except ValueError: print("INVALID GROUND TRUTH: enter a positive number in mm")
        finally:
            cap.release(); cv2.destroyAllWindows()


def main() -> int:
    parser=argparse.ArgumentParser(description="Stage 2 Run B real-time stereo depth validation")
    parser.add_argument("--calibration",type=Path,default=DepthValidationConfig.calibration_path)
    parser.add_argument("--device",default="/dev/video0"); parser.add_argument("--smoke-test",action="store_true")
    args=parser.parse_args(); cfg=replace(DepthValidationConfig(),calibration_path=args.calibration.expanduser(),camera_device=args.device)
    try:
        app=App(cfg); print("Calibration:",cfg.calibration_path); print("Excluded pairs:",app.cal.get("excluded_pair_indices","unknown")); return app.run(args.smoke_test)
    except Exception as exc:
        print(f"DEPTH VALIDATION FAILED: {type(exc).__name__}: {exc}"); return 2


if __name__ == "__main__": raise SystemExit(main())
