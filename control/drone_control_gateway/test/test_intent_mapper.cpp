#include "drone_control_gateway/intent_mapper.hpp"

#include <gtest/gtest.h>

#include <chrono>

using drone_control_gateway::CommandLease;
using drone_control_gateway::IntentMapper;
using drone_control_gateway::MapperConfig;

TEST(IntentMapper, HoverAndInvalidAreZero)
{
  IntentMapper mapper(MapperConfig{});
  const auto hover = mapper.map("HOVER", true, 0.0F);
  EXPECT_TRUE(hover.accepted);
  EXPECT_FLOAT_EQ(hover.velocity.north_mps, 0.0F);
  EXPECT_FLOAT_EQ(hover.velocity.east_mps, 0.0F);
  EXPECT_FLOAT_EQ(hover.velocity.down_mps, 0.0F);
  const auto invalid = mapper.map("MOVE_RIGHT", false, 1.0F);
  EXPECT_FALSE(invalid.accepted);
  EXPECT_FLOAT_EQ(invalid.velocity.east_mps, 0.0F);
  EXPECT_FLOAT_EQ(hover.yaw_rate_ned_rad_s, 0.0F);
  EXPECT_FLOAT_EQ(invalid.yaw_rate_ned_rad_s, 0.0F);
}

TEST(IntentMapper, WorldNedDirectionsAreExplicit)
{
  IntentMapper mapper(MapperConfig{});
  EXPECT_GT(mapper.map("MOVE_FORWARD", true, 0.0F).velocity.north_mps, 0.0F);
  EXPECT_LT(mapper.map("MOVE_BACKWARD", true, 0.0F).velocity.north_mps, 0.0F);
  EXPECT_GT(mapper.map("MOVE_RIGHT", true, 0.0F).velocity.east_mps, 0.0F);
  EXPECT_LT(mapper.map("MOVE_LEFT", true, 0.0F).velocity.east_mps, 0.0F);
  EXPECT_LT(mapper.map("ASCEND", true, 0.0F).velocity.down_mps, 0.0F);
  EXPECT_GT(mapper.map("DESCEND", true, 0.0F).velocity.down_mps, 0.0F);
  EXPECT_LT(mapper.map("YAW_LEFT", true, 0.0F).yaw_rate_ned_rad_s, 0.0F);
  EXPECT_GT(mapper.map("YAW_RIGHT", true, 0.0F).yaw_rate_ned_rad_s, 0.0F);
}

TEST(IntentMapper, RequestedSpeedIsClamped)
{
  MapperConfig config;
  config.max_horizontal_speed_mps = 1.2F;
  config.max_vertical_speed_mps = 0.6F;
  IntentMapper mapper(config);
  EXPECT_FLOAT_EQ(mapper.map("MOVE_RIGHT", true, 99.0F).velocity.east_mps, 1.2F);
  EXPECT_FLOAT_EQ(mapper.map("ASCEND", true, 99.0F).velocity.down_mps, -0.6F);
  config.max_yaw_rate_rad_s = 0.45F;
  IntentMapper yaw_mapper(config);
  EXPECT_FLOAT_EQ(yaw_mapper.map("YAW_RIGHT", true, 0.0F, 99.0F).yaw_rate_ned_rad_s, 0.45F);
  EXPECT_FLOAT_EQ(yaw_mapper.map("YAW_LEFT", true, 0.0F, 99.0F).yaw_rate_ned_rad_s, -0.45F);
}

TEST(IntentMapper, UnsupportedCommandFailsClosed)
{
  IntentMapper mapper(MapperConfig{});
  const auto result = mapper.map("TAKEOFF", true, 0.0F);
  EXPECT_FALSE(result.accepted);
  EXPECT_EQ(result.canonical_intent, "HOVER");
  EXPECT_FLOAT_EQ(result.velocity.east_mps, 0.0F);
  EXPECT_FLOAT_EQ(result.yaw_rate_ned_rad_s, 0.0F);
}

TEST(IntentMapper, TimeoutClearsLinearVelocityAndYawRate)
{
  using namespace std::chrono_literals;
  IntentMapper mapper(MapperConfig{});
  CommandLease lease(std::chrono::duration<double>(0.5));
  const auto start = CommandLease::Clock::time_point{};
  lease.update(mapper.map("YAW_RIGHT", true, 0.0F), start);
  bool timed_out = false;
  EXPECT_GT(lease.current(start + 100ms, &timed_out).yaw_rate_ned_rad_s, 0.0F);
  EXPECT_FALSE(timed_out);
  const auto safe = lease.current(start + 600ms, &timed_out);
  EXPECT_TRUE(timed_out);
  EXPECT_FLOAT_EQ(safe.velocity.north_mps, 0.0F);
  EXPECT_FLOAT_EQ(safe.velocity.east_mps, 0.0F);
  EXPECT_FLOAT_EQ(safe.velocity.down_mps, 0.0F);
  EXPECT_FLOAT_EQ(safe.yaw_rate_ned_rad_s, 0.0F);
}
