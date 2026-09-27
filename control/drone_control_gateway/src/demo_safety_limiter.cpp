#include "drone_control_gateway/demo_safety_limiter.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace drone_control_gateway
{
namespace
{
bool lateral(const std::string & intent)
{
  return intent == "MOVE_LEFT" || intent == "MOVE_RIGHT";
}

bool vertical(const std::string & intent)
{
  return intent == "ASCEND" || intent == "DESCEND";
}
}  // namespace

const char * episode_state_name(EpisodeState state)
{
  switch (state) {
    case EpisodeState::IDLE: return "IDLE";
    case EpisodeState::ACTIVE: return "ACTIVE";
    case EpisodeState::LIMIT_REACHED: return "LIMIT_REACHED";
    case EpisodeState::BLOCKED_INVALID_POSITION: return "BLOCKED_INVALID_POSITION";
  }
  return "UNKNOWN";
}

DemoSafetyLimiter::DemoSafetyLimiter(DemoSafetyConfig config) : config_(config)
{
  if (!std::isfinite(config_.horizontal_cap_mps) || config_.horizontal_cap_mps <= 0.0F ||
    !std::isfinite(config_.vertical_cap_mps) || config_.vertical_cap_mps <= 0.0F ||
    !std::isfinite(config_.episode_limit_m) || config_.episode_limit_m <= 0.0F ||
    config_.position_timeout.count() <= 0)
  {
    throw std::invalid_argument("invalid demo safety limits");
  }
  snapshot_.limit_m = config_.episode_limit_m;
}

void DemoSafetyLimiter::close(const std::string & reason)
{
  snapshot_.state = EpisodeState::IDLE;
  snapshot_.episode_intent.clear();
  snapshot_.distance_m = 0.0F;
  snapshot_.velocity_cap_mps = 0.0F;
  snapshot_.reason = reason;
  snapshot_.started_at = {};
  snapshot_.last_valid_position_at = {};
}

MappingResult DemoSafetyLimiter::hover(const std::string & reason) const
{
  MappingResult safe;
  safe.accepted = true;
  safe.reason = reason;
  return safe;
}

bool DemoSafetyLimiter::position_valid(
  const std::string & intent, const LocalPositionSample & position,
  CommandLease::Clock::time_point now) const
{
  if (!position.source_fresh || position.received_at == CommandLease::Clock::time_point{} ||
    now < position.received_at || now - position.received_at > config_.position_timeout)
  {
    return false;
  }
  if (lateral(intent)) {
    return position.xy_valid && std::isfinite(position.x) && std::isfinite(position.y);
  }
  return position.z_valid && std::isfinite(position.z);
}

MappingResult DemoSafetyLimiter::apply(
  const std::string & requested_intent, bool source_valid, uint64_t session_id,
  bool authority, const MappingResult & desired, const LocalPositionSample & position,
  CommandLease::Clock::time_point now, bool trusted_release)
{
  if (!authority) {
    if (session_id_ != 0 || snapshot_.state != EpisodeState::IDLE) {
      require_neutral_ = true;
    }
    close("FLIGHT_AUTHORITY_REVOKED");
    return hover(snapshot_.reason);
  }
  if (session_id == 0) {
    if (session_id_ != 0 || snapshot_.state != EpisodeState::IDLE) {
      require_neutral_ = true;
    }
    close("SESSION_UNAVAILABLE");
    return hover(snapshot_.reason);
  }
  if (session_id_ != 0 && session_id_ != session_id) {
    session_id_ = session_id;
    require_neutral_ = true;
    close("SESSION_CHANGED");
    return hover(snapshot_.reason);
  }
  session_id_ = session_id;

  if (!source_valid || requested_intent == "UNKNOWN" || requested_intent == "INVALID") {
    close("SOURCE_INVALID_OR_RELEASED");
    if (trusted_release) {
      require_neutral_ = false;
    }
    return hover(snapshot_.reason);
  }
  if (requested_intent == "HOVER") {
    close("TRUSTED_HOVER_RELEASE");
    require_neutral_ = false;
    return hover(snapshot_.reason);
  }
  if (require_neutral_) {
    close("WAIT_FRESH_GESTURE_RELEASE");
    return hover(snapshot_.reason);
  }
  if (!lateral(requested_intent) && !vertical(requested_intent)) {
    close("UNSUPPORTED_DEMO_INTENT");
    return hover(snapshot_.reason);
  }
  if (snapshot_.episode_intent != requested_intent) {
    close("GESTURE_SWITCH");
  }
  if (snapshot_.state == EpisodeState::LIMIT_REACHED) {
    return hover("EPISODE_DISTANCE_LIMIT");
  }
  if (snapshot_.state == EpisodeState::BLOCKED_INVALID_POSITION) {
    return hover(snapshot_.reason);
  }
  if (!desired.accepted || desired.canonical_intent != requested_intent) {
    snapshot_.episode_intent = requested_intent;
    snapshot_.state = EpisodeState::BLOCKED_INVALID_POSITION;
    snapshot_.reason = desired.reason.empty() ? "MAPPING_REJECTED" : desired.reason;
    return hover(snapshot_.reason);
  }
  if (!position_valid(requested_intent, position, now)) {
    snapshot_.episode_intent = requested_intent;
    snapshot_.state = EpisodeState::BLOCKED_INVALID_POSITION;
    snapshot_.reason = "INVALID_OR_STALE_LOCAL_POSITION";
    return hover(snapshot_.reason);
  }
  if (snapshot_.state == EpisodeState::IDLE) {
    ++snapshot_.episode_id;
    snapshot_.episode_intent = requested_intent;
    snapshot_.state = EpisodeState::ACTIVE;
    snapshot_.started_at = now;
    start_x_ = position.x;
    start_y_ = position.y;
    start_z_ = position.z;
  }
  snapshot_.last_valid_position_at = position.received_at;
  snapshot_.distance_m = lateral(requested_intent) ?
    std::hypot(position.x - start_x_, position.y - start_y_) :
    std::abs(position.z - start_z_);
  snapshot_.velocity_cap_mps = lateral(requested_intent) ?
    config_.horizontal_cap_mps : config_.vertical_cap_mps;
  if (!std::isfinite(snapshot_.distance_m) ||
    snapshot_.distance_m >= config_.episode_limit_m)
  {
    snapshot_.state = EpisodeState::LIMIT_REACHED;
    snapshot_.reason = "EPISODE_DISTANCE_LIMIT";
    return hover(snapshot_.reason);
  }
  MappingResult limited = desired;
  if (lateral(requested_intent)) {
    const float speed = std::hypot(limited.velocity.north_mps, limited.velocity.east_mps);
    if (!std::isfinite(speed)) {
      snapshot_.state = EpisodeState::BLOCKED_INVALID_POSITION;
      snapshot_.reason = "INVALID_MAPPED_VELOCITY";
      return hover(snapshot_.reason);
    }
    if (speed > config_.horizontal_cap_mps) {
      const float scale = config_.horizontal_cap_mps / speed;
      limited.velocity.north_mps *= scale;
      limited.velocity.east_mps *= scale;
    }
  } else {
    if (!std::isfinite(limited.velocity.down_mps)) {
      snapshot_.state = EpisodeState::BLOCKED_INVALID_POSITION;
      snapshot_.reason = "INVALID_MAPPED_VELOCITY";
      return hover(snapshot_.reason);
    }
    limited.velocity.down_mps = std::clamp(
      limited.velocity.down_mps, -config_.vertical_cap_mps, config_.vertical_cap_mps);
  }
  snapshot_.reason = "ACTIVE";
  return limited;
}

}  // namespace drone_control_gateway
