#pragma once

#include <chrono>
#include <string>

namespace drone_control_gateway
{

struct VelocityNed
{
  float north_mps{0.0F};
  float east_mps{0.0F};
  float down_mps{0.0F};
};

struct MappingResult
{
  VelocityNed velocity{};
  float yaw_rate_ned_rad_s{0.0F};
  bool accepted{false};
  std::string canonical_intent{"HOVER"};
  std::string reason{};
};

struct MapperConfig
{
  float forward_speed_mps{0.8F};
  float horizontal_speed_mps{0.8F};
  float vertical_speed_mps{0.5F};
  float max_horizontal_speed_mps{1.0F};
  float max_vertical_speed_mps{0.7F};
  float yaw_rate_rad_s{0.35F};
  float max_yaw_rate_rad_s{0.6F};
};

class IntentMapper
{
public:
  explicit IntentMapper(MapperConfig config);
  MappingResult map(
    const std::string & intent, bool valid, float requested_speed_m_s,
    float requested_yaw_rate_rad_s = 0.0F) const;
  const MapperConfig & config() const noexcept {return config_;}

private:
  MapperConfig config_;
};

class CommandLease
{
public:
  using Clock = std::chrono::steady_clock;

  explicit CommandLease(std::chrono::duration<double> timeout);
  void update(const MappingResult & command, Clock::time_point now);
  MappingResult current(Clock::time_point now, bool * timed_out = nullptr);

private:
  std::chrono::duration<double> timeout_;
  MappingResult active_{};
  Clock::time_point last_update_{};
  bool active_valid_{false};
};

}  // namespace drone_control_gateway
