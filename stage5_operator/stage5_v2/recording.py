"""Opt-in, per-clip visual recording with exact timing and optional raw SBS PNGs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DiagnosticRecorder:
    def __init__(self, run_dir: Path, *, raw_stereo: bool = False,
                 playback_fps: float = 10.0):
        if not 1.0 <= playback_fps <= 120.0:
            raise ValueError("playback_fps must be in [1,120]")
        self.run_dir = Path(run_dir)
        self.raw_stereo = raw_stereo
        self.playback_fps = playback_fps
        self.clip_dir: Path | None = None
        self.writer = None
        self.frame_log = None
        self.clip_number = 0
        self.frame_count = 0
        self.clips: list[dict] = []
        self.manifest: dict | None = None

    @property
    def active(self) -> bool:
        return self.writer is not None

    def start(self, annotated: np.ndarray, metadata: dict) -> Path:
        if self.active:
            raise RuntimeError("recording is already active")
        if annotated.dtype != np.uint8 or annotated.ndim != 3 or annotated.shape[2] != 3:
            raise ValueError("annotated frame must be BGR uint8 HxWx3")
        height, width = annotated.shape[:2]
        self.clip_number += 1
        clip = self.run_dir / "recordings" / f"clip_{self.clip_number:03d}"
        clip.mkdir(parents=True, exist_ok=False)
        video_path = clip / "annotated.avi"
        writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"),
                                 self.playback_fps, (width, height))
        if not writer.isOpened():
            writer.release()
            raise RuntimeError(f"MJPG VideoWriter unavailable: {video_path}")
        if self.raw_stereo:
            (clip / "raw_sbs_png").mkdir()
        self.clip_dir = clip
        self.writer = writer
        self.frame_log = (clip / "frames.jsonl").open("w", encoding="utf-8")
        self.frame_count = 0
        self.manifest = {
            "schema_version": "Stage5DiagnosticRecordingV1", "status": "RECORDING",
            "started_utc": utc_now(), "ended_utc": None,
            "video_file": "annotated.avi", "frames_file": "frames.jsonl",
            "annotated_size": [width, height], "video_codec": "MJPG",
            "video_playback_fps": self.playback_fps,
            "timing_note": "AVI is constant-FPS playback; use per-frame capture_monotonic_ns for actual timing",
            "raw_stereo_png": self.raw_stereo,
            "raw_stereo_note": "PNG preserves decoded SBS pixels; it does not recover original USB MJPEG bitstream",
            "frames_written": 0, **metadata,
        }
        (clip / "manifest.json").write_text(json.dumps(self.manifest, indent=2, allow_nan=False) + "\n",
                                            encoding="utf-8")
        return clip

    def write(self, annotated: np.ndarray, raw_sbs: np.ndarray,
              record: dict, capture_monotonic_ns: int) -> None:
        if not self.active or self.clip_dir is None or self.manifest is None:
            raise RuntimeError("recording is not active")
        width, height = self.manifest["annotated_size"]
        if annotated.dtype != np.uint8 or annotated.shape != (height, width, 3):
            raise ValueError("annotated frame shape changed during recording")
        if not isinstance(capture_monotonic_ns, int) or capture_monotonic_ns <= 0:
            raise ValueError("invalid capture monotonic timestamp")
        raw_path = f"raw_sbs_png/frame_{self.frame_count:06d}.png" if self.raw_stereo else None
        entry = {
            "recording_frame_index": self.frame_count,
            "capture_monotonic_ns": capture_monotonic_ns,
            "recorded_utc": utc_now(),
            "raw_sbs_png": raw_path,
            "diagnostics": record,
        }
        encoded = json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n"
        if self.raw_stereo:
            if raw_sbs.dtype != np.uint8 or raw_sbs.ndim != 3 or raw_sbs.shape[2] != 3:
                raise ValueError("raw SBS must be BGR uint8 HxWx3")
            if not cv2.imwrite(str(self.clip_dir / raw_path), raw_sbs):
                raise OSError(f"Could not write raw SBS PNG: {raw_path}")
        self.writer.write(annotated)
        self.frame_log.write(encoded)
        self.frame_log.flush()
        self.frame_count += 1

    def stop(self, *, reason: str = "USER_STOP") -> dict | None:
        if not self.active:
            return None
        self.writer.release()
        self.writer = None
        self.frame_log.close()
        self.frame_log = None
        assert self.clip_dir is not None and self.manifest is not None
        video_path = self.clip_dir / "annotated.avi"
        status = "COMPLETE" if self.frame_count and video_path.is_file() and video_path.stat().st_size > 0 else "EMPTY_OR_VIDEO_ERROR"
        self.manifest.update({"status": status, "ended_utc": utc_now(),
                              "frames_written": self.frame_count, "stop_reason": reason,
                              "video_bytes": video_path.stat().st_size if video_path.exists() else 0})
        (self.clip_dir / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        summary = {"clip_dir": str(self.clip_dir), "status": status,
                   "frames": self.frame_count, "reason": reason}
        self.clips.append(summary)
        self.clip_dir = None
        self.manifest = None
        return summary
