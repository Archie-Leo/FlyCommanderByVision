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
        self.setMinimumSize(480, 320)

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
        if fresh_video and policy["metadata_ready"] and policy["ai_ready"] and policy["draw_person"]:
            self._person(painter, area, scale)
        if fresh_video and not policy["metadata_ready"]:
            painter.fillRect(QRectF(area.left()+12, area.top()+12, 190, 31),
                             QColor(10, 20, 28, 215))
            painter.setPen(WARN)
            painter.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.DemiBold))
            painter.drawText(QPointF(area.left()+22, area.top()+34), "METADATA LOST")
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
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        text_y = max(area.top()+27, area.top()+bbox[1]*scale-20)
        text_x = max(area.left()+8, min(area.right()-265, area.left()+bbox[0]*scale))
        painter.fillRect(QRectF(text_x-5, text_y-17, 265, 41), QColor(10, 20, 28, 215))
        painter.setPen(color)
        painter.drawText(QPointF(text_x, text_y), label)
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.drawText(QPointF(text_x, text_y+17), str(op.get("state") or ""))
