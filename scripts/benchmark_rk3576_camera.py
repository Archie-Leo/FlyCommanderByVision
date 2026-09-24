"""Read-only FFmpeg camera throughput check; does not open ROS or PX4."""
from __future__ import annotations

import argparse
import json
import resource
import time

from camera.ffmpeg_source import FFmpegCameraConfig, FFmpegStereoSource


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/video73")
    parser.add_argument("--fps", type=float, default=60)
    parser.add_argument("--stereo-width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument("--eye", choices=("stereo", "left", "right"), default="left")
    parser.add_argument("--output-width", type=int, default=640)
    parser.add_argument("--output-height", type=int, default=480)
    parser.add_argument("--pixel-format", choices=("bgr24", "rgb24"), default="bgr24")
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--consumer-sleep", type=float, default=0,
                        help="Pause after each read to verify old-frame dropping")
    args = parser.parse_args()
    config = FFmpegCameraConfig(
        device=args.device, fps=args.fps, stereo_width=args.stereo_width,
        height=args.height, eye=args.eye, output_width=args.output_width,
        output_height=args.output_height, pixel_format=args.pixel_format,
    )
    cpu_start = resource.getrusage(resource.RUSAGE_SELF)
    started = time.monotonic()
    received = 0
    failures = 0
    first_id = last_id = None
    first_at = last_at = None
    with FFmpegStereoSource(config) as source:
        for _ in range(args.frames):
            try:
                frame = source.read(timeout=5)
            except (RuntimeError, TimeoutError):
                failures += 1
                break
            received += 1
            read_at = time.monotonic()
            first_at = read_at if first_at is None else first_at
            last_at = read_at
            first_id = frame.frame_id if first_id is None else first_id
            last_id = frame.frame_id
            if args.consumer_sleep:
                time.sleep(args.consumer_sleep)
        dropped = source.dropped_frames
        loop_ended = time.monotonic()
    elapsed = time.monotonic() - started
    cpu_end = resource.getrusage(resource.RUSAGE_SELF)
    cpu_seconds = (cpu_end.ru_utime + cpu_end.ru_stime
                   - cpu_start.ru_utime - cpu_start.ru_stime)
    print(json.dumps({
        "received_frames": received,
        "requested_frames": args.frames,
        "first_frame_id": first_id,
        "last_frame_id": last_id,
        "dropped_frames": dropped,
        "failures": failures,
        "elapsed_seconds": round(elapsed, 3),
        "effective_fps": round(received / elapsed, 2) if elapsed else 0,
        "startup_seconds": round(first_at - started, 3) if first_at else None,
        "read_window_seconds": round(last_at - first_at, 3) if first_at and last_at else None,
        "steady_fps": round((received - 1) / (last_at - first_at), 2)
                      if received > 1 and last_at > first_at else 0,
        "shutdown_seconds": round(time.monotonic() - loop_ended, 3),
        "capture_thread_alive_after_close": source._thread.is_alive(),
        "python_process_cpu_seconds": round(cpu_seconds, 3),
        "eye": args.eye,
        "output_size": [args.output_width, args.output_height],
        "pixel_format": args.pixel_format,
    }, indent=2))
    if failures or received != args.frames:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
