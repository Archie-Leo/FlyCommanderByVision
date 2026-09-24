from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from stage5_v2.ownership import State
from stage5_v2.pipeline import Stage5PipelineV2
from test_stage5_v2 import person, seeded


def lost_auto(**settings):
    manager = seeded()
    manager.settings = replace(manager.settings, auto_reauthorize_enabled=True, **settings)
    manager.state = State.OPERATOR_LOST
    manager._last_frame_ms = 1300
    return manager


def feed(manager, times=(5000, 5100, 5200, 5300, 5400, 5500, 5600, 5700), **kwargs):
    for t in times:
        manager.update([person(t, **kwargs)], t, t)
    return manager


class AutoReauthorizationTests(unittest.TestCase):
    def test_default_disabled_and_manual_behavior_unchanged(self):
        m = seeded(); m.state = State.OPERATOR_LOST; m._last_frame_ms = 1300
        feed(m)
        self.assertEqual(m.state, State.OPERATOR_LOST)
        self.assertEqual(m.auto_reauthorize_diagnostics(5700)["auto_reauthorize_reject_reason"],
                         "AUTO_REAUTH_DISABLED")

    def test_single_strong_frame_only_confirms(self):
        m = lost_auto(); feed(m, (5000,))
        self.assertEqual(m.state, State.AUTO_REAUTHORIZE_CONFIRMING)
        self.assertFalse(m.authorize(None, 5000, 5000).valid)

    def test_gallery_read_only_through_confirmation(self):
        m = lost_auto(); count = len(m.gallery.entries)
        feed(m, (5000, 5100, 5200))
        self.assertEqual(m.state, State.AUTO_REAUTHORIZE_CONFIRMING)
        self.assertEqual(len(m.gallery.entries), count)
        self.assertFalse(m.gallery_updated)

    def test_frames_required(self):
        m = lost_auto(); feed(m, (5000, 5200, 5400, 5800))
        self.assertEqual(m.state, State.AUTO_REAUTHORIZE_CONFIRMING)

    def test_elapsed_time_required(self):
        m = lost_auto(); feed(m, tuple(5000+i*50 for i in range(8)))
        self.assertEqual(m.state, State.AUTO_REAUTHORIZE_CONFIRMING)

    def test_sustained_evidence_creates_new_session(self):
        m = lost_auto(); old = m.memory.operator_session_id
        feed(m)
        self.assertEqual(m.state, State.LOCKED_HIGH)
        self.assertNotEqual(m.memory.operator_session_id, old)
        self.assertEqual(m.old_operator_session_id, old)
        self.assertEqual(m.new_operator_session_id, m.memory.operator_session_id)
        self.assertEqual(m.authorization_source, "AUTO_REAUTHORIZE")
        self.assertEqual(m.state_transition_reason, "AUTO_REAUTH_SUCCESS")
        self.assertFalse(m.gallery_updated)

    def test_no_old_gesture_restored(self):
        m = lost_auto(); feed(m)
        self.assertFalse(m.authorize(None, 5700, 5700).valid)
        self.assertEqual(m.authorize(None, 5700, 5700).gesture, "UNKNOWN")

    def test_old_session_not_resurrected(self):
        m = lost_auto(); old = m.memory
        feed(m)
        self.assertIsNot(m.memory, old)
        self.assertNotEqual(m.memory.operator_session_id, old.operator_session_id)

    def test_weak_reid_rejected(self):
        m = lost_auto(); feed(m, (5000,), emb=(0., 1., 0.))
        self.assertEqual(m.state, State.OPERATOR_LOST)
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_HARD_GATE_FAIL")

    def test_high_reid_but_low_fused_score_rejected(self):
        m = lost_auto(auto_reauthorize_score_threshold=.999)
        p = person(5000, emb=(.94, .342, 0.))
        m.update([p], 5000, 5000)
        self.assertEqual(m.state, State.OPERATOR_LOST)
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_SCORE_LOW")

    def test_high_reid_hard_gate_rejected(self):
        m = lost_auto(); p = person(5000); p.face_conflict = True
        m.update([p], 5000, 5000)
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_HARD_GATE_FAIL")

    def test_poor_crop_rejected(self):
        m = lost_auto(); feed(m, (5000,), crop=.5)
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_LOW_CROP_QUALITY")

    def test_two_matching_candidates_ambiguous(self):
        m = lost_auto()
        m.update([person(5000, track=2), person(5000, track=3)], 5000, 5000)
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_AMBIGUOUS")
        self.assertEqual(m.state, State.OPERATOR_LOST)

    def test_margin_low_rejected(self):
        m = lost_auto(auto_reauthorize_reid_threshold=1.0)
        m.update([person(5000, track=2), person(5000, track=3, emb=(.98,.199,0.))],5000,5000)
        self.assertIn(m.reject_reason, {"AUTO_REAUTH_AMBIGUOUS", "AUTO_REAUTH_MARGIN_LOW"})

    def test_disappearance_aborts_and_resets_frames(self):
        m = lost_auto(); feed(m, (5000,5100))
        m.update([], 5200, 5200)
        self.assertEqual(m.state, State.OPERATOR_LOST)
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_CANDIDATE_LOST")
        feed(m, (5300,))
        self.assertEqual(m.auto_reauthorize_diagnostics(5300)["auto_reauthorize_frames"],1)

    def test_timeout_aborts(self):
        m = lost_auto(); feed(m, (5000,))
        feed(m, (6600,))
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_TIMEOUT")

    def test_id_change_resets_not_authorizes(self):
        m = lost_auto(); feed(m, (5000,5100,5200))
        feed(m, (5300,), track=7)
        self.assertEqual(m.state, State.OPERATOR_LOST)
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_CANDIDATE_LOST")
        feed(m, (5400,), track=7)
        self.assertEqual(m.state, State.AUTO_REAUTHORIZE_CONFIRMING)
        self.assertEqual(m.auto_reauthorize_diagnostics(5400)["auto_reauthorize_frames"],1)

    def test_reused_old_track_id_is_not_identity(self):
        m = lost_auto(); feed(m, (5000,), track=2, emb=(0.,1.,0.))
        self.assertEqual(m.state, State.OPERATOR_LOST)

    def test_one_accidental_gallery_match_not_enough(self):
        m = lost_auto(); m.gallery.entries.clear()
        m.gallery.entries.extend([(0.,1.,0.)]*5+[(1.,0.,0.)])
        feed(m, (5000,))
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_IDENTITY_LOW")
        self.assertEqual(m.auto_reauthorize_diagnostics(5000)["auto_reauthorize_gallery_match_count"],1)

    def test_no_gallery_rejected(self):
        m = lost_auto(); m.gallery.entries.clear(); feed(m, (5000,))
        self.assertEqual(m.reject_reason, "AUTO_REAUTH_NO_GALLERY")

    def test_nonfinite_embedding_fails_closed(self):
        m = lost_auto(); feed(m, (5000,), emb=(float("nan"),0.,0.))
        self.assertEqual(m.state, State.OPERATOR_LOST)
        self.assertFalse(m.authorize(None,5000,5000).valid)

    def test_tpose_fallback_still_creates_one_new_session(self):
        m = lost_auto(); old = m.memory.operator_session_id
        m.tpose = SimpleNamespace(recognize=lambda skeleton, track: SimpleNamespace(matched=True,reasons=()))
        feed(m, (5000,5100,5200,5300,5400,5600))
        self.assertEqual(m.state, State.LOCKED_HIGH)
        self.assertEqual(m.authorization_source, "LOST_TPOSE")
        self.assertNotEqual(m.memory.operator_session_id,old)
        self.assertIsNone(m.new_operator_session_id)

    def test_auto_and_tpose_do_not_create_two_sessions(self):
        m = lost_auto(); feed(m, (5000,5100))
        m.tpose = SimpleNamespace(recognize=lambda skeleton, track: SimpleNamespace(matched=True,reasons=()))
        feed(m, (5200,5300,5400,5500,5600,5800))
        self.assertEqual(m.state, State.LOCKED_HIGH)
        self.assertEqual(m.authorization_source, "LOST_TPOSE")
        self.assertIsNone(m.new_operator_session_id)

    def test_gallery_hold_after_success(self):
        m = lost_auto(); feed(m); size = len(m.gallery.entries)
        m.update([person(5800)],5800,5800)
        self.assertFalse(m.gallery_updated)
        self.assertEqual(len(m.gallery.entries),size)

    def test_complete_json_diagnostics(self):
        m = lost_auto(); feed(m, (5000,))
        d = m.auto_reauthorize_diagnostics(5000)
        expected = {"auto_reauthorize_enabled", "auto_reauthorize_state",
                    "auto_reauthorize_candidate_id", "auto_reauthorize_reid_similarity",
                    "auto_reauthorize_gallery_max", "auto_reauthorize_gallery_topk_mean",
                    "auto_reauthorize_gallery_match_count", "auto_reauthorize_identity_score",
                    "auto_reauthorize_margin", "auto_reauthorize_frames",
                    "auto_reauthorize_elapsed_ms", "auto_reauthorize_deadline_ms",
                    "auto_reauthorize_reject_reason", "authorization_source",
                    "old_operator_session_id", "new_operator_session_id",
                    "auto_reauthorize_success_notice"}
        self.assertEqual(set(d),expected)
        json.dumps(d,allow_nan=False)

    def test_invalid_timestamp_stays_lost(self):
        m = lost_auto(); feed(m, (5000,))
        m.update([person(5000)], 5000, 5000)
        self.assertEqual(m.state, State.OPERATOR_LOST)
        self.assertFalse(m.authorize(None,5000,5000).valid)

    def test_stale_detection_stays_lost(self):
        m = lost_auto(); m.update([person(4900)],5000,5000)
        self.assertEqual(m.state, State.OPERATOR_LOST)

    def test_pipeline_requires_new_gesture_after_auto_success(self):
        m = lost_auto(); feed(m, (5000,5100,5200,5300,5400,5500,5600))
        class Depth:
            last_latency_ms = 0.
            def process(self,left,right,boxes,rectified_left=None):
                return left,[person().depth for _ in boxes]
        class Embedder:
            last_latency_ms = 0.
            def extract(self,image,box): return (1.,0.,0.),1.
        class Tracker:
            last_latency_ms = 0.
            def update(self,image,boxes,scores,vectors):
                return [{"detection_index":0,"track_id":2,
                         "bbox_xyxy":boxes[0],"confidence":.95}]
        class Temporal:
            def __init__(self): self.resets=0;self.updates=0
            def reset(self): self.resets+=1
            def update(self,raw):
                self.updates+=1
                return SimpleNamespace(label="LEFT",stable=True,score=.99,
                                       to_dict=lambda: {"label":"LEFT"})
        pipeline = Stage5PipelineV2(Tracker(),Embedder(),Depth())
        pipeline.ownership = m
        pipeline.temporal = Temporal()
        pipeline.gesture = SimpleNamespace(recognize=lambda skeleton: SimpleNamespace(
            label="LEFT",stable=False,score=.99,to_dict=lambda: {"label":"LEFT"}))
        pipeline._last_selected = (m.memory.operator_session_id,2)
        pose = SimpleNamespace(bbox_xyxy=(100.,100.,300.,600.),pose_score=.95)
        evaluator = SimpleNamespace(evaluate=lambda pose: SimpleNamespace(valid=True))
        normalizer = SimpleNamespace(normalize=lambda pose,quality: object())
        frame = np.zeros((960,1280,3),dtype=np.uint8)
        at = lambda t: SimpleNamespace(poses=[pose],timestamp_ms=t,frame_id=t)
        _,_,authorized,log = pipeline.process(frame,frame,at(5700),evaluator,normalizer)
        self.assertEqual(m.state,State.LOCKED_HIGH)
        self.assertFalse(authorized.valid)
        self.assertEqual(pipeline.temporal.updates,0)
        self.assertIsNone(log["gesture_stable"])
        _,_,authorized,_ = pipeline.process(frame,frame,at(5800),evaluator,normalizer)
        self.assertTrue(authorized.valid)
        self.assertEqual(pipeline.temporal.updates,1)

    def test_auto_confirming_ui_renders_without_authority(self):
        from live_operator_v2 import draw
        m = lost_auto(); feed(m,(5000,))
        log = {"ui_state":m.ui_state(),"suspected_track_id":2,
               "best_candidate":m.candidates[0].to_dict(),
               "ambiguity_margin":m.margin,"reject_reason":m.reject_reason,
               "ownership_state":m.state.value,"gallery_size":len(m.gallery.entries),
               "latency_ms":{"stage5_total":1.},"acquisition_checks":m.acquisition_checks,
               "timestamp_ms":5000,**m.auto_reauthorize_diagnostics(5000)}
        authorized = m.authorize(None,5000,5000)
        self.assertFalse(authorized.valid)
        image = draw(np.zeros((960,1280,3),dtype=np.uint8),[person(5000)],
                     authorized,log,debug=True)
        self.assertEqual(image.shape,(960,1280,3))


if __name__ == "__main__":
    unittest.main()
