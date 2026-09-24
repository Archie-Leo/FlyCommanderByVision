from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from stage5_v2.depth import PersonDepthV1, StereoPersonDepthAdapter
from stage5_v2.evidence import CandidateEvidence, Evidence
from stage5_v2.gallery import OperatorReIDGalleryV2
from stage5_v2.ownership import OwnershipManagerV2, OperatorTargetMemory, PersonObservation, State


def depth(z=1.4, quality=.9):
    return PersonDepthV1(z, quality, .8, .02, (0., 0., z)) if z is not None else PersonDepthV1(None,0.,0.,None,None)


def person(t=100, track=2, z=1.4, emb=(1.,0.,0.), box=(100.,100.,300.,600.),
           pose=True, crop=1., hsv=None):
    return PersonObservation(track,t,t,box,.95,SimpleNamespace(valid=pose),object(),emb,crop,depth(z),hsv)


def seeded():
    manager = OwnershipManagerV2()
    assert manager.gallery.seed([(1.,0.,0.)]*6)
    manager.memory = OperatorTargetMemory("session-v2",2,0,0,(100.,100.,300.,600.),1.4,(0.,0.,1.4))
    manager.state = State.LOCKED_HIGH
    return manager


class Stage5V2SafetyTests(unittest.TestCase):
    def test_empty_scene_skips_disparity_and_reuses_rectified_left(self):
        adapter=object.__new__(StereoPersonDepthAdapter)
        adapter.size=(1280,960)
        adapter.last_latency_ms=0.0
        raw=np.zeros((960,1280,3),dtype=np.uint8)
        rectified=np.ones_like(raw)
        with patch("stage5_v2.depth.cv2.remap",side_effect=AssertionError("unneeded remap")):
            image, depths=adapter.process(raw,raw,[],rectified_left=rectified)
        self.assertIs(image,rectified)
        self.assertEqual(depths,[])

    def test_depth_continuous_scores_high(self):
        m=seeded(); c=m._evidence(person(),100)
        self.assertTrue(c.accepted)
        self.assertGreater(c.depth.value,.8)

    def test_impossible_depth_jump_hard_reject(self):
        m=seeded(); c=m._evidence(person(z=2.6),100)
        self.assertFalse(c.hard_gates["xyz_depth_plausible"])
        m.update([person(z=2.6)],100,100)
        self.assertNotEqual(m.state,State.LOCKED_HIGH)

    def test_depth_unavailable_is_not_zero_score(self):
        m=seeded(); c=m._evidence(person(z=None),100)
        self.assertTrue(c.hard_gates["xyz_depth_plausible"])
        self.assertFalse(c.depth.available)
        self.assertGreater(c.fused_normalized,.5)

    def test_impossible_lateral_xyz_jump_hard_reject(self):
        m=seeded(); p=person()
        p.depth=PersonDepthV1(1.4,.9,.8,.02,(2.0,0.,1.4))
        self.assertFalse(m._evidence(p,100).hard_gates["xyz_depth_plausible"])

    def test_reid_and_motion_can_hold(self):
        m=seeded(); m.update([person()],100,100)
        self.assertEqual(m.state,State.LOCKED_HIGH)

    def test_reid_drop_red_not_lost(self):
        m=seeded(); m.update([person(100,emb=(.2,.98,0.))],100,100)
        self.assertIn(m.state,{State.LOCKED_LOW,State.REACQUIRING})

    def test_short_pose_invalid_denies_gesture(self):
        m=seeded(); m.update([person(100,pose=False)],100,100)
        gesture=SimpleNamespace(label="LEFT",stable=True,score=.99)
        self.assertFalse(m.authorize(gesture,100,100).valid)

    def test_recovery_red_to_green_after_hysteresis(self):
        m=seeded(); m.update([],100,100)
        for t in (200,300,400,500,600,700):
            m.update([person(t)],t,t)
        self.assertEqual(m.state,State.LOCKED_HIGH)

    def test_grace_timeout_lost(self):
        m=seeded(); m.update([],100,100); m.update([],1300,1300)
        self.assertEqual(m.state,State.OPERATOR_LOST)

    def test_lost_requires_fresh_tpose_and_old_identity_after_long_exit(self):
        m=seeded(); old_session=m.memory.operator_session_id
        m.tpose=SimpleNamespace(recognize=lambda skeleton, track: SimpleNamespace(matched=True,reasons=()))
        m.update([],100,100); m.update([],1300,1300)
        self.assertEqual(m.state,State.OPERATOR_LOST)
        for t in (5000,5100,5200,5300,5400):
            m.update([person(t)],t,t)
            self.assertNotEqual(m.state,State.LOCKED_HIGH)
            self.assertFalse(m.authorize(SimpleNamespace(label="LEFT",stable=True,score=.99),t,t).valid)
        m.update([person(5600)],5600,5600)
        self.assertEqual(m.state,State.LOCKED_HIGH)
        self.assertNotEqual(m.memory.operator_session_id,old_session)
        self.assertEqual(m.memory.current_track_id,2)

    def test_lost_bystander_tpose_cannot_take_old_session(self):
        m=seeded()
        m.tpose=SimpleNamespace(recognize=lambda skeleton, track: SimpleNamespace(matched=True,reasons=()))
        m.update([],100,100); m.update([],1300,1300)
        for t in (5000,5100,5200,5300,5400,5600,5700):
            m.update([person(t,track=9,emb=(0.,1.,0.))],t,t)
        self.assertEqual(m.state,State.OPERATOR_LOST)
        self.assertEqual(m.memory.operator_session_id,"session-v2")
        self.assertLess(m.acquisition_checks[0]["old_gallery_similarity"],m.settings.reauth_reid_similarity)

    def test_lost_two_matching_tposes_remain_ambiguous(self):
        m=seeded()
        m.tpose=SimpleNamespace(recognize=lambda skeleton, track: SimpleNamespace(matched=True,reasons=()))
        m.update([],100,100); m.update([],1300,1300)
        for t in (5000,5100,5200,5300,5400,5600):
            m.update([person(t,track=2),person(t,track=9)],t,t)
        self.assertEqual(m.state,State.OPERATOR_LOST)
        self.assertEqual(m.reject_reason,"REAUTH_AMBIGUOUS")
        m.update([person(5700,track=2)],5700,5700)
        self.assertEqual(m.state,State.REAUTHORIZING)

    def test_lost_without_tpose_never_auto_recovers(self):
        m=seeded(); m.update([],100,100); m.update([],1300,1300)
        for t in (5000,5100,5200,5300,5400,5600):
            m.update([person(t)],t,t)
        self.assertEqual(m.state,State.OPERATOR_LOST)
        self.assertIn("tpose_reasons",m.acquisition_checks[0])

    def test_competing_candidate_rejects(self):
        m=seeded(); m.update([person(100,track=2),person(100,track=3)],100,100)
        self.assertEqual(m.state,State.AMBIGUOUS)

    def test_track_id_change_keeps_session(self):
        m=seeded(); m.update([person(100,track=4)],100,100)
        for t in (200,300,400,500,600): m.update([person(t,track=4)],t,t)
        self.assertEqual(m.state,State.LOCKED_HIGH)
        self.assertEqual(m.memory.operator_session_id,"session-v2")
        self.assertEqual(m.memory.current_track_id,4)

    def test_old_id_cannot_override_reid_conflict(self):
        m=seeded(); m.update([person(100,track=2,emb=(0.,1.,0.))],100,100)
        self.assertNotEqual(m.state,State.LOCKED_HIGH)

    def test_same_hsv_different_reid_denied(self):
        m=seeded(); m.update([person(100,emb=(0.,1.,0.),hsv=(1.,))],100,100)
        self.assertNotEqual(m.state,State.LOCKED_HIGH)

    def test_gallery_red_cannot_update(self):
        m=seeded(); m.update([],100,100)
        self.assertFalse(m.gallery_updated)
        self.assertEqual(len(m.gallery.entries),6)

    def test_gallery_green_quality_gate(self):
        g=OperatorReIDGalleryV2()
        self.assertFalse(g.update((1.,0.),green=True,confidence=.9,crop_quality=.5,
                                  occlusion=0.,margin=.5,conflict=False,motion_pass=True,depth_pass=True))
        self.assertTrue(g.update((1.,0.),green=True,confidence=.9,crop_quality=.9,
                                 occlusion=0.,margin=.5,conflict=False,motion_pass=True,depth_pass=True))

    def test_only_green_can_authorize(self):
        gesture=SimpleNamespace(label="LEFT",stable=True,score=.99)
        for state in (State.WAIT_OPERATOR,State.LOCKED_LOW,State.OPERATOR_LOST):
            m=seeded(); m.state=state
            self.assertFalse(m.authorize(gesture,100,100).valid)
        m=seeded(); m.update([person()],100,100)
        self.assertTrue(m.authorize(gesture,100,100).valid)

    def test_fusion_unavailable_is_renormalized(self):
        c=CandidateEvidence(1,reid=Evidence(.9,.8,True),depth=Evidence(0.,0.,False))
        self.assertAlmostEqual(c.fuse(),.9)

    def test_nonfinite_serialization_rejected(self):
        with self.assertRaises(ValueError): Evidence(float("nan"),1.,True)

    def test_serialization_contains_no_nan(self):
        m=seeded(); m.update([person(z=None)],100,100)
        payload={"state":m.state.value,"ui_state":m.ui_state(),
                 "candidates":[c.to_dict() for c in m.candidates]}
        encoded=json.dumps(payload,allow_nan=False)
        self.assertNotIn("NaN",encoded)

    def test_stereo_invalid_fallback_to_other_evidence(self):
        m=seeded(); m.update([person(z=None)],100,100)
        self.assertEqual(m.state,State.LOCKED_HIGH)
        self.assertFalse(m.candidates[0].depth.available)

    def test_impossible_2d_motion_hard_reject(self):
        m=seeded(); far=person(box=(900.,100.,1100.,600.))
        self.assertFalse(m._evidence(far,100).hard_gates["motion_plausible"])
        m.update([far],100,100)
        self.assertNotEqual(m.state,State.LOCKED_HIGH)

    def test_pose_geometry_is_weak_evidence_not_identity_gate(self):
        m=seeded(); m.memory.pose_geometry_summary=1.0
        p=person(); p.skeleton=SimpleNamespace(bones={
            "shoulder_line":SimpleNamespace(length=2.),
            "left_torso":SimpleNamespace(length=1.),
            "right_torso":SimpleNamespace(length=1.)})
        c=m._evidence(p,100)
        self.assertEqual(c.pose.value,0.)
        self.assertTrue(c.hard_gates["reid_no_strong_conflict"])


