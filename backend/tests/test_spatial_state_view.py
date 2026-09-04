"""
backend/tests/test_spatial_state_view.py

spatial_state_view.py(Presentation / Read Model層)の回帰テスト。

Spatial State Updaterの内部表現(state/confidence_flag/mu)を、Viewer向けの
安定した表示契約(presence/confidence/mobility)へ変換するロジックが、
既存Viewer(local_space_prototype.htmlのSTATE_COLOR/muToMobilityFlag)が
これまで行っていた分類と同じ結果を返すことを確認する
(数式・閾値は移設のみで変更していないため、分類結果も変わらないはず)。

実行方法(リポジトリルートから):
    python backend/tests/test_spatial_state_view.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from spatial_state_view import build_spatial_state_view, build_voxel_view  # noqa: E402


def _voxel(state, confidence_flag, mu, **extra):
    base = {"state": state, "confidence_flag": confidence_flag, "p_occ": 0.5, "kappa": 0.5, "mu": mu, "n_obs": 3, "n_mu": 3}
    base.update(extra)
    return base


def test_presence_mapping_matches_old_state_color_visibility():
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.0))["presence"] == "PRESENT"
    assert build_voxel_view(_voxel("DECAYED", "CONFIRMED", 0.0))["presence"] == "ABSENT"
    # 旧Viewerはstate==SCAN_COVERED/NEVER_OBSERVEDのどちらも描画しない
    # (currentVoxels = voxels.filter(state==OCCUPIED||DECAYED))。
    # Read Modelはこの2つを区別せずUNOBSERVEDへ統合する。
    assert build_voxel_view(_voxel("SCAN_COVERED", "CONFIRMED", 0.0))["presence"] == "UNOBSERVED"
    assert build_voxel_view(_voxel("NEVER_OBSERVED", "PENDING", 0.0))["presence"] == "UNOBSERVED"


def test_confidence_mapping():
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.0))["confidence"] == "HIGH"
    assert build_voxel_view(_voxel("OCCUPIED", "PENDING", 0.0))["confidence"] == "LOW"


def test_mobility_thresholds_match_old_muToMobilityFlag():
    # 旧local_space_prototype.html muToMobilityFlag()と同じ閾値・分岐:
    #   confidence_flag != CONFIRMED -> PENDING
    #   mu >= 0.6 -> DYNAMIC / mu <= 0.4 -> STATIC / それ以外 -> PENDING
    assert build_voxel_view(_voxel("OCCUPIED", "PENDING", 0.9))["mobility"] == "PENDING"
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.6))["mobility"] == "DYNAMIC"
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.99))["mobility"] == "DYNAMIC"
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.4))["mobility"] == "STATIC"
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.0))["mobility"] == "STATIC"
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.5))["mobility"] == "PENDING"
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.41))["mobility"] == "PENDING"
    assert build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.59))["mobility"] == "PENDING"


def test_view_never_exposes_internal_field_names():
    view = build_voxel_view(_voxel("OCCUPIED", "CONFIRMED", 0.6))
    # Read Modelは表示契約であり、内部表現(state/confidence_flag/mu/kappa/
    # p_occ/alpha/beta等)のキー名をそのまま漏らさないこと。
    for leaked_key in ("state", "confidence_flag", "mu", "kappa", "p_occ", "alpha", "beta"):
        assert leaked_key not in view, f"内部表現のキー'{leaked_key}'がRead Modelに漏れている"
    assert set(view.keys()) == {"presence", "confidence", "mobility"}


def test_build_spatial_state_view_applies_to_all_voxels():
    tracker_summary = {
        "9/0/0/0": _voxel("OCCUPIED", "CONFIRMED", 0.1),
        "9/0/0/1": _voxel("DECAYED", "CONFIRMED", 0.9),
        "9/0/0/2": _voxel("NEVER_OBSERVED", "PENDING", 0.0),
    }
    view = build_spatial_state_view(tracker_summary)
    assert set(view.keys()) == set(tracker_summary.keys())
    assert view["9/0/0/0"]["presence"] == "PRESENT"
    assert view["9/0/0/1"]["presence"] == "ABSENT"
    assert view["9/0/0/2"]["presence"] == "UNOBSERVED"


def test_build_spatial_state_view_empty_input():
    assert build_spatial_state_view({}) == {}


if __name__ == "__main__":
    tests = [
        test_presence_mapping_matches_old_state_color_visibility,
        test_confidence_mapping,
        test_mobility_thresholds_match_old_muToMobilityFlag,
        test_view_never_exposes_internal_field_names,
        test_build_spatial_state_view_applies_to_all_voxels,
        test_build_spatial_state_view_empty_input,
    ]
    for t in tests:
        t()
        print(f"OK: {t.__name__}")
    print("ALL PASSED")
