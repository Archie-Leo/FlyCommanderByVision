#pragma once

#include "drone_control_gateway/intent_mapper.hpp"

#include <chrono>
#include <cstdint>
#include <string>

namespace drone_control_gateway
{

struct DemoSafetyConfig
{
  float horizontal_cap_mps{0.30F};
  float vertical_cap_mps{0.20F};
  float episode_limit_m{0.50F};
  std::chrono::milliseconds position_timeout{500};
};

struct LocalPositionSample
{
  float x{0.0F};
  float y{0.0F};
  float z{0.0F};
  bool xy_valid{false};
  bool z_valid{false};
  bool source_fresh{false};
  CommandLease::Clock::time_point received_at{};
};

enum class EpisodeState {IDLE, ACTIVE, LIMIT_REACHED, BLOCKED_INVALID_POSITION};

struct LimiterSnapshot
{
  EpisodeState state{EpisodeState::IDLE};
  uint64_t episode_id{0};
  std::string episode_intent{};
  float distance_m{0.0F};
  float limit_m{0.50F};
  float velocity_cap_mps{0.0F};
  std::string reason{"IDLE"};
  CommandLease::Clock::time_point started_at{};
  CommandLease::Clock::time_point last_valid_position_at{};
};

const char * episode_state_name(EpisodeState state);

class DemoSafetyLimiter
{
public:
  explicit DemoSafetyLimiter(DemoSafetyConfig config = {});
  MappingResult apply(
    const std::string & requested_intent, bool source_valid, uint64_t session_id,
    bool authority, const MappingResult & desired, const LocalPositionSample & position,
    CommandLease::Clock::time_point now, bool trusted_release = false);
  const LimiterSnapshot & snapshot() const noexcept {return snapshot_;}

private:
  bool position_valid(const std::string & intent, const LocalPositionSample & position,
    CommandLease::Clock::time_point now) const;
  void close(const std::string & reason);
  MappingResult hover(const std::string & reason) const;

  DemoSafetyConfig config_;
  LimiterSnapshot snapshot_{};
  uint64_t session_id_{0};
  bool require_neutral_{false};
  float start_x_{0.0F};
  float start_y_{0.0F};
  float start_z_{0.0F};
};

}  // namespace drone_control_gateway
