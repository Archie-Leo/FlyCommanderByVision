from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from types import SimpleNamespace

from ground_station.board_snapshot import build_visual_snapshot
from ground_station.coordinate_adapter import RectifiedAnalysisToVideoDisplayAdapter
from ground_station.display_policy import display_state
from ground_station.metadata_receiver import MetadataReceiver
from ground_station.protocol import JOINTS, MAX_PACKET_BYTES, decode_packet, empty_snapshot, encode_packet
from ground_station.sender import MetadataSender, encode_with_optional_limiter


class Map:
    shape = (960, 1280)

    def __init__(self, axis, offset=0):
        self.axis, self.offset = axis, offset

    def __getitem__(self, index):
        y, x = index
        return (x if self.axis == "x" else y) + self.offset


class GroundStationTests(unittest.TestCase):
    def test_live_command_metadata_reports_blocked_zero_output(self):
        receiver = MetadataReceiver("127.0.0.1", 0)
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder) / "gateway_live.json"
            snapshot.write_text(json.dumps({
                "mode": "LIVE", "ros_published": True,
                "monotonic_ns": time.monotonic_ns(),
                "intent": "HOVER", "frame": "LOCAL_NED",
                "velocity": [0, 0, 0], "yawspeed": 0.0,
                "safety_limiter_state": "IDLE", "episode_id": 0,
                "episode_distance_m": 0, "episode_limit_m": 0.5,
                "velocity_cap_mps": 0.3,
            }), encoding="utf-8")
            sender = MetadataSender("127.0.0.1", receiver.socket.getsockname()[1],
                                    20, command_file=snapshot, command_mode="LIVE",
                                    authority_snapshot=lambda: {
                                        "flight_authority_enabled": False,
                                        "authority_transition_reason": "BLOCKED_DISARMED"})
            try:
                deadline = time.monotonic() + 0.4
                command = None
                while time.monotonic() < deadline:
                    packet = receiver.snapshot()[0]
                    command = (packet or {}).get("command")
                    if command and command.get("fresh"):
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(command)
                self.assertTrue(command["fresh"])
                self.assertTrue(command["transmitted"])
                self.assertEqual(command["authority"], "BLOCKED")
                self.assertEqual(command["velocity"], [0, 0, 0])
            finally:
                sender.close()
                receiver.close()

    def test_optional_limiter_never_drops_existing_metadata_packet(self):
        packet = empty_snapshot()
        packet["command"] = {"mode": "SHADOW", "pad": "x" * 600}
        base_size = len(encode_packet(packet))
        packet["command"]["pad"] += "x" * (MAX_PACKET_BYTES - base_size - 5)
        self.assertLessEqual(len(encode_packet(packet)), MAX_PACKET_BYTES)
        packet["command"]["limiter"] = ["L", 3, 0.5, 0.5, 0.3]
        encoded = encode_with_optional_limiter(packet)
        self.assertNotIn("limiter", decode_packet(encoded)["command"])

    def test_coordinate_center_edges_and_rotation(self):
        identity = RectifiedAnalysisToVideoDisplayAdapter(Map("x"), Map("y"))
        for point in ((640.,480.), (1.,1.), (1278.,958.)):
            self.assertEqual(identity.point(*point), point)
        shifted = RectifiedAnalysisToVideoDisplayAdapter(Map("x",10), Map("y",5))
        self.assertEqual(shifted.point(500.,500.), (490.,495.))
        self.assertEqual(shifted.bbox((100.,100.,201.,301.)),
                         (90.,95.,190.,295.))
        self.assertIsNone(identity.point(-1, 20))

    def test_no_operator_and_locked_packet_size(self):
        empty = empty_snapshot()
        self.assertEqual(decode_packet(encode_packet(empty))["operator"]["state"], "WAIT_OPERATOR")
        pose = SimpleNamespace(frame_id=12, poses=[], timestamp_ms=100)
        packet = build_visual_snapshot(pose, [], {"ownership_state":"WAIT_OPERATOR"}, 40,
            RectifiedAnalysisToVideoDisplayAdapter(Map("x"), Map("y")))
        self.assertIsNone(packet["operator"]["bbox"])
        joints = {name: SimpleNamespace(x_px=200.+i*10,y_px=300.+i*10,
                   confidence=.9,valid=True) for i,name in enumerate(JOINTS)}
        person_pose = SimpleNamespace(local_detection_id=2,bbox_xyxy=(100.,100.,500.,700.),
                                      pose_score=.92,joints=joints)
        pose.poses = [person_pose]
        observed = SimpleNamespace(track_id=7,skeleton=SimpleNamespace(local_detection_id=2),
                                   depth=SimpleNamespace(depth_m=2.14,depth_quality=.8,
                                                         available=True))
        record = {"ownership_state":"LOCKED_HIGH","operator_session_id":"a"*36,
                  "current_track_id":7,"gesture_raw":{"label":"RIGHT"},
                  "gesture_stable":{"label":"RIGHT"},
                  "depth_observations":[{"track_id":7,"age_ms":23}]}
        packet = build_visual_snapshot(pose,[observed],record,40,
            RectifiedAnalysisToVideoDisplayAdapter(Map("x"),Map("y")))
        self.assertEqual(packet["operator"]["kind"],"operator")
        self.assertEqual(len(packet["operator"]["keypoints"]),len(JOINTS))
        self.assertLess(len(encode_packet(packet)),1400)
        packet["stage6"] = {"intent":"HOVER","valid":False,
                            "reason":"OPERATOR_LOST","lease":"INACTIVE"}
        self.assertFalse(decode_packet(encode_packet(packet))["stage6"]["valid"])

    def test_display_stale_and_lost_clears_person(self):
        packet = empty_snapshot()
        packet["ai_age_ms"] = 80
        packet["operator"].update(state="LOCKED_HIGH",kind="operator",bbox=[1,2,3,4])
        packet["gesture"]["stable"] = "RIGHT"
        self.assertTrue(display_state(packet,10)["draw_person"])
        self.assertEqual(display_state(packet,510)["gesture"],"STALE")
        self.assertFalse(display_state(packet,1010)["draw_person"])
        packet["ai_age_ms"] = 1100
        self.assertFalse(display_state(packet,10)["draw_person"])
        packet["ai_age_ms"] = 80
        packet["operator"]["state"] = "OPERATOR_LOST"
        self.assertFalse(display_state(packet,10)["draw_person"])

    def test_udp_latest_recovery_and_sender_absent_receiver(self):
        receiver = MetadataReceiver("127.0.0.1",0)
        address = receiver.socket.getsockname()
        tx = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            for sequence in (10,12,1):
                packet = empty_snapshot();packet["sequence"] = sequence
                tx.sendto(encode_packet(packet),address)
            deadline = time.monotonic()+1
            while receiver.snapshot()[2]["received"] < 3 and time.monotonic()<deadline:
                time.sleep(.01)
            latest,age,stats=receiver.snapshot()
            self.assertEqual(latest["sequence"],1)
            self.assertEqual(stats["gaps"],1)
            self.assertLess(age,500)
        finally:
            tx.close();receiver.close()
        sender = MetadataSender("127.0.0.1",address[1],20)
        try:
            time.sleep(.16)
            self.assertGreaterEqual(sender.status()["sent"],2)
            self.assertEqual(sender.status()["oversize"],0)
        finally:
            sender.close()


if __name__ == "__main__":
    unittest.main()
