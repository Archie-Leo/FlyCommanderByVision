"""Low-delay FFmpeg H.264 decoder draining into a one-frame slot."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time


class VideoReceiver:
    def __init__(self, ffmpeg, sdp, width=1280, height=960):
        self.ffmpeg, self.sdp = str(ffmpeg), str(sdp)
        self.width, self.height = width, height
        self.frame_bytes = width * height * 4
        self.lock = threading.Lock()
        self.latest = None
        self.received_at = None
        self.sequence = self.frames = self.restarts = 0
        self.started_at = time.monotonic()
        self.stop_event = threading.Event()
        self.process = None
        self.thread = threading.Thread(target=self._run, name="h264-decoder", daemon=True)
        self.thread.start()

    def _command(self):
        return [self.ffmpeg, "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "file,udp,rtp", "-fflags", "nobuffer",
            "-flags", "low_delay", "-analyzeduration", "500000", "-probesize", "1000000",
            "-max_delay", "0", "-i", self.sdp, "-an", "-f", "rawvideo",
            "-pix_fmt", "bgra", "-y", "pipe:1"]

    def _run(self):
        while not self.stop_event.is_set():
            try:
                with tempfile.TemporaryFile() as error_log:
                    proc = subprocess.Popen(self._command(), stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=error_log,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), bufsize=0)
                    self.process = proc
                    frame = bytearray(self.frame_bytes)
                    while not self.stop_event.is_set():
                        view = memoryview(frame)
                        received = 0
                        while received < self.frame_bytes and not self.stop_event.is_set():
                            count = proc.stdout.readinto(view[received:])
                            if not count:
                                break
                            received += count
                        if received != self.frame_bytes:
                            break
                        now = time.monotonic()
                        with self.lock:
                            self.latest = bytes(frame)
                            self.received_at = now
                            self.sequence += 1
                            self.frames += 1
                    if not self.stop_event.is_set() and self.restarts < 3:
                        error_log.seek(0)
                        print("FFmpeg receiver restart:", error_log.read(1000).decode(
                            "utf-8", errors="replace"), file=sys.stderr, flush=True)
            except OSError:
                pass
            finally:
                if self.process is not None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                    self.process = None
            if not self.stop_event.is_set():
                self.restarts += 1
                self.stop_event.wait(.3)

    def snapshot(self):
        with self.lock:
            age_ms = ((time.monotonic()-self.received_at)*1000
                      if self.received_at is not None else None)
            return self.latest, self.sequence, age_ms, {
                "frames": self.frames,
                "fps": self.frames/max(.001, time.monotonic()-self.started_at),
                "restarts": self.restarts}

    def close(self):
        self.stop_event.set()
        if self.process is not None:
            self.process.terminate()
        self.thread.join(timeout=2)
