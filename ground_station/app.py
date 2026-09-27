"""Windows competition console. Read-only video and metadata; no PX4 publisher."""
from __future__ import annotations

import argparse
import json
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
    QVBoxLayout, QWidget)

from .config import load_config
from .evidence import competition_state
from .metadata_receiver import MetadataReceiver
from .overlay import VideoCanvas
from .video_receiver import VideoReceiver


COLORS = {"normal": "#68b7bb", "authorized": "#68d2a2",
          "candidate": "#dfb76b", "warning": "#e3ad69", "muted": "#8394a2"}


def shown(value, suffix=""):
    return "--" if value is None else f"{value}{suffix}"


def label(text, object_name=None, *, wrap=False):
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(wrap)
    return widget


def set_text(widget, value):
    value = str(value)
    if widget.text() != value:
        widget.setText(value)


class EvidenceNode(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("evidenceNode")
        self.setMinimumHeight(90)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 9, 10, 9)
        layout.setSpacing(3)
        layout.addWidget(label(title, "nodeTitle"))
        self.value = label("--", "nodeValue", wrap=True)
        layout.addWidget(self.value)
        self.detail = label("", "nodeDetail", wrap=True)
        layout.addWidget(self.detail)
        self.previous = None
        self.set_state("--", "", "muted")

    def set_state(self, value, detail, tone):
        state = (value, detail, tone)
        if state == self.previous:
            return
        self.previous = state
        set_text(self.value, value)
        set_text(self.detail, detail)
        color = COLORS.get(tone, COLORS["muted"])
        self.setStyleSheet("QFrame#evidenceNode {background:#17232d;"
                           f"border:1px solid #30414c;border-left:4px solid {color};"
                           "border-radius:7px;}")
        self.value.setStyleSheet(f"color:{color};font-weight:700;")


