#include "drone_control_gateway/intent_mapper.hpp"
#include "drone_control_gateway/demo_safety_limiter.hpp"
#include "drone_control_gateway/msg/intent.hpp"

#include <px4_msgs/msg/offboard_control_mode.hpp>
#include <px4_msgs/msg/trajectory_setpoint.hpp>
#include <px4_msgs/msg/vehicle_local_position.hpp>
#include <px4_msgs/msg/vehicle_status.hpp>
#include <rclcpp/rclcpp.hpp>

#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <limits>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>

using namespace std::chrono_literals;

namespace drone_control_gateway
{

class ControlGatewayNode : public rclcpp::Node
{
public:
  ControlGatewayNode()
  : Node("control_gateway"),
    mapper_(load_mapper_config()),
    lease_(std::chrono::duration<double>(
        declare_parameter<double>("command_timeout_s", 0.5))),
    limiter_config_(load_safety_config()),
    limiter_(limiter_config_),
    projected_limiter_(limiter_config_)
  {
    shadow_mode_ = declare_parameter<bool>("shadow_mode", false);
    shadow_snapshot_path_ = declare_parameter<std::string>("shadow_snapshot_path", "");
    shadow_trace_path_ = declare_parameter<std::string>("shadow_trace_path", "");
    if (shadow_mode_) {
      if (shadow_snapshot_path_.empty() || shadow_trace_path_.empty()) {
        throw std::invalid_argument("shadow mode requires snapshot and trace paths");
      }
      shadow_trace_.open(shadow_trace_path_, std::ios::app);
      if (!shadow_trace_) {
        throw std::runtime_error("cannot open shadow trace");
      }
    } else {
      offboard_mode_pub_ = create_publisher<px4_msgs::msg::OffboardControlMode>(
        "/fmu/in/offboard_control_mode", 10);
      trajectory_pub_ = create_publisher<px4_msgs::msg::TrajectorySetpoint>(
        "/fmu/in/trajectory_setpoint", 10);
    }
    intent_sub_ = create_subscription<drone_control_gateway::msg::Intent>(
      shadow_mode_ ? "/interaction/intent_shadow" : "/interaction/intent",
      rclcpp::QoS(10).reliable(),
      std::bind(&ControlGatewayNode::on_intent, this, std::placeholders::_1));
    if (shadow_mode_) {
      candidate_sub_ = create_subscription<drone_control_gateway::msg::Intent>(
        "/interaction/candidate_shadow", rclcpp::QoS(10).reliable(),
        std::bind(&ControlGatewayNode::on_candidate, this, std::placeholders::_1));
    }
    heading_sub_ = create_subscription<px4_msgs::msg::VehicleLocalPosition>(
      "/fmu/out/vehicle_local_position_v1",
      rclcpp::QoS(rclcpp::KeepLast(5)).best_effort().transient_local(),
      [this](px4_msgs::msg::VehicleLocalPosition::SharedPtr msg) {
        std::lock_guard<std::mutex> lock(command_mutex_);
        const auto now_us = std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count();
        const auto sample_us = static_cast<int64_t>(msg->timestamp_sample);
        heading_sample_fresh_ = sample_us > 0 && sample_us <= now_us + 250000 &&
          now_us - sample_us <= 1500000;
        heading_rad_ = msg->heading;
        heading_good_for_control_ = msg->heading_good_for_control;
        heading_received_at_ = CommandLease::Clock::now();
        position_.x = msg->x;
        position_.y = msg->y;
        position_.z = msg->z;
        position_.xy_valid = msg->xy_valid;
        position_.z_valid = msg->z_valid;
        position_.source_fresh = heading_sample_fresh_;
        position_.received_at = heading_received_at_;
      });
    status_sub_ = create_subscription<px4_msgs::msg::VehicleStatus>(
      "/fmu/out/vehicle_status_v4",
      rclcpp::QoS(rclcpp::KeepLast(5)).best_effort().transient_local(),
      [this](px4_msgs::msg::VehicleStatus::SharedPtr msg) {
        std::lock_guard<std::mutex> lock(command_mutex_);
        status_armed_ = msg->arming_state == msg->ARMING_STATE_ARMED;
        status_offboard_ = msg->nav_state == msg->NAVIGATION_STATE_OFFBOARD;
        status_failsafe_ = msg->failsafe;
        status_received_at_ = CommandLease::Clock::now();
      });

    const double heartbeat_hz = declare_parameter<double>("heartbeat_hz", 10.0);
    if (!std::isfinite(heartbeat_hz) || heartbeat_hz <= 2.0) {
      throw std::invalid_argument("heartbeat_hz must be finite and > 2 Hz");
    }
    const auto period = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::duration<double>(1.0 / heartbeat_hz));
    timer_ = create_wall_timer(period, std::bind(&ControlGatewayNode::publish_cycle, this));

