#include "drone_control_gateway/intent_mapper.hpp"

#include <gtest/gtest.h>

#include <chrono>
#include <cmath>
#include <limits>

using drone_control_gateway::CommandLease;
using drone_control_gateway::ControlFrameContext;
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
  const ControlFrameContext north{0.0F, true, true};
  EXPECT_LT(mapper.map("MOVE_RIGHT", true, 0.0F, 0.0F, north).velocity.east_mps, 0.0F);
  EXPECT_GT(mapper.map("MOVE_LEFT", true, 0.0F, 0.0F, north).velocity.east_mps, 0.0F);
  EXPECT_LT(mapper.map("ASCEND", true, 0.0F).velocity.down_mps, 0.0F);
  EXPECT_GT(mapper.map("DESCEND", true, 0.0F).velocity.down_mps, 0.0F);
  EXPECT_LT(mapper.map("YAW_LEFT", true, 0.0F).yaw_rate_ned_rad_s, 0.0F);
  EXPECT_GT(mapper.map("YAW_RIGHT", true, 0.0F).yaw_rate_ned_rad_s, 0.0F);
}

TEST(IntentMapper, ShadowProjectionUsesProductionBodyToNedMapping)
{
  IntentMapper mapper(MapperConfig{});
  const ControlFrameContext north{0.0F, true, true};
  const auto right = mapper.map("MOVE_RIGHT", true, 0.0F, 0.0F, north);
  const auto left = mapper.map("MOVE_LEFT", true, 0.0F, 0.0F, north);
  const auto ascend = mapper.map("ASCEND", true, 0.0F);
  const auto descend = mapper.map("DESCEND", true, 0.0F);
  EXPECT_FLOAT_EQ(right.velocity.east_mps, -0.8F);
  EXPECT_FLOAT_EQ(left.velocity.east_mps, 0.8F);
  EXPECT_FLOAT_EQ(ascend.velocity.down_mps, -0.5F);
  EXPECT_FLOAT_EQ(descend.velocity.down_mps, 0.5F);
  EXPECT_FLOAT_EQ(right.yaw_rate_ned_rad_s, 0.0F);
  EXPECT_FLOAT_EQ(left.yaw_rate_ned_rad_s, 0.0F);
  // Translational lateral mapping only; no operator orbit/yaw maintenance.
}

TEST(IntentMapper, HeadingMatrixAndOppositeVectors)
{
  IntentMapper mapper(MapperConfig{});
  constexpr float pi = 3.14159265358979323846F;
  struct Row {float heading; float right_n; float right_e;};
  const Row rows[] = {
    {0.0F, 0.0F, -0.8F},
    {pi / 2.0F, 0.8F, 0.0F},
    {pi, 0.0F, 0.8F},
    {-pi / 2.0F, -0.8F, 0.0F},
    {pi / 4.0F, 0.8F / std::sqrt(2.0F), -0.8F / std::sqrt(2.0F)}
  };
  for (const auto & row : rows) {
    const ControlFrameContext frame{row.heading, true, true};
    const auto right = mapper.map("MOVE_RIGHT", true, 0.0F, 0.0F, frame);
    const auto left = mapper.map("MOVE_LEFT", true, 0.0F, 0.0F, frame);
    ASSERT_TRUE(right.accepted);
    ASSERT_TRUE(left.accepted);
    EXPECT_NEAR(right.velocity.north_mps, row.right_n, 1e-5F);
    EXPECT_NEAR(right.velocity.east_mps, row.right_e, 1e-5F);
    EXPECT_NEAR(left.velocity.north_mps, -right.velocity.north_mps, 1e-5F);
    EXPECT_NEAR(left.velocity.east_mps, -right.velocity.east_mps, 1e-5F);
    EXPECT_NEAR(std::hypot(right.velocity.north_mps, right.velocity.east_mps), 0.8F, 1e-5F);
    EXPECT_NEAR(std::hypot(left.velocity.north_mps, left.velocity.east_mps), 0.8F, 1e-5F);
    EXPECT_FLOAT_EQ(right.velocity.down_mps, 0.0F);
    EXPECT_FLOAT_EQ(left.velocity.down_mps, 0.0F);
  }
}

TEST(IntentMapper, InvalidAndStaleHeadingFailClosed)
{
  IntentMapper mapper(MapperConfig{});
  const ControlFrameContext invalid{0.0F, false, true};
  const ControlFrameContext stale{0.0F, true, false};
  const ControlFrameContext nan{std::numeric_limits<float>::quiet_NaN(), true, true};
  for (const auto & frame : {invalid, stale, nan}) {
    for (const auto * intent : {"MOVE_RIGHT", "MOVE_LEFT"}) {
      const auto result = mapper.map(intent, true, 0.0F, 0.0F, frame);
      EXPECT_FALSE(result.accepted);
      EXPECT_EQ(result.canonical_intent, "HOVER");
      EXPECT_FLOAT_EQ(result.velocity.north_mps, 0.0F);
      EXPECT_FLOAT_EQ(result.velocity.east_mps, 0.0F);
      EXPECT_FLOAT_EQ(result.velocity.down_mps, 0.0F);
      EXPECT_FLOAT_EQ(result.yaw_rate_ned_rad_s, 0.0F);
    }
  }
  EXPECT_EQ(mapper.map("MOVE_RIGHT", true, 0.0F, 0.0F, invalid).reason, "HEADING_INVALID");
  EXPECT_EQ(mapper.map("MOVE_RIGHT", true, 0.0F, 0.0F, stale).reason, "HEADING_STALE");
  EXPECT_FLOAT_EQ(mapper.map("ASCEND", true, 0.0F, 0.0F, invalid).velocity.down_mps, -0.5F);
  EXPECT_FLOAT_EQ(mapper.map("DESCEND", true, 0.0F, 0.0F, stale).velocity.down_mps, 0.5F);
}

TEST(IntentMapper, RequestedSpeedIsClamped)
{
  MapperConfig config;
  config.max_horizontal_speed_mps = 1.2F;
  config.max_vertical_speed_mps = 0.6F;
  IntentMapper mapper(config);
  EXPECT_FLOAT_EQ(mapper.map("MOVE_RIGHT", true, 99.0F, 0.0F,
    ControlFrameContext{0.0F, true, true}).velocity.east_mps, -1.2F);
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
