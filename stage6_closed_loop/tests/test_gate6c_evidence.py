from __future__ import annotations

import io
import json
import math
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import rclpy
from drone_control_gateway.msg import Intent
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions

from analyze_gate6c_run import (EXPECTED, analyze, check_movement_release,
                                 check_video_alignment,
                                 evaluate_episode)
from stage6.gate6c_recorder import Gate6CRecorder
from stage6.px4_observer import (Gate6CObserver, local_position_fields, trajectory_fields,
                                  vehicle_status_fields)
from stage6.run_context import RunContext, json_safe, write_jsonl


def evidence_for(label):
    gesture, axis, setpoint_axis, sign = EXPECTED[label]
    episode = {"intent": label, "start_ms": 1000, "end_ms": 1800,
               "operator_session_id": "s1", "intent_events": 8}
    vision = [{"run_elapsed_ms": 1100, "operator_session_id": "s1",
               "gesture_stable": {"label": gesture},
               "authorized_gesture": {"gesture": gesture, "valid": True}}]
    gateway = [{"run_elapsed_ms": 1100, "event_type": "received_intent",
                "topic": "/interaction/intent", "received_intent": label,
                "intent_valid": True}]
    for t in (1200, 1450, 1700):
        fields = {"trajectory_velocity_x": 0.0,
                  "trajectory_velocity_y": 0.0,
                  "trajectory_velocity_z": 0.0}
        if setpoint_axis:
            fields[setpoint_axis] = sign*.8
        gateway.append({"run_elapsed_ms": t, "event_type": "trajectory_setpoint",
                        **fields})
    px4 = [{"run_elapsed_ms": 1300, "event_type": "vehicle_status",
            "armed": True, "nav_state": 14, "failsafe": False}]
    for index, t in enumerate((1350, 1600, 1900, 2200)):
        position = {"x": 0., "y": 0., "z": 0.,
                    "vx": 0., "vy": 0., "vz": 0.,
                    "xy_valid": True, "z_valid": True,
                    "v_xy_valid": True, "v_z_valid": True}
        if axis:
            position[axis] = sign*index*.18
            position["v"+axis] = sign*.45
        px4.append({"run_elapsed_ms": t, "event_type": "local_position",
                    **position})
    return episode, vision, gateway, px4