    RCLCPP_INFO(
      get_logger(),
      "Gateway %s ready in HOVER. Operator RIGHT=body LEFT, LEFT=body RIGHT "
      "(valid PX4 heading required); ASCEND=-Z, DESCEND=+Z, "
      "YAW_RIGHT=+rate clockwise, YAW_LEFT=-rate. No auto ARM/OFFBOARD/TAKEOFF/LAND.",
      shadow_mode_ ? "SHADOW (NO PX4 PUBLISHERS)" : "LIVE");
  }

private:
  void on_candidate(const drone_control_gateway::msg::Intent::SharedPtr msg)
  {
    std::lock_guard<std::mutex> lock(command_mutex_);
    candidate_msg_ = msg;
    candidate_seq_ = msg->seq;
    candidate_received_at_ = CommandLease::Clock::now();
  }

  ControlFrameContext frame_context_locked(CommandLease::Clock::time_point now) const
  {
    ControlFrameContext frame;
    frame.heading_rad = heading_rad_;
    frame.heading_fresh = heading_received_at_ != CommandLease::Clock::time_point{} &&
      now - heading_received_at_ <= 1500ms && heading_sample_fresh_;
    frame.heading_valid = frame.heading_fresh && heading_good_for_control_ &&
      std::isfinite(heading_rad_);
    return frame;
  }

  static bool lateral(const std::string & intent)
  {
    return intent == "MOVE_LEFT" || intent == "MOVE_RIGHT";
  }

  MapperConfig load_mapper_config()
  {
    MapperConfig config;
    config.forward_speed_mps = static_cast<float>(
      declare_parameter<double>("forward_speed_mps", 0.8));
    config.horizontal_speed_mps = static_cast<float>(
      declare_parameter<double>("horizontal_speed_mps", 0.8));
    config.vertical_speed_mps = static_cast<float>(
      declare_parameter<double>("vertical_speed_mps", 0.5));
    config.max_horizontal_speed_mps = static_cast<float>(
      declare_parameter<double>("max_horizontal_speed_mps", 1.0));
    config.max_vertical_speed_mps = static_cast<float>(
      declare_parameter<double>("max_vertical_speed_mps", 0.7));
    config.yaw_rate_rad_s = static_cast<float>(
      declare_parameter<double>("yaw_rate_rad_s", 0.35));
    config.max_yaw_rate_rad_s = static_cast<float>(
      declare_parameter<double>("max_yaw_rate_rad_s", 0.6));
    return config;
  }

  DemoSafetyConfig load_safety_config()
  {
    DemoSafetyConfig config;
    config.horizontal_cap_mps = static_cast<float>(
      declare_parameter<double>("demo_horizontal_cap_mps", 0.30));
    config.vertical_cap_mps = static_cast<float>(
      declare_parameter<double>("demo_vertical_cap_mps", 0.20));
    config.episode_limit_m = static_cast<float>(
      declare_parameter<double>("demo_episode_limit_m", 0.50));
    config.position_timeout = std::chrono::milliseconds(
      declare_parameter<int>("demo_position_timeout_ms", 500));
    return config;
  }

