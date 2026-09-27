#include "drone_control_gateway/demo_safety_limiter.hpp"

#include <gtest/gtest.h>

#include <chrono>
#include <cmath>
#include <limits>

using drone_control_gateway::CommandLease;
using drone_control_gateway::ControlFrameContext;
using drone_control_gateway::DemoSafetyLimiter;
using drone_control_gateway::EpisodeState;
using drone_control_gateway::IntentMapper;
using drone_control_gateway::LocalPositionSample;
using drone_control_gateway::MapperConfig;

namespace
{
using namespace std::chrono_literals;
using Clock = CommandLease::Clock;

struct Harness
{
  Clock::time_point now{Clock::time_point{} + 1s};
  LocalPositionSample position{};
  IntentMapper mapper{MapperConfig{}};
  DemoSafetyLimiter limiter{};

  Harness()
  {
    position.xy_valid = true;
    position.z_valid = true;
    position.source_fresh = true;
    position.received_at = now;
    position.z = -1.0F;
  }

  drone_control_gateway::MappingResult step(
    const char * intent, float x, float y, float z, bool valid = true,
    bool authority = true, uint64_t session = 1, float heading = 0.0F)
  {
    now += 100ms;
    position.x = x;
    position.y = y;
    position.z = z;
    position.received_at = now;
    const auto desired = mapper.map(intent, valid, 0.0F, 0.0F,
      ControlFrameContext{heading, true, true});
    return limiter.apply(intent, valid, session, authority, desired, position, now);
  }
};

void expect_zero(const drone_control_gateway::MappingResult & result)
{
  EXPECT_EQ(result.canonical_intent, "HOVER");
  EXPECT_FLOAT_EQ(result.velocity.north_mps, 0.0F);
  EXPECT_FLOAT_EQ(result.velocity.east_mps, 0.0F);
  EXPECT_FLOAT_EQ(result.velocity.down_mps, 0.0F);
  EXPECT_FLOAT_EQ(result.yaw_rate_ned_rad_s, 0.0F);
}
}  // namespace

TEST(DemoSafetyLimiter, RightAndLeftMeasuredDistanceLimitAndHeldNoRestart)
{
  for (const char * intent : {"MOVE_RIGHT", "MOVE_LEFT"}) {
    Harness h;
    const auto first = h.step(intent, 0, 0, -1);
    ASSERT_EQ(h.limiter.snapshot().state, EpisodeState::ACTIVE);
    EXPECT_NE(first.canonical_intent, "HOVER");
    const auto episode_id = h.limiter.snapshot().episode_id;
    for (float x : {0.10F, 0.20F, 0.30F, 0.40F}) {
      EXPECT_NE(h.step(intent, x, 0, -1).canonical_intent, "HOVER");
      EXPECT_NEAR(h.limiter.snapshot().distance_m, x, 1e-5F);
      EXPECT_EQ(h.limiter.snapshot().episode_id, episode_id);
    }
    expect_zero(h.step(intent, 0.50F, 0, -1));
    EXPECT_EQ(h.limiter.snapshot().state, EpisodeState::LIMIT_REACHED);
    EXPECT_EQ(h.limiter.snapshot().reason, "EPISODE_DISTANCE_LIMIT");
    for (int i = 0; i < 5; ++i) {
      expect_zero(h.step(intent, 0.50F, 0, -1));
      EXPECT_EQ(h.limiter.snapshot().episode_id, episode_id);
      EXPECT_EQ(h.limiter.snapshot().state, EpisodeState::LIMIT_REACHED);
    }
    expect_zero(h.step("HOVER", 0.50F, 0, -1));
    EXPECT_NE(h.step(intent, 0.50F, 0, -1).canonical_intent, "HOVER");
    EXPECT_GT(h.limiter.snapshot().episode_id, episode_id);
    EXPECT_FLOAT_EQ(h.limiter.snapshot().distance_m, 0.0F);
  }
}

TEST(DemoSafetyLimiter, GestureSwitchStartsNewBaseline)
{
  Harness h;
  h.step("MOVE_RIGHT", 0, 0, -1);
  h.step("MOVE_RIGHT", 0.30F, 0, -1);
  const auto first_id = h.limiter.snapshot().episode_id;
  EXPECT_EQ(h.step("MOVE_LEFT", 0.30F, 0, -1).canonical_intent, "MOVE_LEFT");
  EXPECT_GT(h.limiter.snapshot().episode_id, first_id);
  EXPECT_FLOAT_EQ(h.limiter.snapshot().distance_m, 0.0F);
}

TEST(DemoSafetyLimiter, AscendAndDescendUseNedZMagnitude)
{
  for (const char * intent : {"ASCEND", "DESCEND"}) {
    Harness h;
    h.step(intent, 0, 0, -1);
    for (float delta : {0.10F, 0.20F, 0.30F, 0.40F}) {
      const float z = intent[0] == 'A' ? -1.0F - delta : -1.0F + delta;
      EXPECT_NE(h.step(intent, 0, 0, z).canonical_intent, "HOVER");
      EXPECT_NEAR(h.limiter.snapshot().distance_m, delta, 1e-5F);
    }
    expect_zero(h.step(intent, 0, 0, intent[0] == 'A' ? -1.50F : -0.50F));
    EXPECT_EQ(h.limiter.snapshot().state, EpisodeState::LIMIT_REACHED);
  }
}

