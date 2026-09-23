import tempfile
import unittest
from pathlib import Path
import numpy as np
from config import DepthValidationConfig
from stereo_depth_validation import append_csv, build_matcher, sample_depth


class DepthValidationTests(unittest.TestCase):
    def test_matcher_and_config(self):
        cfg=DepthValidationConfig(); cfg.validate(); matcher=build_matcher(cfg)
        self.assertEqual(matcher.getNumDisparities(),128); self.assertEqual(cfg.p1,200); self.assertEqual(cfg.p2,800)

    def test_sample_depth_valid_and_invalid(self):
        cfg=DepthValidationConfig(); d=np.full((10,10),32.0,np.float32); p=np.zeros((10,10,3),np.float32); p[:,:,2]=1000.0; valid=np.ones((10,10),bool)
        m=sample_depth(5,5,d,p,valid,cfg); self.assertTrue(m['valid']); self.assertEqual(m['depth_mm'],1000.0)
        self.assertFalse(sample_depth(-1,0,d,p,valid,cfg)['valid']); self.assertFalse(sample_depth(5,5,d,p,np.zeros_like(valid),cfg)['valid'])

    def test_csv(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'depth_validation.csv'; m={'depth_mm':990.0,'x':1,'y':2,'median_disparity':40.0,'valid_samples':20}
            append_csv(path,m,1000.0); text=path.read_text(); self.assertIn('relative_error_percent',text); self.assertIn('990.0',text)


if __name__=='__main__': unittest.main()
