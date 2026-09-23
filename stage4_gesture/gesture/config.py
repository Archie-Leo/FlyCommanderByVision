from dataclasses import dataclass


@dataclass(frozen=True)
class GeometryConfig:
    min_joint_confidence: float = 0.65
    straight_elbow_min_deg: float = 145.0
    bent_elbow_min_deg: float = 65.0
    bent_elbow_max_deg: float = 125.0
    horizontal_dy_max: float = 0.35
    single_arm_reach_x_min: float = 0.65
    opposite_down_y_min: float = 0.65
    opposite_down_abs_x_max: float = 0.45
    ascend_up_y_min: float = 0.65
    descend_down_y_min: float = 0.65
    descend_out_x_min: float = 0.40
    hover_upper_out_x_min: float = 0.40
    hover_forearm_up_y_min: float = 0.35
    hover_forearm_abs_x_max: float = 0.40
    min_segment_length: float = 0.20


@dataclass(frozen=True)
class TemporalConfig:
    confirm_ms: int = 300
    min_confirm_frames: int = 4
    release_ms: int = 120
    min_release_frames: int = 2
