from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    skeleton = data.get("normalized_skeleton", data)
    if not skeleton or not skeleton.get("valid"):
        raise ValueError(f"No valid NormalizedSkeleton in {path}")
    return skeleton


def compare(a, b):
    joint_sq = []
    for name in sorted(set(a["joints"]) & set(b["joints"])):
        ja, jb = a["joints"][name], b["joints"][name]
        if ja["valid"] and jb["valid"]:
            joint_sq.append((ja["x"] - jb["x"]) ** 2 + (ja["y"] - jb["y"]) ** 2)
    bone_diff = []
    for name in sorted(set(a["bones"]) & set(b["bones"])):
        ba, bb = a["bones"][name], b["bones"][name]
        if ba["valid"] and bb["valid"]:
            bone_diff.append(math.hypot(ba["dx"] - bb["dx"], ba["dy"] - bb["dy"]))
    angle_diff = []
    for name in sorted(set(a["angles_deg"]) & set(b["angles_deg"])):
        aa, ab = a["angles_deg"][name], b["angles_deg"][name]
        if aa["valid"] and ab["valid"]:
            delta = abs(aa["degrees"] - ab["degrees"])
            angle_diff.append(min(delta, 360.0 - delta))
    return {
        "normalized_joint_rmse": math.sqrt(sum(joint_sq) / len(joint_sq)) if joint_sq else None,
        "mean_bone_vector_difference": sum(bone_diff) / len(bone_diff) if bone_diff else None,
        "mean_angle_difference_deg": sum(angle_diff) / len(angle_diff) if angle_diff else None,
        "common_valid_joints": len(joint_sq),
        "common_valid_bones": len(bone_diff),
        "common_valid_angles": len(angle_diff),
        "threshold_note": "No official PASS threshold is applied; values are for engineering comparison.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("comparisons", type=Path, nargs="+")
    args = parser.parse_args()
    reference = load(args.reference)
    print(json.dumps({str(path): compare(reference, load(path)) for path in args.comparisons}, indent=2))


if __name__ == "__main__":
    main()

