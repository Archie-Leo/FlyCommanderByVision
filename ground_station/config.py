"""One place for Windows viewer paths, ports, and freshness limits."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "video_sdp": "ground_station/video_link.sdp",
    "ffmpeg": "D:/K230IDE/share/qtcreator/ffmpeg/windows/bin/ffmpeg.exe",
    "metadata_host": "0.0.0.0", "metadata_port": 5603,
    "command_host": "192.168.1.100", "command_port": 5604,
    "command_key": "runs/ground_command.key",
    "metadata_stale_ms": 500, "overlay_timeout_ms": 1000,
    "video_stale_ms": 1000, "width": 1280, "height": 960,
    "metrics_jsonl": "runs/ground_station_v1.jsonl",
}


def load_config(path=None):
    result = dict(DEFAULTS)
    target = Path(path) if path else ROOT / "ground_station" / "config_v1.json"
    if target.is_file():
        result.update(json.loads(target.read_text(encoding="utf-8")))
    sdp = Path(result["video_sdp"])
    result["video_sdp"] = sdp if sdp.is_absolute() else ROOT / sdp
    result["ffmpeg"] = Path(result["ffmpeg"])
    metrics = Path(result["metrics_jsonl"])
    result["metrics_jsonl"] = metrics if metrics.is_absolute() else ROOT / metrics
    key = Path(result["command_key"])
    result["command_key"] = key if key.is_absolute() else ROOT / key
    return result