TEST(DemoSafetyLimiter, InvalidStaleAndNanPositionBlockHeldEpisode)
{
  for (int kind = 0; kind < 3; ++kind) {
    Harness h;
    h.step("MOVE_RIGHT", 0, 0, -1);
    h.now += 100ms;
    if (kind == 0) {h.position.xy_valid = false;}
    if (kind == 1) {h.position.received_at = h.now - 600ms;}
    if (kind == 2) {h.position.x = std::numeric_limits<float>::quiet_NaN();}
    const auto desired = h.mapper.map("MOVE_RIGHT", true, 0.0F, 0.0F,
      ControlFrameContext{0.0F, true, true});
    expect_zero(h.limiter.apply("MOVE_RIGHT", true, 1, true, desired, h.position, h.now));
    EXPECT_EQ(h.limiter.snapshot().state, EpisodeState::BLOCKED_INVALID_POSITION);
    h.position.xy_valid = true;
    h.position.x = 0.0F;
    h.position.received_at = h.now;
    expect_zero(h.limiter.apply("MOVE_RIGHT", true, 1, true, desired, h.position, h.now));
  }
}

TEST(DemoSafetyLimiter, CapsPreserveBodyToNedDirection)
{
  constexpr float pi = 3.14159265358979323846F;
  for (float heading : {0.0F, pi / 4.0F, pi / 2.0F}) {
    for (const char * intent : {"MOVE_RIGHT", "MOVE_LEFT"}) {
      Harness h;
      const auto result = h.step(intent, 0, 0, -1, true, true, 1, heading);
      ASSERT_EQ(result.canonical_intent, intent);
      EXPECT_NEAR(std::hypot(result.velocity.north_mps, result.velocity.east_mps),
        0.30F, 1e-5F);
      if (heading == 0.0F) {
        EXPECT_EQ(std::signbit(result.velocity.east_mps),
          std::string(intent) == "MOVE_RIGHT");
      }
    }
  }
  Harness ascend;
  EXPECT_FLOAT_EQ(ascend.step("ASCEND", 0, 0, -1).velocity.down_mps, -0.20F);
  Harness descend;
  EXPECT_FLOAT_EQ(descend.step("DESCEND", 0, 0, -1).velocity.down_mps, 0.20F);
}

TEST(DemoSafetyLimiter, AuthorityExitRequiresFreshNeutralBeforeHeldMovement)
{
  Harness h;
  h.step("MOVE_RIGHT", 0, 0, -1);
  expect_zero(h.step("MOVE_RIGHT", 0, 0, -1, true, false));
  expect_zero(h.step("MOVE_RIGHT", 0, 0, -1));
  expect_zero(h.step("UNKNOWN", 0, 0, -1, false));
  expect_zero(h.step("MOVE_RIGHT", 0, 0, -1));
  expect_zero(h.step("HOVER", 0, 0, -1));
  EXPECT_EQ(h.step("MOVE_RIGHT", 0, 0, -1).canonical_intent, "MOVE_RIGHT");
}

TEST(DemoSafetyLimiter, Stage6TrustedReleaseEventUnlocksAfterAuthorityReentry)
{
  Harness h;
  h.step("MOVE_RIGHT", 0, 0, -1);
  h.step("MOVE_RIGHT", 0, 0, -1, true, false);
  h.step("MOVE_RIGHT", 0, 0, -1);
  const auto invalid_hover = h.mapper.map("HOVER", false, 0.0F);
  expect_zero(h.limiter.apply("HOVER", false, 1, true, invalid_hover,
    h.position, h.now, true));
  EXPECT_EQ(h.step("MOVE_RIGHT", 0, 0, -1).canonical_intent, "MOVE_RIGHT");
}

TEST(DemoSafetyLimiter, SessionChangeInvalidatesEpisode)
{
  Harness h;
  h.step("MOVE_RIGHT", 0, 0, -1);
  const auto episode_id = h.limiter.snapshot().episode_id;
  expect_zero(h.step("MOVE_RIGHT", 0, 0, -1, true, true, 2));
  EXPECT_EQ(h.limiter.snapshot().state, EpisodeState::IDLE);
  EXPECT_EQ(h.limiter.snapshot().episode_id, episode_id);
  expect_zero(h.step("MOVE_RIGHT", 0, 0, -1, true, true, 2));
  expect_zero(h.step("HOVER", 0, 0, -1, true, true, 2));
  EXPECT_EQ(h.step("MOVE_RIGHT", 0, 0, -1, true, true, 2).canonical_intent,
    "MOVE_RIGHT");
  EXPECT_GT(h.limiter.snapshot().episode_id, episode_id);
}

TEST(DemoSafetyLimiter, InitialMissingCandidateDoesNotPoisonShadowProjection)
{
  Harness h;
  const auto invalid = h.mapper.map("HOVER", false, 0.0F);
  expect_zero(h.limiter.apply("UNKNOWN", false, 0, true, invalid, h.position, h.now));
  EXPECT_EQ(h.step("MOVE_RIGHT", 0, 0, -1).canonical_intent, "MOVE_RIGHT");
}

TEST(DemoSafetyLimiter, VisionTimeoutAndOperatorLostCloseEpisode)
{
  Harness h;
  h.step("MOVE_RIGHT", 0, 0, -1);
  expect_zero(h.step("UNKNOWN", 0, 0, -1, false));
  EXPECT_EQ(h.limiter.snapshot().state, EpisodeState::IDLE);
  h.step("MOVE_RIGHT", 0, 0, -1);
  expect_zero(h.step("HOVER", 0, 0, -1, false));
  EXPECT_EQ(h.limiter.snapshot().state, EpisodeState::IDLE);
}