  void on_intent(const drone_control_gateway::msg::Intent::SharedPtr msg)
  {
    MappingResult mapped;
    bool should_log = false;
    {
      std::lock_guard<std::mutex> lock(command_mutex_);
      last_intent_msg_ = msg;
      last_intent_received_at_ = CommandLease::Clock::now();
      if (msg->reason == "FRESH_GESTURE_READY") {
        trusted_release_pending_ = true;
      }
      mapped = mapper_.map(msg->intent, msg->valid,
        msg->requested_speed_m_s, msg->requested_yaw_rate_rad_s,
        frame_context_locked(CommandLease::Clock::now()));
      last_mapped_ = mapped;
      lease_.update(mapped, CommandLease::Clock::now());
      should_log = msg->seq != last_logged_seq_ ||
        mapped.canonical_intent != last_logged_intent_ || !mapped.accepted;
      last_logged_seq_ = msg->seq;
      last_logged_intent_ = mapped.canonical_intent;
      last_intent_seq_ = msg->seq;
      last_intent_valid_ = msg->valid;
      last_intent_reason_ = msg->reason;
    }
    if (!should_log || shadow_mode_) {
      return;
    }
    if (!mapped.accepted) {
      RCLCPP_WARN(get_logger(), "Rejected '%s': %s", msg->intent.c_str(), mapped.reason.c_str());
    } else {
      RCLCPP_INFO(
        get_logger(), "Intent %s seq=%llu -> NED velocity [%.3f, %.3f, %.3f] m/s, yaw_rate %.3f rad/s",
        mapped.canonical_intent.c_str(), static_cast<unsigned long long>(msg->seq),
        mapped.velocity.north_mps, mapped.velocity.east_mps, mapped.velocity.down_mps,
        mapped.yaw_rate_ned_rad_s);
    }
  }

