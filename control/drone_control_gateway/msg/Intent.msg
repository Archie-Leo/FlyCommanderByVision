# Domain-level interaction command. It deliberately contains no PX4 types.
builtin_interfaces/Time stamp
int64 operator_id
string intent
float32 confidence
bool valid
uint64 seq
string reason

# Optional requested magnitude. <= 0 selects the configured nominal speed.
# The gateway always clamps it to the configured maximum.
float32 requested_speed_m_s

# Optional yaw-rate magnitude in rad/s. <= 0 selects the configured nominal
# yaw rate. Used only by YAW_LEFT/YAW_RIGHT and always clamped.
float32 requested_yaw_rate_rad_s
