#include "drone_control_gateway/intent_mapper.hpp"
#include "drone_control_gateway/msg/intent.hpp"

#include <px4_msgs/msg/offboard_control_mode.hpp>
#include <px4_msgs/msg/trajectory_setpoint.hpp>
#include <rclcpp/rclcpp.hpp>

#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <mutex>
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
        declare_parameter<double>("command_timeout_s", 0.5)))
  {
    offboard_mode_pub_ = create_publisher<px4_msgs::msg::OffboardControlMode>(
      "/fmu/in/offboard_control_mode", 10);
    trajectory_pub_ = create_publisher<px4_msgs::msg::TrajectorySetpoint>(
      "/fmu/in/trajectory_setpoint", 10);
    intent_sub_ = create_subscription<drone_control_gateway::msg::Intent>(
      "/interaction/intent", rclcpp::QoS(10).reliable(),
      std::bind(&ControlGatewayNode::on_intent, this, std::placeholders::_1));

    const double heartbeat_hz = declare_parameter<double>("heartbeat_hz", 10.0);
    if (!std::isfinite(heartbeat_hz) || heartbeat_hz <= 2.0) {
      throw std::invalid_argument("heartbeat_hz must be finite and > 2 Hz");
    }
    const auto period = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::duration<double>(1.0 / heartbeat_hz));
    timer_ = create_wall_timer(period, std::bind(&ControlGatewayNode::publish_cycle, this));

    RCLCPP_INFO(
      get_logger(),
      "Gateway ready in HOVER. World NED: FORWARD=+X North, BACKWARD=-X South, "
      "RIGHT=+Y East, LEFT=-Y West, ASCEND=-Z, DESCEND=+Z, "
      "YAW_RIGHT=+rate clockwise, YAW_LEFT=-rate. No auto ARM/OFFBOARD/TAKEOFF/LAND.");
  }

private:
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

  void on_intent(const drone_control_gateway::msg::Intent::SharedPtr msg)
  {
    const MappingResult mapped = mapper_.map(
      msg->intent, msg->valid, msg->requested_speed_m_s, msg->requested_yaw_rate_rad_s);
    bool should_log = false;
    {
      std::lock_guard<std::mutex> lock(command_mutex_);
      lease_.update(mapped, CommandLease::Clock::now());
      should_log = msg->seq != last_logged_seq_ ||
        mapped.canonical_intent != last_logged_intent_ || !mapped.accepted;
      last_logged_seq_ = msg->seq;
      last_logged_intent_ = mapped.canonical_intent;
    }
    if (!should_log) {
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
    bool timed_out = false;
    {
      std::lock_guard<std::mutex> lock(command_mutex_);
      command = lease_.current(CommandLease::Clock::now(), &timed_out);
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
    offboard_mode_pub_->publish(mode);

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
    trajectory_pub_->publish(setpoint);
  }

  IntentMapper mapper_;
  CommandLease lease_;
  std::mutex command_mutex_;
  uint64_t last_logged_seq_{std::numeric_limits<uint64_t>::max()};
  std::string last_logged_intent_{};

  rclcpp::Publisher<px4_msgs::msg::OffboardControlMode>::SharedPtr offboard_mode_pub_;
  rclcpp::Publisher<px4_msgs::msg::TrajectorySetpoint>::SharedPtr trajectory_pub_;
  rclcpp::Subscription<drone_control_gateway::msg::Intent>::SharedPtr intent_sub_;
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