class Gate6CEvidenceTests(unittest.TestCase):
    def test_run_elapsed_monotonic(self):
        with tempfile.TemporaryDirectory() as temp:
            run = RunContext(Path(temp), t0_monotonic_ns=1_000_000_000)
            self.assertEqual(run.stamp(1_100_000_000)["run_elapsed_ms"], 100)
            self.assertLess(run.stamp(1_100_000_000)["run_elapsed_ms"],
                            run.stamp(1_200_000_000)["run_elapsed_ms"])

    def test_timestamp_before_run_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                RunContext(Path(temp),t0_monotonic_ns=100).stamp(99)

    def test_nonfinite_json_is_null_and_flagged(self):
        output = io.StringIO()
        write_jsonl(output,{"velocity":[math.nan, math.inf, -.4]})
        row = json.loads(output.getvalue())
        self.assertEqual(row["velocity"],[None,None,-.4])
        self.assertEqual(row["nonfinite_fields"],["velocity[0]","velocity[1]"])

    def test_local_position_serialization(self):
        msg = SimpleNamespace(timestamp=123,timestamp_sample=120,x=1.,y=2.,z=-3.,
                              vx=.1,vy=.2,vz=-.3,xy_valid=True,z_valid=True,
                              v_xy_valid=True,v_z_valid=False)
        row = local_position_fields(msg)
        self.assertEqual((row["y"],row["z"],row["px4_source_timestamp_us"]),(2.,-3.,123))
        self.assertFalse(row["v_z_valid"])

    def test_vehicle_status_serialization(self):
        msg = SimpleNamespace(timestamp=123,arming_state=2,ARMING_STATE_ARMED=2,
                              nav_state=14,failsafe=False)
        self.assertTrue(vehicle_status_fields(msg)["armed"])

    def test_trajectory_nan_is_not_finite_fake(self):
        msg = SimpleNamespace(timestamp=123,position=[math.nan]*3,
                              velocity=[0.,.8,0.],yawspeed=math.nan)
        clean,bad = json_safe(trajectory_fields(msg))
        self.assertIsNone(clean["trajectory_position"][0])
        self.assertIn("yawspeed",bad)

    def test_right_expected_positive_y(self):
        self.assertEqual(EXPECTED["MOVE_RIGHT"],("RIGHT","y","trajectory_velocity_y",1))
        self.assertEqual(evaluate_episode(*evidence_for("MOVE_RIGHT"),live_mode=True)["result"],"PASS")

    def test_left_expected_negative_y(self):
        self.assertEqual(EXPECTED["MOVE_LEFT"],("LEFT","y","trajectory_velocity_y",-1))
        self.assertEqual(evaluate_episode(*evidence_for("MOVE_LEFT"),live_mode=True)["result"],"PASS")

    def test_ascend_expected_negative_z(self):
        self.assertEqual(EXPECTED["ASCEND"],("ASCEND","z","trajectory_velocity_z",-1))
        self.assertEqual(evaluate_episode(*evidence_for("ASCEND"),live_mode=True)["result"],"PASS")

    def test_descend_expected_positive_z(self):
        self.assertEqual(EXPECTED["DESCEND"],("DESCEND","z","trajectory_velocity_z",1))
        self.assertEqual(evaluate_episode(*evidence_for("DESCEND"),live_mode=True)["result"],"PASS")

    def test_hover_requires_zero_setpoint_and_low_speed(self):
        episode,vision,gateway,px4 = evidence_for("HOVER")
        self.assertEqual(evaluate_episode(episode,vision,gateway,px4,live_mode=True)["result"],"PASS")
        gateway[1]["trajectory_velocity_y"] = .8
        self.assertEqual(evaluate_episode(episode,vision,gateway,px4,live_mode=True)["result"],"FAIL")

    def test_missing_px4_is_insufficient_not_pass(self):
        episode,vision,gateway,_ = evidence_for("MOVE_RIGHT")
        result = evaluate_episode(episode,vision,gateway,[],live_mode=True)
        self.assertEqual(result["result"],"INSUFFICIENT_EVIDENCE")

    def test_dry_run_cannot_claim_aircraft_pass(self):
        episode,vision,gateway,px4 = evidence_for("MOVE_RIGHT")
        self.assertEqual(evaluate_episode(episode,vision,gateway,px4,
                                          live_mode=False)["result"],"INSUFFICIENT_EVIDENCE")

    def test_video_frame_log_matches_vision_and_closes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);recorder=Gate6CRecorder(root,playback_fps=10)
            frame=np.zeros((48,64,3),dtype=np.uint8)
            recorder.start(frame,{"run_t0_monotonic_ns":100})
            with (root/"vision_frames.jsonl").open("w") as out:
                for index in range(3):
                    row={"frame_id":index,"capture_monotonic_ns":100+index,
                         "video_frame_index":index}
                    recorder.write(frame,frame,row,100+index)
                    write_jsonl(out,row)
            clip=recorder.stop("TEST_END")
            self.assertEqual(clip["status"],"COMPLETE")
            self.assertFalse(recorder.active)
            vision=[json.loads(s) for s in (root/"vision_frames.jsonl").read_text().splitlines()]
            ok,reason,count=check_video_alignment(root,vision,[clip])
            self.assertEqual((ok,reason,count),(True,None,3))

    def test_video_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            ok,reason,_=check_video_alignment(Path(temp),[],[])
            self.assertFalse(ok)
            self.assertEqual(reason,"ANNOTATED_VIDEO_MISSING")

    def test_analysis_rejects_dry_run_even_with_synthetic_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            RunContext(root,t0_monotonic_ns=100).manifest(
                status="COMPLETE",intent_topic="/interaction/intent_dry_run",
                gateway_topic_explicit=False,recordings=[])
            (root/"intent_events.jsonl").write_text(
                json.dumps({"run_elapsed_ms":1000,"intent":"MOVE_RIGHT",
                            "valid":True,"operator_session_id":"s1"})+"\n")
            self.assertEqual(analyze(root)["gate6c_result"],"INSUFFICIENT_EVIDENCE")

    def test_observer_receives_intent_before_log_close(self):
        with tempfile.TemporaryDirectory() as temp:
            rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
            gateway_log=io.StringIO();px4_log=io.StringIO()
            observer=Gate6CObserver(RunContext(Path(temp)),gateway_log,px4_log,
                                    intent_topic="/interaction/intent_dry_run")
            source=Node("stage6_gate6c_observer_test_source")
            publisher=source.create_publisher(Intent,"/interaction/intent_dry_run",10)
            executor=SingleThreadedExecutor();executor.add_node(observer);executor.add_node(source)
            try:
                end=time.monotonic()+.15
                while time.monotonic()<end:executor.spin_once(timeout_sec=.01)
                message=Intent();message.intent="MOVE_RIGHT";message.valid=True
                message.reason="TEST";publisher.publish(message)
                end=time.monotonic()+.15
                while time.monotonic()<end:executor.spin_once(timeout_sec=.01)
                rows=[json.loads(s) for s in gateway_log.getvalue().splitlines()]
                self.assertTrue(any(row.get("received_intent")=="MOVE_RIGHT"
                                    and row.get("intent_valid") for row in rows))
                self.assertTrue(all(row.get("run_elapsed_ms",-1)>=0 for row in rows))
            finally:
                executor.shutdown();source.destroy_node();observer.destroy_node()
                gateway_log.close();px4_log.close();rclpy.try_shutdown()

    def test_release_requires_invalid_hover_and_zero_setpoint(self):
        episode={"intent":"MOVE_RIGHT","end_ms":1000}
        intent=[{"run_elapsed_ms":1100,"intent":"HOVER","valid":False}]
        gateway=[{"run_elapsed_ms":1200,"event_type":"trajectory_setpoint",
                  "trajectory_velocity_x":0.,"trajectory_velocity_y":0.,
                  "trajectory_velocity_z":0.},
                 {"run_elapsed_ms":1300,"event_type":"trajectory_setpoint",
                  "trajectory_velocity_x":0.,"trajectory_velocity_y":0.,
                  "trajectory_velocity_z":0.}]
        self.assertTrue(check_movement_release([episode],intent,gateway)[0])
        gateway[1]["trajectory_velocity_y"] = .8
        self.assertFalse(check_movement_release([episode],intent,gateway)[0])

    def test_release_missing_is_insufficient(self):
        self.assertIn("RELEASE_HOVER_MISSING",
                      check_movement_release([{"intent":"MOVE_LEFT","end_ms":1000}],[],[])[1][0])


