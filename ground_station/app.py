"""Windows read-only video and metadata console. No ROS or PX4 imports."""
from __future__ import annotations

import argparse
import json
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QVBoxLayout, QWidget)

from .config import load_config
from .display_policy import display_state
from .metadata_receiver import MetadataReceiver
from .overlay import VideoCanvas
from .video_receiver import VideoReceiver


def text(value, suffix=""):
    return "—" if value is None else f"{value}{suffix}"


class Console(QMainWindow):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.video = VideoReceiver(config["ffmpeg"], config["video_sdp"],
                                   config["width"], config["height"])
        self.metadata = MetadataReceiver(config["metadata_host"], config["metadata_port"])
        self.started_at = time.monotonic()
        self.last_report_at = self.started_at
        self.last_displayed = self.last_painted = self.last_packets = self.last_decoded = 0
        config["metrics_jsonl"].parent.mkdir(parents=True, exist_ok=True)
        self.metrics_file = config["metrics_jsonl"].open("w", encoding="utf-8")
        self.setWindowTitle("灵眸控飞  |  Vision Flight Console")
        self.resize(1510, 960)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("灵眸控飞  <span style='color:#80a0b8'>/ Vision Flight Console</span>")
        title.setFont(QFont("Microsoft YaHei UI", 19, QFont.Weight.DemiBold))
        mode = QLabel("●  MONITOR MODE")
        mode.setStyleSheet("color:#5ee0b6;font-weight:700;letter-spacing:1px;")
        header.addWidget(title); header.addStretch(); header.addWidget(mode)
        outer.addLayout(header)
        content = QHBoxLayout()
        content.setSpacing(14)
        self.canvas = VideoCanvas(config)
        content.addWidget(self.canvas, 1)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(305)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(18, 18, 18, 18)
        side_layout.setSpacing(10)
        self.fields = {}
        for section, names in (
            ("SYSTEM STATUS", ("Camera", "Video", "AI", "Metadata", "PX4", "Safety")),
            ("OPERATOR", ("State", "Track", "Depth")),
            ("GESTURE / INTENT", ("Gesture", "Intent", "Reason", "Lease")),
            ("LINK METRICS", ("Video FPS", "AI FPS", "Metadata Hz", "Metadata age", "Depth age", "Sequence gaps")),
        ):
            heading = QLabel(section)
            heading.setObjectName("section")
            side_layout.addWidget(heading)
            for name in names:
                row = QHBoxLayout()
                label = QLabel(name)
                label.setObjectName("fieldname")
                value = QLabel("—")
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                value.setWordWrap(True)
                row.addWidget(label); row.addWidget(value, 1)
                side_layout.addLayout(row)
                self.fields[name] = value
        side_layout.addStretch()
        content.addWidget(side)
        outer.addLayout(content, 1)
        footer = QHBoxLayout()
        for name in ("ARM", "TAKEOFF 1.2m", "LAND", "RETURN"):
            button = QPushButton(name)
            button.setEnabled(False)
            button.setToolTip("Ground Station V1 监视模式，尚未启用飞行控制")
            footer.addWidget(button)
        footer.addStretch()
        read_only = QLabel("READ-ONLY  /  NO PX4 LIVE OUTPUT")
        read_only.setObjectName("readOnly")
        footer.addWidget(read_only)
        outer.addLayout(footer)
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#10151c; color:#e9eef5; font-family:'Microsoft YaHei UI'; }
            #sidebar { background:#1b2530; border:1px solid #344351; border-radius:8px; }
            #section { color:#82a9bc; font-size:11px; font-weight:700; margin-top:10px; }
            #fieldname { color:#9cacbb; font-size:12px; }
            #readOnly { color:#f6c66a; font-weight:700; letter-spacing:1px; }
            QPushButton { background:#293440; color:#73818e; border:1px solid #45515d;
                          border-radius:5px; padding:10px 14px; font-weight:700; }
        """)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(16)

    def _set(self, name, value):
        self.fields[name].setText(str(value))

    def refresh(self):
        frame, sequence, video_age, video_metrics = self.video.snapshot()
        packet, metadata_age, metadata_metrics = self.metadata.snapshot()
        self.canvas.set_state(frame, sequence, video_age, packet, metadata_age)
        state = display_state(packet, metadata_age,
            metadata_stale_ms=self.config["metadata_stale_ms"],
            overlay_timeout_ms=self.config["overlay_timeout_ms"])
        video_ready = video_age is not None and video_age <= self.config["video_stale_ms"]
        self._set("Camera", "READY" if video_ready else "NO VIDEO")
        self._set("Video", "READY" if video_ready else "VIDEO LOST")
        self._set("AI", "READY" if state["ai_ready"] else "STALE")
        self._set("Metadata", "READY" if state["metadata_ready"] else "METADATA LOST")
        self._set("PX4", "N/A · dry-run")
        stage6 = (packet or {}).get("stage6") or {}
        self._set("Safety", "VALID" if state["metadata_ready"] and stage6.get("valid")
                  else "CONTROL INHIBITED")
        self._set("State", state["operator_state"] or "WAIT_OPERATOR")
        op = (packet or {}).get("operator") or {}
        self._set("Track", "#"+str(op["track_id"]) if state["draw_person"] and
                  op.get("track_id") is not None else "—")
        depth = (packet or {}).get("depth") or {}
        self._set("Depth", f"{depth['m']:.2f} m" if state["draw_person"] and
                  depth.get("valid") and isinstance(depth.get("m"), (float, int)) else "—")
        self._set("Gesture", state["gesture"])
        self._set("Intent", state["intent"])
        self._set("Reason", state["safety_reason"])
        self._set("Lease", stage6.get("lease") if state["metadata_ready"] else "STALE")
        elapsed = max(.001, time.monotonic()-self.started_at)
        self._set("Video FPS", f"{self.canvas.displayed_frames/elapsed:.1f}")
        system = (packet or {}).get("system") or {}
        self._set("AI FPS", text(system.get("ai_fps")) if state["metadata_ready"] else "—")
        self._set("Metadata Hz", f"{metadata_metrics['hz']:.1f}")
        self._set("Metadata age", f"{metadata_age:.0f} ms" if metadata_age is not None else "—")
        self._set("Depth age", f"{depth['age_ms']:.0f} ms" if state["draw_person"] and
                  isinstance(depth.get("age_ms"), (float, int)) else "—")
        self._set("Sequence gaps", metadata_metrics["gaps"])
        now = time.monotonic()
        if now-self.last_report_at >= 5:
            duration = now-self.last_report_at
            report = {"event":"GS_WINDOW", "elapsed_s":round(now-self.started_at,2),
                "video_decode_fps":round(video_metrics["fps"],2),
                "video_decode_interval_fps":round((video_metrics["frames"]-self.last_decoded)/duration,2),
                "video_display_fps":round((self.canvas.displayed_frames-self.last_displayed)/duration,2),
                "ui_fps":round((self.canvas.paint_count-self.last_painted)/duration,2),
                "metadata_hz":round((metadata_metrics["received"]-self.last_packets)/duration,2),
                "metadata_age_ms":round(metadata_age,1) if metadata_age is not None else None,
                "video_age_ms":round(video_age,1) if video_age is not None else None,
                "metadata_gaps":metadata_metrics["gaps"],
                "video_restarts":video_metrics["restarts"],
                "operator_state":state["operator_state"]}
            line = json.dumps(report, separators=(",", ":"))
            print(line, flush=True)
            self.metrics_file.write(line+"\n")
            self.metrics_file.flush()
            self.last_report_at = now
            self.last_displayed = self.canvas.displayed_frames
            self.last_painted = self.canvas.paint_count
            self.last_packets = metadata_metrics["received"]
            self.last_decoded = video_metrics["frames"]

    def closeEvent(self, event):
        self.timer.stop()
        self.metadata.close()
        self.video.close()
        self.metrics_file.close()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Ground-station JSON configuration")
    args = parser.parse_args()
    config = load_config(args.config)
    if not config["video_sdp"].is_file() or not config["ffmpeg"].is_file():
        parser.error("video SDP or FFmpeg executable is missing; check ground_station/config_v1.json")
    app = QApplication(sys.argv[:1])
    # Some portable Python/Qt combinations expose no system font families.
    for font in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/segoeui.ttf"):
        QFontDatabase.addApplicationFont(font)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = Console(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
