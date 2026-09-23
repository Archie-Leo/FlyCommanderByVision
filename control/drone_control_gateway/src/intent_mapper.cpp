#include "drone_control_gateway/intent_mapper.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <stdexcept>

namespace drone_control_gateway
{
namespace
{
std::string canonicalize(std::string value)
{
  value.erase(value.begin(), std::find_if(value.begin(), value.end(), [](unsigned char c) {
    return !std::isspace(c);
  }));
  value.erase(std::find_if(value.rbegin(), value.rend(), [](unsigned char c) {
    return !std::isspace(c);
  }).base(), value.end());
  std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
  return value;
}

float requested_or_default(float requested, float fallback)
{
  return std::isfinite(requested) && requested > 0.0F ? requested : fallback;
}
}  // namespace

IntentMapper::IntentMapper(MapperConfig config) : config_(config)
{
  const bool valid = std::isfinite(config_.forward_speed_mps) &&
    std::isfinite(config_.horizontal_speed_mps) &&
    std::isfinite(config_.vertical_speed_mps) &&
    std::isfinite(config_.max_horizontal_speed_mps) &&
    std::isfinite(config_.max_vertical_speed_mps) &&
    std::isfinite(config_.yaw_rate_rad_s) &&
    std::isfinite(config_.max_yaw_rate_rad_s) &&
    config_.forward_speed_mps >= 0.0F && config_.horizontal_speed_mps >= 0.0F &&
    config_.vertical_speed_mps >= 0.0F && config_.yaw_rate_rad_s >= 0.0F &&
    config_.max_horizontal_speed_mps > 0.0F && config_.max_vertical_speed_mps > 0.0F &&
    config_.max_yaw_rate_rad_s > 0.0F;
  if (!valid) {
    throw std::invalid_argument("Gateway speeds/rates must be finite; nominal >= 0 and maxima > 0");
  }
}

MappingResult IntentMapper::map(
  const std::string & intent, bool valid, float requested_speed_m_s,
  float requested_yaw_rate_rad_s) const
{
  MappingResult result;
  if (!valid) {
    result.reason = "invalid intent message -> HOVER";
    return result;
  }

  const std::string command = canonicalize(intent);
  const float forward = std::min(
    requested_or_default(requested_speed_m_s, config_.forward_speed_mps),
    config_.max_horizontal_speed_mps);
  const float lateral = std::min(
    requested_or_default(requested_speed_m_s, config_.horizontal_speed_mps),
    config_.max_horizontal_speed_mps);
  const float vertical = std::min(
    requested_or_default(requested_speed_m_s, config_.vertical_speed_mps),
    config_.max_vertical_speed_mps);

  result.accepted = true;
  result.canonical_intent = command;
  if (command == "HOVER") {
    result.velocity = {};
  } else if (command == "MOVE_FORWARD") {
    // PX4 local NED world frame: +X is North. This is not vehicle body-forward.
    result.velocity.north_mps = forward;
  } else if (command == "MOVE_BACKWARD") {
    result.velocity.north_mps = -forward;
  } else if (command == "MOVE_RIGHT") {
    // PX4 local NED world frame: +Y is East. V1 RIGHT means world East, not body-right.
    result.velocity.east_mps = lateral;
  } else if (command == "MOVE_LEFT") {
    result.velocity.east_mps = -lateral;
  } else if (command == "ASCEND") {
    // PX4 NED: +Z is Down, therefore ascending requires negative Z velocity.
    result.velocity.down_mps = -vertical;
  } else if (command == "DESCEND") {
    result.velocity.down_mps = vertical;
  } else if (command == "YAW_RIGHT" || command == "YAW_LEFT") {
    const float yaw_rate = std::min(
      requested_or_default(requested_yaw_rate_rad_s, config_.yaw_rate_rad_s),
      config_.max_yaw_rate_rad_s);
    // About NED +Z (Down), positive yaw is clockwise viewed from above: RIGHT.
    result.yaw_rate_ned_rad_s = command == "YAW_RIGHT" ? yaw_rate : -yaw_rate;
  } else {
    result.accepted = false;
    result.canonical_intent = "HOVER";
    result.velocity = {};
    result.reason = "unsupported intent -> HOVER";
  }
  return result;
}

CommandLease::CommandLease(std::chrono::duration<double> timeout) : timeout_(timeout)
{
  if (!std::isfinite(timeout_.count()) || timeout_.count() <= 0.0) {
    throw std::invalid_argument("Command timeout must be finite and > 0");
  }
}

void CommandLease::update(const MappingResult & command, Clock::time_point now)
{
  active_ = command;
  active_valid_ = command.accepted;
  last_update_ = now;
}

MappingResult CommandLease::current(Clock::time_point now, bool * timed_out)
{
  if (timed_out != nullptr) {
    *timed_out = false;
  }
  if (active_valid_ && now - last_update_ <= timeout_) {
    return active_;
  }
  if (active_valid_ && timed_out != nullptr) {
    *timed_out = true;
  }
  active_valid_ = false;
  active_ = MappingResult{};
  return active_;
}

}  // namespace drone_control_gateway