class RosbagScriptTests(unittest.TestCase):
    def test_script_checks_topic_types_and_records_only_present(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run=root/"run";run.mkdir();bin_dir=root/"bin";bin_dir.mkdir()
            mock=bin_dir/"ros2"
            mock.write_text("#!/usr/bin/env bash\n"
                "if [[ $1 == topic && $2 == type ]]; then\n"
                "  case $3 in\n"
                "    /interaction/intent_dry_run) echo drone_control_gateway/msg/Intent;;\n"
                "    /fmu/out/vehicle_local_position) echo px4_msgs/msg/VehicleLocalPosition;;\n"
                "    *) exit 1;;\n"
                "  esac\n"
                "elif [[ $1 == bag && $2 == record ]]; then\n"
                "  mkdir -p \"$4\"; touch \"$4/metadata.yaml\"\n"
                "else exit 1; fi\n")
            mock.chmod(0o755)
            script=Path(__file__).resolve().parents[1]/"scripts"/"record_gate6c_rosbag.sh"
            env={**os.environ,"PATH":str(bin_dir)+os.pathsep+os.environ["PATH"]}
            proc=subprocess.run(["bash",str(script),str(run),"dry-run"],
                                env=env,capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            topics=(run/"rosbag_topics.txt").read_text().splitlines()
            self.assertIn("/interaction/intent_dry_run",topics)
            self.assertIn("/fmu/out/vehicle_local_position",topics)
            self.assertNotIn("/fmu/in/trajectory_setpoint",topics)

    def test_script_refuses_live_without_gateway_topics(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run=root/"run";run.mkdir()
            bin_dir=root/"bin";bin_dir.mkdir()
            mock=bin_dir/"ros2"
            mock.write_text("#!/usr/bin/env bash\nexit 1\n")
            mock.chmod(0o755)
            script=Path(__file__).resolve().parents[1]/"scripts"/"record_gate6c_rosbag.sh"
            env={**os.environ,"PATH":str(bin_dir)+os.pathsep+os.environ["PATH"]}
            proc=subprocess.run(["bash",str(script),str(run),"live"],
                                env=env,capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0)
            self.assertFalse((run/"rosbag").exists())


if __name__ == "__main__":
    unittest.main()