  void publish_cycle()
  {
    MappingResult command;
    MappingResult projected;
    ControlFrameContext frame;
    uint64_t projected_seq = 0;
    std::string candidate_intent;
    bool timed_out = false;
    LimiterSnapshot limiter_snapshot;
    LimiterSnapshot projected_snapshot;
    {
      std::lock_guard<std::mutex> lock(command_mutex_);
      const auto now = CommandLease::Clock::now();
      frame = frame_context_locked(now);
      command = lease_.current(now, &timed_out);
      if (command.accepted && lateral(command.canonical_intent) && last_intent_msg_) {
        command = mapper_.map(last_intent_msg_->intent, last_intent_msg_->valid,
          last_intent_msg_->requested_speed_m_s, last_intent_msg_->requested_yaw_rate_rad_s,
          frame);
        if (!command.accepted) {
          // Revoke an active lateral lease as soon as heading becomes unsafe.
          lease_.update(command, now);
          last_mapped_ = command;
        }
      }
      if (shadow_mode_ && candidate_msg_ && now - candidate_received_at_ <= 500ms) {
        projected = mapper_.map(candidate_msg_->intent, candidate_msg_->valid,
          candidate_msg_->requested_speed_m_s, candidate_msg_->requested_yaw_rate_rad_s, frame);
        projected_seq = candidate_seq_;
        candidate_intent = candidate_msg_->intent;
      }
      const bool status_fresh = status_received_at_ != CommandLease::Clock::time_point{} &&
        now >= status_received_at_ && now - status_received_at_ <= 1500ms;
      const bool flight_authority = status_fresh && status_armed_ && status_offboard_ &&
        !status_failsafe_;
      const bool intent_current = last_intent_msg_ && !timed_out &&
        now - last_intent_received_at_ <= 500ms;
      if (intent_current && !command.accepted) {
        command = last_mapped_;
      }
      command = limiter_.apply(
        intent_current ? last_intent_msg_->intent : "UNKNOWN",
        intent_current && last_intent_msg_->valid,
        last_intent_msg_ ? static_cast<uint64_t>(last_intent_msg_->operator_id) : 0,
        !shadow_mode_ && flight_authority, command, position_, now,
        trusted_release_pending_ && flight_authority);
      trusted_release_pending_ = false;
      limiter_snapshot = limiter_.snapshot();
      if (shadow_mode_) {
        const bool candidate_current = candidate_msg_ && now - candidate_received_at_ <= 500ms;
        projected = projected_limiter_.apply(
          candidate_current ? candidate_msg_->intent : "UNKNOWN",
          candidate_current && candidate_msg_->valid,
          candidate_current ? static_cast<uint64_t>(candidate_msg_->operator_id) : 0,
          true, projected, position_, now);
        projected_snapshot = projected_limiter_.snapshot();
      }
    }
    if (timed_out) {
      RCLCPP_WARN(get_logger(), "Intent timeout -> HOVER (zero NED velocity and yaw rate)");
    }

    const uint64_t timestamp_us = static_cast<uint64_t>(get_clock()->now().nanoseconds() / 1000);
    px4_msgs::msg::OffboardControlMode mode{};
    mode.timestamp = timestamp_us;
    mode.position = false;
    mode.velocity = true;
    mode.acceleration = false;
    mode.attitude = false;
    mode.body_rate = false;
    mode.thrust_and_torque = false;
    mode.direct_actuator = false;

    const float nan = std::numeric_limits<float>::quiet_NaN();
    px4_msgs::msg::TrajectorySetpoint setpoint{};
    setpoint.timestamp = timestamp_us;
    setpoint.position = {nan, nan, nan};
    setpoint.velocity = {
      command.velocity.north_mps, command.velocity.east_mps, command.velocity.down_mps};
    setpoint.acceleration = {nan, nan, nan};
    setpoint.jerk = {nan, nan, nan};
    setpoint.yaw = nan;
    setpoint.yawspeed = command.yaw_rate_ned_rad_s;
    if (shadow_mode_) {
      std::ostringstream json;
      json << "{\"mode\":\"SHADOW\",\"transmitted\":false,"
           << "\"frame\":\"LOCAL_NED\",\"monotonic_ns\":"
           << std::chrono::duration_cast<std::chrono::nanoseconds>(
                CommandLease::Clock::now().time_since_epoch()).count()
           << ",\"intent_seq\":" << last_intent_seq_
           << ",\"source_valid\":" << (last_intent_valid_ ? "true" : "false")
           << ",\"intent\":\"" << command.canonical_intent << "\""
           << ",\"gateway_command_valid\":" << (command.accepted ? "true" : "false")
           << ",\"projected_intent\":\"" << projected.canonical_intent << "\""
           << ",\"candidate_intent\":\"" << candidate_intent << "\""
           << ",\"control_frame\":\"BODY_LATERAL_TO_LOCAL_NED\""
           << ",\"heading_rad\":";
      if (std::isfinite(frame.heading_rad) && heading_received_at_ != CommandLease::Clock::time_point{}) {
        json << frame.heading_rad;
      } else {
        json << "null";
      }
      json << ",\"heading_valid\":" << (frame.heading_valid ? "true" : "false")
           << ",\"heading_fresh\":" << (frame.heading_fresh ? "true" : "false")
           << ",\"mapping_reason\":\"" << (projected.accepted ? "OK" : projected.reason) << "\""
           << ",\"projected_seq\":" << projected_seq
           << ",\"projected_valid\":" << (projected.accepted ? "true" : "false")
           << ",\"projected_velocity\":[" << projected.velocity.north_mps << ','
           << projected.velocity.east_mps << ',' << projected.velocity.down_mps << ']'
           << ",\"projected_yawspeed\":" << projected.yaw_rate_ned_rad_s
           << ",\"safety_limiter_state\":\"" << episode_state_name(projected_snapshot.state) << "\""
           << ",\"episode_id\":" << projected_snapshot.episode_id
           << ",\"episode_intent\":\"" << projected_snapshot.episode_intent << "\""
           << ",\"episode_distance_m\":" << projected_snapshot.distance_m
           << ",\"episode_limit_m\":" << projected_snapshot.limit_m
           << ",\"velocity_cap_mps\":" << projected_snapshot.velocity_cap_mps
           << ",\"limit_reached\":" <<
              (projected_snapshot.state == EpisodeState::LIMIT_REACHED ? "true" : "false")
           << ",\"limiter_reason\":\"" << projected_snapshot.reason << "\""
           << ",\"effective_limiter_state\":\"" << episode_state_name(limiter_snapshot.state) << "\""
           << ",\"timed_out\":" << (timed_out ? "true" : "false")
           << ",\"velocity\":[" << setpoint.velocity[0] << ','
           << setpoint.velocity[1] << ',' << setpoint.velocity[2] << ']'
           << ",\"yawspeed\":" << setpoint.yawspeed
           << ",\"offboard_velocity\":true,\"position_control\":false,"
           << "\"position\":null,\"yaw\":null,\"ros_published\":false}";
      const auto payload = json.str();
      shadow_trace_ << payload << '\n';
      shadow_trace_.flush();
      const auto temporary = shadow_snapshot_path_ + ".tmp";
      {
        std::ofstream snapshot(temporary, std::ios::trunc);
        snapshot << payload;
      }
      std::rename(temporary.c_str(), shadow_snapshot_path_.c_str());
      return;
    }
    offboard_mode_pub_->publish(mode);
    trajectory_pub_->publish(setpoint);
  }

