"""Local Qt overlay on the frozen H.264 video; display coordinates only."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .display_policy import display_state
from .protocol import EDGES

BG = QColor("#10151c")
FG = QColor("#e9eef5")
GOOD = QColor("#5ee0b6")
WARN = QColor("#f6c66a")
BAD = QColor("#f08080")


class VideoCanvas(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.frame = None
        self.frame_data = None
        self.frame_sequence = -1
        self.packet = None
        self.packet_age_ms = None
        self.video_age_ms = None
        self.paint_count = 0
        self.displayed_frames = 0
        self.last_painted_sequence = -1
        self.setMinimumSize(700, 525)

    def set_state(self, frame, sequence, video_age_ms, packet, packet_age_ms):
        if frame is not None and sequence != self.frame_sequence:
            self.frame_data = frame
            self.frame = QImage(self.frame_data, self.config["width"], self.config["height"],
                                self.config["width"]*4, QImage.Format.Format_ARGB32)
            self.frame_sequence = sequence
        self.video_age_ms = video_age_ms
        self.packet, self.packet_age_ms = packet, packet_age_ms
        self.update()

    def paintEvent(self, event):
        self.paint_count += 1
        painter = QPainter(self)
        painter.fillRect(self.rect(), BG)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.config["width"], self.config["height"]
        scale = min(self.width()/width, self.height()/height)
        area = QRectF((self.width()-width*scale)/2, (self.height()-height*scale)/2,
                      width*scale, height*scale)
        fresh_video = (self.frame is not None and self.video_age_ms is not None and
                       self.video_age_ms <= self.config["video_stale_ms"])
        if fresh_video:
            painter.drawImage(area, self.frame)
            if self.frame_sequence != self.last_painted_sequence:
                self.displayed_frames += 1
                self.last_painted_sequence = self.frame_sequence
        else:
            self._center(painter, "VIDEO LOST", BAD)
        policy = display_state(self.packet, self.packet_age_ms,
            metadata_stale_ms=self.config["metadata_stale_ms"],
            overlay_timeout_ms=self.config["overlay_timeout_ms"])
        if fresh_video and policy["draw_person"]:
            self._person(painter, area, scale)
        if fresh_video:
            self._status_strip(painter, area, policy)
        painter.end()

    def _center(self, painter, label, color):
        painter.setPen(color)
        painter.setFont(QFont("Microsoft YaHei UI", 19, QFont.Weight.DemiBold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, label)

    def _person(self, painter, area, scale):
        op = (self.packet or {}).get("operator") or {}
        bbox = op.get("bbox")
        joints = op.get("keypoints") or []
        if not bbox:
            return
        locked = op.get("kind") == "operator" and op.get("state") == "LOCKED_HIGH"
        color = GOOD if locked else WARN
        def xy(x, y):
            return QPointF(area.left()+x*scale, area.top()+y*scale)
        painter.setPen(QPen(color, 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(xy(bbox[0], bbox[1]), xy(bbox[2], bbox[3])).normalized())
        painter.setPen(QPen(color, 2))
        for a, b in EDGES:
            if a < len(joints) and b < len(joints) and joints[a] and joints[b]:
                painter.drawLine(xy(joints[a][0], joints[a][1]),
                                 xy(joints[b][0], joints[b][1]))
        painter.setBrush(color)
        for joint in joints:
            if joint:
                painter.drawEllipse(xy(joint[0], joint[1]), 3.4, 3.4)
        label = ("CONTROL OPERATOR" if locked else "CANDIDATE")
        if op.get("track_id") is not None:
            label += f"  #{op['track_id']}"
        label += "  " + str(op.get("state") or "")
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        text_y = max(area.top()+26, area.top()+bbox[1]*scale-9)
        text_x = max(area.left()+8, min(area.right()-300, area.left()+bbox[0]*scale))
        painter.fillRect(QRectF(text_x-5, text_y-17, 300, 23), QColor(10, 20, 28, 215))
        painter.setPen(color)
        painter.drawText(QPointF(text_x, text_y), label)

    def _status_strip(self, painter, area, policy):
        painter.fillRect(QRectF(area.left()+12, area.top()+12, 250, 78),
                         QColor(10, 20, 28, 205))
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        painter.setPen(FG)
        painter.drawText(QPointF(area.left()+22, area.top()+35),
                         "GESTURE   " + policy["gesture"])
        painter.drawText(QPointF(area.left()+22, area.top()+58),
                         "INTENT      " + policy["intent"])
        color = GOOD if policy["ai_ready"] else WARN
        if policy["operator_state"] == "OPERATOR_LOST":
            color = BAD
        painter.setPen(color)
        state = policy["operator_state"] or "WAITING FOR OPERATOR"
        painter.drawText(QPointF(area.left()+22, area.top()+80), state[:31])
        if state == "WAIT_OPERATOR":
            painter.setPen(WARN)
            painter.drawText(QPointF(area.left()+20, area.bottom()-20),
                             "WAITING FOR OPERATOR  •  Show T-Pose to authorize")