class StatusCard(QFrame):
    def __init__(self, title, rows):
        super().__init__()
        self.setObjectName("statusCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 11, 16, 11)
        layout.setSpacing(4)
        layout.addWidget(label(title, "cardTitle"))
        self.primary = label("--", "cardPrimary", wrap=True)
        layout.addWidget(self.primary)
        self.detail = label("", "cardDetail", wrap=True)
        layout.addWidget(self.detail)
        self.rows = {}
        for name in rows:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(label(name, "rowName"))
            value = label("--", "rowValue", wrap=True)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addStretch(1)
            row.addWidget(value, 2)
            layout.addLayout(row)
            self.rows[name] = value
        layout.addStretch(1)
        self.tone = None
        self.set_status("--", "", "muted")

    def set_status(self, primary, detail, tone):
        set_text(self.primary, primary)
        set_text(self.detail, detail)
        if tone != self.tone:
            self.tone = tone
            color = COLORS.get(tone, COLORS["muted"])
            self.setStyleSheet("QFrame#statusCard {background:#17232d;"
                               f"border:1px solid #30414c;border-left:4px solid {color};"
                               "border-radius:7px;}")
            self.primary.setStyleSheet(f"color:{color};font-weight:700;")

    def put(self, name, value):
        set_text(self.rows[name], value)


class EngineeringDialog(QDialog):
    FIELDS = (
        "Board video FPS", "AI FPS", "Metadata Hz", "Metadata age",
        "AI age at send", "Depth age at send", "Decode FPS", "Display FPS",
        "Video age", "Sequence gaps", "Invalid packets", "FFmpeg restarts",
        "People count", "Track ID", "Session ID", "Pose score",
        "Raw gesture", "Stable gesture", "Stage6 valid", "Stage6 intent",
        "Stage6 reason", "Stage6 lease", "PX4 status age", "PX4 mode raw",
        "PX4 armed", "PX4 failsafe", "Local position valid",
        "Local position NED", "Local velocity NED", "Last VehicleCommand ACK",
        "Command mode", "Command authority", "Authority reason",
        "Gateway effective NED", "Gateway projected NED", "Projected intent",
        "Limiter state", "Episode", "Episode distance", "Episode limit",
        "Velocity cap", "Limiter reason",
        "ROS2 to PX4", "Control trace",
        "Packet bytes", "Diagnostics",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Engineering · Vision Flight Console")
        self.resize(430, 650)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.addWidget(label("ENGINEERING DATA", "engineeringTitle"))
        outer.addWidget(label("Read-only · values from the current packet and local receiver", "engineeringHint"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.viewport().setStyleSheet("background:#17232d;")
        body = QWidget()
        body.setStyleSheet("background:#17232d;")
        form = QFormLayout(body)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        self.fields = {}
        for name in self.FIELDS:
            value = label("--", "engineeringValue", wrap=True)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            form.addRow(label(name, "engineeringName"), value)
            self.fields[name] = value
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    def put(self, name, value):
        set_text(self.fields[name], value)


class Console(QMainWindow):
    def __init__(self, config, *, video=None, metadata=None):
        super().__init__()
        self.config = config
        self.video = video if video is not None else VideoReceiver(
            config["ffmpeg"], config["video_sdp"], config["width"], config["height"])
        self.metadata = metadata if metadata is not None else MetadataReceiver(
            config["metadata_host"], config["metadata_port"])
        self.started_at = self.last_report_at = self.last_ui_at = time.monotonic()
        self.last_displayed = self.last_painted = self.last_packets = self.last_decoded = 0
        self.display_fps = self.decode_fps = self.metadata_hz = 0.0
        config["metrics_jsonl"].parent.mkdir(parents=True, exist_ok=True)
        self.metrics_file = config["metrics_jsonl"].open("w", encoding="utf-8")
        self.setWindowTitle("灵眸控飞  |  Vision Flight Console")
        self.resize(1510, 920)
        self.setMinimumSize(1000, 680)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(9)

        header = QHBoxLayout()
        brand = label("灵眸控飞", "brand")
        subtitle = label("VISION FLIGHT CONSOLE", "subtitle")
        header.addWidget(brand)
        header.addSpacing(13)
        header.addWidget(subtitle)
        header.addStretch(1)
        header.addWidget(label("COMPETITION MODE", "modeBadge"))
        header.addSpacing(12)
        header.addWidget(label("MONITOR ONLY", "monitorBadge"))
        outer.addLayout(header)

        chain = QHBoxLayout()
        chain.setSpacing(5)
        self.nodes = {}
        for index, name in enumerate(("PERCEPTION", "AUTHORIZATION", "GESTURE", "SAFETY", "PX4")):
            if index:
                arrow = label("›", "chainArrow")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                chain.addWidget(arrow)
            node = EvidenceNode(name)
            self.nodes[name] = node
            chain.addWidget(node, 1)
        outer.addLayout(chain)

        content = QHBoxLayout()
        content.setSpacing(10)
        self.canvas = VideoCanvas(config)
        content.addWidget(self.canvas, 7)
        cards = QVBoxLayout()
        cards.setSpacing(8)
        self.operator_card = StatusCard("OPERATOR", ("Track", "Depth"))
        self.control_card = StatusCard("CONTROL", ("Gesture", "Intent", "Lease", "Safety"))
        self.px4_card = StatusCard("PX4", ("Mode", "Armed", "Failsafe", "Local Pos", "Control"))
        for card in (self.operator_card, self.control_card, self.px4_card):
            cards.addWidget(card, 1)
        content.addLayout(cards, 3)
        outer.addLayout(content, 1)

        flow = QHBoxLayout()
        flow.setSpacing(10)
        command = QFrame()
        command.setObjectName("flowCard")
        command_layout = QVBoxLayout(command)
        command_layout.setContentsMargins(14, 8, 14, 8)
        command_layout.setSpacing(3)
        command_layout.addWidget(label("COMMAND TO PX4", "flowTitle"))
        self.command_value = label("NOT FORWARDED  ·  DRY-RUN MODE", "flowValue", wrap=True)
        command_layout.addWidget(self.command_value)
        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        self.flight_buttons = {}
        for name in ("ARM", "TAKEOFF 1.2m", "LAND", "RETURN"):
            button = QPushButton(name)
            button.setEnabled(False)
            button.setToolTip("Ground Station monitor mode. PX4 live control is disabled.")
            buttons.addWidget(button)
            self.flight_buttons[name] = button
        command_layout.addLayout(buttons)
        flow.addWidget(command, 1)
        feedback = QFrame()
        feedback.setObjectName("flowCard")
        feedback_layout = QVBoxLayout(feedback)
        feedback_layout.setContentsMargins(14, 8, 14, 8)
        feedback_layout.addWidget(label("PX4 FEEDBACK", "flowTitle"))
        self.feedback_value = label("NO DATA  ·  PX4 DISCONNECTED", "flowValue", wrap=True)
        feedback_layout.addWidget(self.feedback_value)
        flow.addWidget(feedback, 1)
        outer.addLayout(flow)

        footer = QHBoxLayout()
        self.system_label = label("SYSTEM  ·  WAITING", "systemStatus")
        footer.addWidget(self.system_label)
        footer.addStretch(1)
        self.control_mode_value = label("CONTROL MODE  ·  DRY-RUN", "dryRun")
        footer.addWidget(self.control_mode_value)
        self.engineering_button = QPushButton("Engineering  ›")
        self.engineering_button.setObjectName("engineeringButton")
        footer.addWidget(self.engineering_button)
        outer.addLayout(footer)
        self.engineering = EngineeringDialog(self)
        self.engineering_button.clicked.connect(self.toggle_engineering)

        self.setStyleSheet("""
            QMainWindow, QDialog, QWidget#root {background:#0c141b;}
            QWidget {color:#e6eef2;font-family:'Microsoft YaHei UI';}
            QLabel#brand {font-size:23px;font-weight:700;color:#f2f6f7;}
            QLabel#subtitle {font-size:11px;letter-spacing:2px;color:#89a8b2;}
            QLabel#modeBadge {font-size:10px;font-weight:700;color:#72c6c4;}
            QLabel#monitorBadge {font-size:10px;color:#b8c9cb;}
            QLabel#chainArrow {font-size:25px;color:#59747d;}
            QLabel#nodeTitle, QLabel#cardTitle, QLabel#flowTitle {font-size:10px;font-weight:700;letter-spacing:1px;color:#91aab4;}
            QLabel#nodeValue {font-size:15px;font-weight:700;}
            QLabel#nodeDetail, QLabel#cardDetail {font-size:10px;color:#9eb0b7;}
            QLabel#cardPrimary {font-size:16px;font-weight:700;}
            QLabel#rowName {font-size:10px;color:#9daeb5;}
            QLabel#rowValue {font-size:11px;font-weight:600;color:#e4eef0;}
            QFrame#flowCard {background:#17232d;border:1px solid #30414c;border-radius:7px;}
            QLabel#flowValue {font-size:12px;font-weight:700;color:#d3dfe1;}
            QLabel#systemStatus {font-size:11px;font-weight:700;color:#77c7c5;}
            QLabel#dryRun {font-size:10px;font-weight:700;color:#e3b670;}
            QPushButton {background:#24343d;border:1px solid #41535c;border-radius:5px;padding:5px 10px;color:#d9e8e9;}
            QPushButton:disabled {color:#72828a;background:#1b292f;border:1px solid #364750;}
            QPushButton#engineeringButton {font-weight:700;color:#8bd0d0;}
            QLabel#engineeringTitle {font-size:16px;font-weight:700;color:#e5f2f3;}
            QLabel#engineeringHint, QLabel#engineeringName {font-size:10px;color:#9eafb8;}
            QLabel#engineeringValue {font-size:10px;font-weight:600;color:#e4eef0;}
            QScrollArea {border:0;}
        """)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(16)

    def toggle_engineering(self):
        if self.engineering.isVisible():
            self.engineering.hide()
            set_text(self.engineering_button, "Engineering  ›")
        else:
            self.engineering.show()
            self.engineering.raise_()
            set_text(self.engineering_button, "Engineering  ×")
            self.last_ui_at = 0
            self.refresh()

    def refresh(self):
        frame, sequence, video_age, video_metrics = self.video.snapshot()
        packet, metadata_age, metadata_metrics = self.metadata.snapshot()
        self.canvas.set_state(frame, sequence, video_age, packet, metadata_age)
        now = time.monotonic()
        if now-self.last_report_at >= 5:
            self._report(now, video_age, metadata_age, video_metrics, metadata_metrics, packet)
        if now-self.last_ui_at < .2:
            return
        self.last_ui_at = now
        video_ready = video_age is not None and video_age <= self.config["video_stale_ms"]
        state = competition_state(packet, metadata_age, video_ready=video_ready,
            metadata_stale_ms=self.config["metadata_stale_ms"],
            overlay_timeout_ms=self.config["overlay_timeout_ms"])
        for name, key in (("PERCEPTION", "perception"),
                          ("AUTHORIZATION", "authorization"),
                          ("GESTURE", "gesture"), ("SAFETY", "safety"),
                          ("PX4", "px4")):
            self.nodes[name].set_state(*state[key])
        self.operator_card.set_status(state["operator_state"],
            "CONTROL AUTHORIZED" if state["operator_state"] == "LOCKED_HIGH" else "Current ownership state",
            state["authorization"][2])
        self.operator_card.put("Track", state["track"])
        self.operator_card.put("Depth", state["depth"])
        self.control_card.set_status(state["control_safety"],
                                     state["stage6_reason"].replace("_", " "),
                                     state["safety"][2])
        for name, key in (("Gesture", "control_gesture"), ("Intent", "control_intent"),
                          ("Lease", "lease"), ("Safety", "control_safety")):
            self.control_card.put(name, state[key])
        px4 = state["px4_details"]
        self.px4_card.set_status(*px4["node"])
        for name, key in (("Mode", "mode"), ("Armed", "armed"),
                          ("Failsafe", "failsafe"), ("Local Pos", "local_position_valid")):
            self.px4_card.put(name, px4[key])
        self.px4_card.put("Control", "DRY-RUN")
        command = state["command_details"]
        if command is None:
            command_text = "NOT FORWARDED  ·  DRY-RUN MODE"
        else:
            velocity = command.get("velocity") or [None, None, None]
            values = ", ".join(shown(item) for item in velocity)
            projected = command.get("projected_velocity") or [None, None, None]
            projected_values = ", ".join(shown(item) for item in projected)
            command_text = (f"SHADOW · NOT TRANSMITTED · {shown(command.get('intent'))} "
                            f"· AUTHORITY {shown(command.get('authority'))} "
                            f"· EFFECTIVE NED [{values}] m/s "
                            f"· IF AUTHORIZED {shown(command.get('projected_intent'))} "
                            f"NED [{projected_values}] m/s")
        set_text(self.command_value, command_text)
        set_text(self.feedback_value, (f"{px4['mode']}  ·  ARMED {px4['armed']}  ·  "
                                       f"FAILSAFE {px4['failsafe']}  ·  LOCAL POS {px4['local_position_valid']}"
                                       if px4["connected"] else "NO DATA  ·  PX4 DISCONNECTED"))
        set_text(self.system_label, "SYSTEM  ·  " + state["system"])
        if self.engineering.isVisible():
            self._refresh_engineering(packet if state["metadata_ready"] else None,
                                      metadata_age, video_age, metadata_metrics,
                                      video_metrics)

    def _refresh_engineering(self, packet, metadata_age, video_age, meta, video):
        op = (packet or {}).get("operator") or {}
        gesture = (packet or {}).get("gesture") or {}
        stage6 = (packet or {}).get("stage6") or {}
        depth = (packet or {}).get("depth") or {}
        system = (packet or {}).get("system") or {}
        px4 = competition_state(packet, metadata_age,
            metadata_stale_ms=self.config["metadata_stale_ms"],
            overlay_timeout_ms=self.config["overlay_timeout_ms"])["px4_details"]
        command = (packet or {}).get("command") or {}
        if command.get("fresh") is not True:
            command = {}
        limiter = command.get("limiter") or []
        def limiter_value(index):
            return limiter[index] if isinstance(limiter, list) and len(limiter) > index else None
        limiter_state = {"I": "IDLE", "A": "ACTIVE", "L": "LIMIT_REACHED",
                         "P": "BLOCKED_INVALID_POSITION"}.get(limiter_value(0))
        limiter_reason = {"L": "EPISODE_DISTANCE_LIMIT",
                          "P": "POSITION_OR_MAPPING_BLOCKED"}.get(limiter_value(0))
        values = {
            "Board video FPS": shown(system.get("video_fps")),
            "AI FPS": shown(system.get("ai_fps")),
            "Metadata Hz": f"{self.metadata_hz:.1f}",
            "Metadata age": shown(round(metadata_age) if metadata_age is not None else None, " ms"),
            "AI age at send": shown((packet or {}).get("ai_age_ms"), " ms"),
            "Depth age at send": shown(depth.get("age_ms"), " ms"),
            "Decode FPS": f"{self.decode_fps:.1f}",
            "Display FPS": f"{self.display_fps:.1f}",
            "Video age": shown(round(video_age) if video_age is not None else None, " ms"),
            "Sequence gaps": meta["gaps"], "Invalid packets": meta["invalid"],
            "FFmpeg restarts": video["restarts"],
            "People count": shown((packet or {}).get("people_count")),
            "Track ID": shown(op.get("track_id")),
            "Session ID": shown(op.get("session_id")),
            "Pose score": shown(op.get("pose_score")),
            "Raw gesture": shown(gesture.get("raw")),
            "Stable gesture": shown(gesture.get("stable")),
            "Stage6 valid": shown(stage6.get("valid")),
            "Stage6 intent": shown(stage6.get("intent")),
            "Stage6 reason": shown(stage6.get("reason")),
            "Stage6 lease": shown(stage6.get("lease")),
            "PX4 status age": shown(round(px4["status_age_ms"]) if px4["status_age_ms"] is not None else None, " ms"),
            "PX4 mode raw": shown(px4["nav_state"]),
            "PX4 armed": px4["armed"], "PX4 failsafe": px4["failsafe"],
            "Local position valid": px4["local_position_valid"],
            "Local position NED": shown(px4["position"]),
            "Local velocity NED": shown(px4["velocity"]),
            "Last VehicleCommand ACK": shown(px4["ack"]),
            "Command mode": shown(command.get("mode")),
            "Command authority": shown(command.get("authority")),
            "Authority reason": shown(command.get("authority_reason")),
            "Gateway effective NED": shown(command.get("velocity")),
            "Gateway projected NED": shown(command.get("projected_velocity")),
            "Projected intent": shown(command.get("projected_intent")),
            "Limiter state": shown(limiter_state),
            "Episode": f"{shown(limiter_value(1))} · {shown(stage6.get('intent'))}",
            "Episode distance": shown(limiter_value(2), " m"),
            "Episode limit": shown(limiter_value(3), " m"),
            "Velocity cap": shown(limiter_value(4), " m/s"),
            "Limiter reason": shown(limiter_reason),
            "ROS2 to PX4": "NOT TRANSMITTED" if command else "--",
            "Control trace": (f"{shown(gesture.get('stable'))} → {shown(stage6.get('intent'))} "
                              f"→ {shown(command.get('authority'))} → "
                              f"{shown(command.get('projected_intent'))} → NOT TRANSMITTED"
                              if command else "--"),
            "Packet bytes": "--", "Diagnostics": "NOT AVAILABLE IN V1",
        }
        for name, value in values.items():
            self.engineering.put(name, value)

    def _report(self, now, video_age, metadata_age, video_metrics, metadata_metrics, packet):
        duration = now-self.last_report_at
        self.display_fps = (self.canvas.displayed_frames-self.last_displayed)/duration
        self.decode_fps = (video_metrics["frames"]-self.last_decoded)/duration
        self.metadata_hz = (metadata_metrics["received"]-self.last_packets)/duration
        report = {"event":"GS_WINDOW", "elapsed_s":round(now-self.started_at,2),
            "video_decode_fps":round(self.decode_fps,2),
            "video_decode_interval_fps":round(self.decode_fps,2),
            "video_display_fps":round(self.display_fps,2),
            "ui_fps":round((self.canvas.paint_count-self.last_painted)/duration,2),
            "metadata_hz":round(self.metadata_hz,2),
            "metadata_age_ms":round(metadata_age,1) if metadata_age is not None else None,
            "video_age_ms":round(video_age,1) if video_age is not None else None,
            "metadata_gaps":metadata_metrics["gaps"],
            "video_restarts":video_metrics["restarts"],
            "operator_state":(packet or {}).get("operator", {}).get("state")}
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
        self.engineering.hide()
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
    for font in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/segoeui.ttf"):
        QFontDatabase.addApplicationFont(font)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = Console(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