  IntentMapper mapper_;
  CommandLease lease_;
  DemoSafetyConfig limiter_config_;
  DemoSafetyLimiter limiter_;
  DemoSafetyLimiter projected_limiter_;
  bool shadow_mode_{false};
  std::string shadow_snapshot_path_;
  std::string shadow_trace_path_;
  std::ofstream shadow_trace_;
  uint64_t last_intent_seq_{0};
  bool last_intent_valid_{false};
  std::string last_intent_reason_;
  drone_control_gateway::msg::Intent::SharedPtr candidate_msg_;
  drone_control_gateway::msg::Intent::SharedPtr last_intent_msg_;
  MappingResult last_mapped_{};
  CommandLease::Clock::time_point last_intent_received_at_{};
  bool trusted_release_pending_{false};
  float heading_rad_{0.0F};
  bool heading_good_for_control_{false};
  bool heading_sample_fresh_{false};
  CommandLease::Clock::time_point heading_received_at_{};
  LocalPositionSample position_{};
  bool status_armed_{false};
  bool status_offboard_{false};
  bool status_failsafe_{true};
  CommandLease::Clock::time_point status_received_at_{};
  uint64_t candidate_seq_{0};
  CommandLease::Clock::time_point candidate_received_at_{};
  std::mutex command_mutex_;
  uint64_t last_logged_seq_{std::numeric_limits<uint64_t>::max()};
  std::string last_logged_intent_{};

  rclcpp::Publisher<px4_msgs::msg::OffboardControlMode>::SharedPtr offboard_mode_pub_;
  rclcpp::Publisher<px4_msgs::msg::TrajectorySetpoint>::SharedPtr trajectory_pub_;
  rclcpp::Subscription<drone_control_gateway::msg::Intent>::SharedPtr intent_sub_;
  rclcpp::Subscription<drone_control_gateway::msg::Intent>::SharedPtr candidate_sub_;
  rclcpp::Subscription<px4_msgs::msg::VehicleLocalPosition>::SharedPtr heading_sub_;
  rclcpp::Subscription<px4_msgs::msg::VehicleStatus>::SharedPtr status_sub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

}  // namespace drone_control_gateway

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<drone_control_gateway::ControlGatewayNode>());
  rclcpp::shutdown();
  return 0;
}
