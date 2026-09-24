"""V4L2 MJPEG camera source with a single overwriteable latest frame."""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass

import numpy as np

from config import CameraConfig
from .stereo_left_source import CameraFrame


@dataclass(frozen=True)
class FFmpegCameraConfig:
    device: str = "/dev/video73"
    fps: float = 60.0
    stereo_width: int = 2560
    height: int = 960
    eye: str = "stereo"  # stereo, left, right; camera layout is LEFT|RIGHT
    output_width: int = 2560
    output_height: int = 960
    pixel_format: str = "bgr24"  # rgb24 is available for a future RGB consumer
    input_format: str = "mjpeg"
    ffmpeg_binary: str = "ffmpeg"

    def __post_init__(self):
        if self.eye not in {"stereo", "left", "right"}:
            raise ValueError("eye must be stereo, left or right")
        if self.pixel_format not in {"bgr24", "rgb24"}:
            raise ValueError("pixel_format must be bgr24 or rgb24")
        if self.stereo_width <= 0 or self.stereo_width % 2 or self.height <= 0:
            raise ValueError("stereo dimensions must be positive with even width")
        if self.output_width <= 0 or self.output_height <= 0 or self.fps <= 0:
            raise ValueError("output dimensions and fps must be positive")
        if self.eye == "stereo" and self.output_width % 2:
            raise ValueError("stereo output width must be even")


class FFmpegStereoSource:
    """Reads complete decoded frames on a producer thread, dropping older frames."""

    def __init__(self, config: FFmpegCameraConfig = FFmpegCameraConfig()):
        self.config = config
        self._condition = threading.Condition()
        self._latest = None
        self._latest_timestamp_ns = 0
        self._captured = 0
        self._delivered = 0
        self._dropped = 0
        self._last_delivered_id = -1
        self._error = None
        self._closed = False
        self._frame_bytes = config.output_width * config.output_height * 3
        cmd = self.command(config)
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
        )
        self._thread = threading.Thread(target=self._capture, name="fcv-ffmpeg-camera", daemon=True)
        self._thread.start()

    @classmethod
    def from_camera_config(cls, config: CameraConfig):
        return cls(FFmpegCameraConfig(
            device=config.device, fps=config.requested_fps,
            stereo_width=config.raw_width, height=config.raw_height,
            output_width=config.raw_width, output_height=config.raw_height,
        ))

    @staticmethod
    def command(config: FFmpegCameraConfig) -> list[str]:
        cmd = [config.ffmpeg_binary, "-hide_banner", "-loglevel", "error",
               "-f", "v4l2", "-input_format", config.input_format,
               "-video_size", f"{config.stereo_width}x{config.height}",
               "-framerate", str(config.fps), "-i", config.device,
               "-an"]
        filters = []
        if config.eye != "stereo":
            x = 0 if config.eye == "left" else config.stereo_width // 2
            filters.append(f"crop={config.stereo_width // 2}:{config.height}:{x}:0")
        source_width = config.stereo_width if config.eye == "stereo" else config.stereo_width // 2
        if (config.output_width, config.output_height) != (source_width, config.height):
            filters.append(f"scale={config.output_width}:{config.output_height}")
        if filters:
            cmd += ["-vf", ",".join(filters)]
        return cmd + ["-pix_fmt", config.pixel_format, "-f", "rawvideo", "pipe:1"]

    def _capture(self):
        try:
            assert self._process.stdout is not None
            while True:
                data = bytearray(self._frame_bytes)
                view = memoryview(data)
                offset = 0
                while offset < self._frame_bytes:
                    count = self._process.stdout.readinto(view[offset:])
                    if not count:
                        raise EOFError(f"FFmpeg camera ended (exit={self._process.poll()})")
                    offset += count
                timestamp_ns = time.monotonic_ns()  # host receipt, not sensor exposure
                frame = np.frombuffer(data, dtype=np.uint8).reshape(
                    self.config.output_height, self.config.output_width, 3
                )
                with self._condition:
                    if self._closed:
                        break
                    self._latest = frame
                    self._latest_timestamp_ns = timestamp_ns
                    self._captured += 1
                    self._condition.notify_all()
        except (OSError, EOFError, ValueError) as exc:
            with self._condition:
                if not self._closed:
                    self._error = exc
                    self._condition.notify_all()

    @property
    def dropped_frames(self) -> int:
        with self._condition:
            return self._dropped

    def read(self, timeout: float = 5.0) -> CameraFrame:
        started = time.perf_counter_ns()
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._captured - 1 <= self._last_delivered_id and not self._error and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("No fresh FFmpeg camera frame")
                self._condition.wait(remaining)
            if self._closed:
                raise RuntimeError("FFmpeg camera source is closed")
            if self._captured - 1 <= self._last_delivered_id:
                raise RuntimeError("FFmpeg camera stopped") from self._error
            frame_id = self._captured - 1
            raw = self._latest
            timestamp_ns = self._latest_timestamp_ns
            self._dropped += frame_id - self._last_delivered_id - 1
            self._last_delivered_id = frame_id
            self._delivered += 1
        if self.config.eye == "stereo":
            left = raw[:, :raw.shape[1] // 2].copy()
            right = raw[:, raw.shape[1] // 2:].copy()
            sbs = raw
        elif self.config.eye == "left":
            left, right, sbs = raw, None, None
        else:
            left, right, sbs = None, raw, None
        return CameraFrame(frame_id, timestamp_ns, sbs, left,
                           (time.perf_counter_ns() - started) / 1e6,
                           right_raw=right, pixel_format=self.config.pixel_format)

    def release(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        # Close the read end first. FFmpeg may be blocked writing the next
        # frame; waiting for it before closing the pipe costs the full timeout.
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._process.poll() is None:
            self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        self._thread.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()
