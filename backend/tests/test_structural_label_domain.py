"""
backend/domain/structural_label.py の動作確認テスト(malformed dataの明示的な失敗)。

実行方法(リポジトリルートから):
    python backend/tests/test_structural_label_domain.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.structural_label import (  # noqa: E402
    LabelCandidate,
    Plane,
    SpatialVoxelLabel,
    StructuralLabel,
)


def _valid_plane_kwargs():
    return dict(
        plane_id="P001", space_id="s1",
        coefficients=[0.0, 0.0, 1.0, 0.0], normal=[0.0, 0.0, 1.0], centroid=[0.0, 0.0, 0.0],
        point_count=3, point_indices=[0, 1, 2],
        suggested_label=StructuralLabel.FLOOR, confirmed_label=StructuralLabel.FLOOR,
    )


def test_valid_plane_constructs():
    p = Plane(**_valid_plane_kwargs())
    assert p.plane_id == "P001"
    print("test_valid_plane_constructs: OK")


def test_plane_point_count_mismatch_raises():
    kwargs = _valid_plane_kwargs()
    kwargs["point_count"] = 99
    try:
        Plane(**kwargs)
        raise AssertionError("point_countとpoint_indicesの不一致が受理されてしまった")
    except ValueError:
        pass
    print("test_plane_point_count_mismatch_raises: OK")


def test_plane_bad_coefficients_length_raises():
    kwargs = _valid_plane_kwargs()
    kwargs["coefficients"] = [0.0, 0.0, 1.0]  # 3要素(4要素必要)
    try:
        Plane(**kwargs)
        raise AssertionError("不正なcoefficients長が受理されてしまった")
    except ValueError:
        pass
    print("test_plane_bad_coefficients_length_raises: OK")


def test_plane_ambiguous_label_rejected():
    kwargs = _valid_plane_kwargs()
    kwargs["confirmed_label"] = StructuralLabel.AMBIGUOUS  # Planeには使わないvoxel専用ラベル
    try:
        Plane(**kwargs)
        raise AssertionError("AMBIGUOUSがPlaneのconfirmed_labelとして受理されてしまった")
    except ValueError:
        pass
    print("test_plane_ambiguous_label_rejected: OK")


def test_missing_space_id_raises():
    try:
        SpatialVoxelLabel(space_id="", local_spatial_id="9/0/0/0", label_candidates=[])
        raise AssertionError("空のspace_idが受理されてしまった")
    except ValueError:
        pass
    print("test_missing_space_id_raises: OK")


def test_valid_spatial_voxel_label_roundtrip():
    v = SpatialVoxelLabel(
        space_id="s1", local_spatial_id="9/0/0/0",
        label_candidates=[LabelCandidate(label=StructuralLabel.FLOOR, fitness=1.0, source_plane_ids=["P001"])],
        resolved_label=StructuralLabel.FLOOR, source_plane_ids=["P001"],
    )
    restored = SpatialVoxelLabel.from_dict(v.to_dict())
    assert restored.space_id == v.space_id
    assert restored.local_spatial_id == v.local_spatial_id
    assert restored.resolved_label == StructuralLabel.FLOOR
    assert restored.label_candidates[0].label == StructuralLabel.FLOOR
    print("test_valid_spatial_voxel_label_roundtrip: OK")


if __name__ == "__main__":
    test_valid_plane_constructs()
    test_plane_point_count_mismatch_raises()
    test_plane_bad_coefficients_length_raises()
    test_plane_ambiguous_label_rejected()
    test_missing_space_id_raises()
    test_valid_spatial_voxel_label_roundtrip()
    print()
    print("全テスト成功。")