class BoundedReacquireTests(unittest.TestCase):
    def recorded_crossing_prefix(self):
        # Compact evidence/time slice transcribed from NUC clip_002 frames
        # 398-405. Raw SBS was not recorded, so this replays logged candidate
        # scores and hard-pass flags, not a new image inference run.
        rows = (
            (0, ((4,.650,.810,.563,.75),)),
            (154, ((4,.702,.810,.531,.69),)),
            (305, ((4,.725,.820,.522,.68),)),
            (456, ((4,.729,.806,.535,.73),)),
            (599, ((4,.726,.798,.521,.71),)),
            (740, ((3,.949,.951,.624,1.0),(4,.710,.767,.510,.70))),
            (931, ((3,.940,.936,.538,.80),(4,.719,.784,.481,.65))),
            (1125, ((3,.941,.937,.497,.72),(4,.701,.758,.467,.64))),
        )
        m=seeded();m.memory.current_track_id=3;m._last_frame_ms=-182
        for t, entries in rows:
            evidence={}
            people=[]
            for track,score,reid,weight,crop in entries:
                c=CandidateEvidence(track,reid=Evidence(reid,1.,True))
                c.hard_gates={"recorded_hard_pass":True}
                c.fused_normalized=score;c.effective_weight=weight
                evidence[track]=c
                people.append(person(t,track=track,crop=crop))
            m._evidence=lambda p,now,items=evidence: items[p.track_id]
            m.update(people,t,t)
        return m

    def test_recorded_crossing_old_grace_no_longer_kills_candidate(self):
        m=self.recorded_crossing_prefix()
        self.assertEqual(m.state,State.REACQUIRE_CONFIRMING)
        self.assertEqual(m._pending[0],3)
        self.assertEqual(m._pending[2],3)
        self.assertEqual(m.reacquire_diagnostics(1125)["overall_reacquire_elapsed_ms"],1125)
        self.assertFalse(m.authorize(SimpleNamespace(label="LEFT",stable=True,score=.99),1125,1125).valid)

    def test_recorded_timing_would_confirm_if_evidence_continues(self):
        m=self.recorded_crossing_prefix();old_session=m.memory.operator_session_id
        # Frames 406-407 have no candidate scores in the old log because the
        # old FSM had already entered LOST. These are explicitly hypothetical.
        for t in (1316,1524):
            c=CandidateEvidence(3,reid=Evidence(.94,1.,True))
            c.hard_gates={"hypothetical_hard_pass":True}
            c.fused_normalized=.94;c.effective_weight=.50
            m._evidence=lambda p,now,candidate=c: candidate
            m.update([person(t,track=3,crop=.74)],t,t)
        self.assertEqual(m.state,State.LOCKED_HIGH)
        self.assertEqual(m.memory.operator_session_id,old_session)

    def confirming(self, track=2, z=1.4):
        m=seeded()
        m.update([],100,100)
        m.update([person(900,track=track,z=z)],900,900)
        self.assertEqual(m.state,State.REACQUIRE_CONFIRMING)
        return m

    def test_unique_strong_candidate_enters_confirming(self):
        m=self.confirming()
        d=m.reacquire_diagnostics(900)
        self.assertTrue(d["reacquire_confirmation_active"])
        self.assertEqual(d["reacquire_confirmation_candidate_id"],2)
        self.assertEqual(d["reacquire_confirmation_frames"],1)
        self.assertEqual(d["state_transition_reason"],"REACQUIRE_CANDIDATE_FOUND")

    def test_old_grace_deadline_cannot_kill_valid_confirmation(self):
        m=self.confirming()
        for t in (1000,1100,1200): m.update([person(t)],t,t)
        self.assertEqual(m.state,State.REACQUIRE_CONFIRMING)
        self.assertGreaterEqual(1200-100,m.settings.red_grace_ms)

    def test_sustained_candidate_recovers_same_session(self):
        m=self.confirming(); original=m.memory.operator_session_id
        for t in (1000,1100,1200,1300): m.update([person(t)],t,t)
        self.assertEqual(m.state,State.LOCKED_HIGH)
        self.assertEqual(m.memory.operator_session_id,original)
        self.assertEqual(m.state_transition_reason,"REACQUIRE_CONFIRMED")

    def test_confirmation_never_authorizes_gesture(self):
        m=self.confirming()
        gesture=SimpleNamespace(label="LEFT",stable=True,score=.99)
        self.assertFalse(m.authorize(gesture,900,900).valid)

    def test_confirmation_does_not_update_gallery(self):
        m=self.confirming(); before=len(m.gallery.entries)
        for t in (1000,1100,1200):
            m.update([person(t)],t,t)
            self.assertFalse(m.gallery_updated)
            self.assertEqual(len(m.gallery.entries),before)

    def test_candidate_disappears_aborts_confirmation(self):
        m=self.confirming();m.update([],1000,1000)
        self.assertEqual(m.state,State.REACQUIRING)
        self.assertFalse(m.reacquire_diagnostics(1000)["reacquire_confirmation_active"])
        self.assertEqual(m.state_transition_reason,"REACQUIRE_CONFIRMATION_LOST")

    def test_identity_conflict_aborts_confirmation(self):
        m=self.confirming();m.update([person(1000,emb=(0.,1.,0.))],1000,1000)
        self.assertEqual(m.state,State.REACQUIRING)
        self.assertIn("reid_no_strong_conflict",m.reject_reason)

    def test_ambiguous_second_candidate_aborts(self):
        m=self.confirming()
        m.update([person(1000,track=2),person(1000,track=3)],1000,1000)
        self.assertEqual(m.state,State.AMBIGUOUS)
        self.assertEqual(m.state_transition_reason,"REACQUIRE_AMBIGUOUS")

    def test_overall_timeout_is_absolute_even_with_candidate(self):
        m=self.confirming();m.update([person(2100)],2100,2100)
        self.assertEqual(m.state,State.OPERATOR_LOST)
        self.assertEqual(m.reject_reason,"REACQUIRE_OVERALL_TIMEOUT")

    def test_weak_candidates_cannot_extend_original_grace(self):
        m=seeded();m.update([],100,100)
        for t in (200,500,900,1200):
            m.update([person(t,emb=(0.,1.,0.))],t,t)
        self.assertEqual(m.state,State.OPERATOR_LOST)
        self.assertEqual(m.reject_reason,"GRACE_TIMEOUT")

    def test_changed_track_id_can_recover_original_session(self):
        m=self.confirming(track=4);original=m.memory.operator_session_id
        for t in (1000,1100,1200,1300):m.update([person(t,track=4)],t,t)
        self.assertEqual(m.state,State.LOCKED_HIGH)
        self.assertEqual(m.memory.operator_session_id,original)
        self.assertEqual(m.memory.current_track_id,4)

    def test_old_track_id_alone_cannot_recover(self):
        m=seeded();m.update([],100,100)
        m.update([person(900,track=2,emb=(0.,1.,0.))],900,900)
        self.assertNotEqual(m.state,State.REACQUIRE_CONFIRMING)
        self.assertNotEqual(m.state,State.LOCKED_HIGH)

    def test_bystander_high_mot_but_reid_conflict_denied(self):
        m=seeded();m.update([],100,100)
        p=person(900,track=2,emb=(0.,1.,0.),hsv=(1.,))
        m.update([p],900,900)
        self.assertFalse(m.gallery_updated)
        self.assertNotEqual(m.state,State.REACQUIRE_CONFIRMING)

    def test_unavailable_depth_does_not_block_confirmation(self):
        self.assertEqual(self.confirming(z=None).state,State.REACQUIRE_CONFIRMING)

    def test_impossible_depth_jump_immediately_rejected(self):
        m=seeded();m.update([],100,100)
        m.update([person(200,z=2.6)],200,200)
        self.assertEqual(m.state,State.REACQUIRING)
        self.assertIn("xyz_depth_plausible",m.reject_reason)

    def test_candidate_confirmation_window_timeout(self):
        m=self.confirming()
        m.update([person(1850)],1850,1850)
        self.assertEqual(m.state,State.REACQUIRING)
        self.assertEqual(m.reject_reason,"REACQUIRE_CONFIRMATION_TIMEOUT")

    def test_reacquire_log_fields_are_json_serializable(self):
        m=self.confirming();data=m.reacquire_diagnostics(900)
        expected={"reacquire_confirmation_active","reacquire_confirmation_candidate_id",
                  "reacquire_confirmation_frames","reacquire_confirmation_elapsed_ms",
                  "reacquire_confirmation_deadline_ms","overall_reacquire_elapsed_ms",
                  "overall_reacquire_deadline_ms","state_transition_reason"}
        self.assertEqual(set(data),expected)
        json.dumps(data,allow_nan=False)


if __name__ == "__main__": unittest.main()
