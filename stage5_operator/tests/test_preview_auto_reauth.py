import unittest
from types import SimpleNamespace

from scripts.preview_stage5_web import (PAGE, PreviewState, auto_validation_fields,
                                        parse_args)


class PreviewAutoReauthorizeTests(unittest.TestCase):
    def test_default_disabled_and_explicit_enable(self):
        self.assertFalse(parse_args([]).auto_reauthorize)
        self.assertTrue(parse_args(['--auto-reauthorize']).auto_reauthorize)
        self.assertTrue(parse_args(['--enable-auto-reauthorize']).auto_reauthorize)

    def test_existing_record_exposes_success_and_session(self):
        record = dict(ownership_state='LOCKED_HIGH', current_track_id=12,
            operator_session_id='new', authorization_source='AUTO_REAUTHORIZE',
            state_transition_reason='AUTO_REAUTH_SUCCESS',
            auto_reauthorize_state='SUCCESS', auto_reauthorize_candidate_id=12,
            auto_reauthorize_reid_similarity=.95,
            auto_reauthorize_gallery_max=.95,
            auto_reauthorize_margin=.3,
            auto_reauthorize_frames=8,
            second_candidate={'S_reid':.4})
        fields = auto_validation_fields(record)
        self.assertEqual(fields['final_decision'], 'AUTHORIZED')
        self.assertEqual(fields['candidate_track_id'], 12)
        self.assertEqual(fields['gallery_second'], .4)
        self.assertIsNone(fields['lost_operator_identity'])

    def test_lost_status_shows_old_session_without_authority(self):
        record = dict(frame_id=50, timestamp_ms=5000, ownership_state='OPERATOR_LOST',
            ui_state='GRAY', reject_reason='AUTO_REAUTH_IDENTITY_LOW',
            operator_session_id='old', current_track_id=3,
            auto_reauthorize_enabled=True, auto_reauthorize_state='SEARCHING',
            auto_reauthorize_candidate_id=8, auto_reauthorize_reid_similarity=.86,
            auto_reauthorize_gallery_max=.86, auto_reauthorize_gallery_topk_mean=.84,
            auto_reauthorize_gallery_match_count=0,
            auto_reauthorize_margin=.2, auto_reauthorize_frames=0,
            auto_reauthorize_reject_reason='AUTO_REAUTH_IDENTITY_LOW',
            authorization_source='FIRST_TPOSE', latency_ms={'stage5_total':10.},
            depth_observations=[], acquisition_checks=[], gallery_size=6)
        record.update(auto_validation_fields(record))
        state = PreviewState()
        state.publish_analysis(None, [], SimpleNamespace(gesture='UNKNOWN', valid=False),
                               record, 0, 8., 0, 30.)
        self.assertEqual(state.status['lost_operator_identity'], 'old')
        self.assertEqual(state.status['auto_reauthorize_candidate_id'], 8)
        self.assertEqual(state.status['auto_reauthorize_reid_similarity'], .86)
        self.assertEqual(state.status['gallery_size'], 6)
        self.assertEqual(state.status['final_decision'], 'REJECTED')
        self.assertIn(b'Auto Reauthorize', PAGE)


if __name__ == '__main__':
    unittest.main()
