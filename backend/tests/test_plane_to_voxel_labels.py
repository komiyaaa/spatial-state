"""
backend/plane_to_voxel_labels.py の動作確認テスト。

実行方法(リポジトリルートから):
    python backend/tests/test_plane_to_voxel_labels.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.structural_label import Plane, StructuralLabel  # noqa: E402
from plane_to_voxel_labels import build_voxel_labels  # noqa: E402
from space_definition_generator import generate_space_definition  # noqa: E402


def _make_space():
    """rotation_rad=0で単純化した1m^3グリッド点群+CoordinateDefinition。"""
    xs = np.linspace(0, 1, 10)
    ys = np.linspace(0, 1, 10)
    zs = np.linspace(0, 1, 5)
    grid = np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)
    space_def = generate_space_definition(grid, "TP01", rotation_rad=0.0)
    return space_def, grid


def _make_plane(plane_id, indices, points, label):
    idx = list(indices)
    pts = points[idx]
    return Plane(
        plane_id=plane_id, space_id="s1",
        coefficients=[0.0, 0.0, 1.0, 0.0], normal=[0.0, 0.0, 1.0],
        centroid=pts.mean(axis=0).tolist(), point_count=len(idx), point_indices=idx,
        suggested_label=label, confirmed_label=label,
    )


def test_ignore_plane_produces_no_voxel_labels():
    space_def, points = _make_space()
    plane = _make_plane("P001", range(0, 20), points, StructuralLabel.IGNORE)
    voxel_labels, history = build_voxel_labels("s1", space_def, [plane], points)
    assert voxel_labels == [], "IGNORE Planeからvoxel labelが生成されてしまった"
    assert history == []
    print("test_ignore_plane_produces_no_voxel_labels: OK")


def test_unassigned_plane_produces_no_voxel_labels():
    space_def, points = _make_space()
    plane = _make_plane("P001", range(0, 20), points, StructuralLabel.UNASSIGNED)
    voxel_labels, _ = build_voxel_labels("s1", space_def, [plane], points)
    assert voxel_labels == []
    print("test_unassigned_plane_produces_no_voxel_labels: OK")


def test_single_label_plane_resolves_without_conflict():
    space_def, points = _make_space()
    plane = _make_plane("P001", range(0, 20), points, StructuralLabel.FLOOR)
    voxel_labels, history = build_voxel_labels("s1", space_def, [plane], points)
    assert len(voxel_labels) > 0, "FLOOR Planeからvoxel labelが1件も生成されなかった"
    for v in voxel_labels:
        assert len(v.label_candidates) == 1
        assert v.label_candidates[0].label == StructuralLabel.FLOOR
        assert v.resolved_label == StructuralLabel.FLOOR
        assert v.source_plane_ids == ["P001"]
    assert len(history) == len(voxel_labels)
    print(f"test_single_label_plane_resolves_without_conflict: OK (voxel数={len(voxel_labels)})")


def test_conflicting_planes_are_not_silently_overwritten():
    space_def, points = _make_space()
    # 20..29 が重複区間(同一点が両方のPlaneに属する) -> 同一voxelに競合が発生する
    plane_wall = _make_plane("P_WALL", range(0, 30), points, StructuralLabel.WALL)
    plane_floor = _make_plane("P_FLOOR", range(20, 50), points, StructuralLabel.FLOOR)
    voxel_labels, _ = build_voxel_labels("s1", space_def, [plane_wall, plane_floor], points)

    conflicted = [v for v in voxel_labels if len(v.label_candidates) > 1]
    assert conflicted, "重複区間があるにもかかわらず、複数候補を持つvoxelが1件も無い"
    for v in conflicted:
        labels = {c.label for c in v.label_candidates}
        assert labels == {StructuralLabel.WALL, StructuralLabel.FLOOR}, f"候補が上書きされている: {labels}"
        assert set(v.source_plane_ids) == {"P_WALL", "P_FLOOR"}
        # 単純多数決で自動的にどちらかへ確定していないこと(fail-closed)
        assert v.resolved_label in (StructuralLabel.AMBIGUOUS, StructuralLabel.UNRESOLVED), (
            f"競合が自動的に確定されてしまった: {v.resolved_label}"
        )

    non_conflicted = [v for v in voxel_labels if len(v.label_candidates) == 1]
    assert any(v.resolved_label == StructuralLabel.WALL for v in non_conflicted)
    assert any(v.resolved_label == StructuralLabel.FLOOR for v in non_conflicted)
    print(f"test_conflicting_planes_are_not_silently_overwritten: OK (競合voxel数={len(conflicted)})")


def test_malformed_space_def_raises():
    _, points = _make_space()
    plane = _make_plane("P001", range(0, 5), points, StructuralLabel.FLOOR)
    try:
        build_voxel_labels("s1", {"degree": 0}, [plane], points)
        raise AssertionError("unit-sizeが無いspace_defが受理されてしまった")
    except ValueError:
        pass
    print("test_malformed_space_def_raises: OK")


def test_empty_points_raises():
    space_def, points = _make_space()
    plane = _make_plane("P001", range(0, 5), points, StructuralLabel.FLOOR)
    try:
        build_voxel_labels("s1", space_def, [plane], np.zeros((0, 3)))
        raise AssertionError("空のpointsが受理されてしまった")
    except ValueError:
        pass
    print("test_empty_points_raises: OK")


def test_out_of_range_point_indices_raises():
    space_def, points = _make_space()
    bad_plane = Plane(
        plane_id="P999", space_id="s1", coefficients=[0, 0, 1, 0], normal=[0, 0, 1],
        centroid=[0, 0, 0], point_count=1, point_indices=[len(points) + 100],
        suggested_label=StructuralLabel.FLOOR, confirmed_label=StructuralLabel.FLOOR,
    )
    try:
        build_voxel_labels("s1", space_def, [bad_plane], points)
        raise AssertionError("範囲外point_indicesが受理されてしまった")
    except ValueError:
        pass
    print("test_out_of_range_point_indices_raises: OK")


if __name__ == "__main__":
    test_ignore_plane_produces_no_voxel_labels()
    test_unassigned_plane_produces_no_voxel_labels()
    test_single_label_plane_resolves_without_conflict()
    test_conflicting_planes_are_not_silently_overwritten()
    test_malformed_space_def_raises()
    test_empty_points_raises()
    test_out_of_range_point_indices_raises()
    print()
    print("全テスト成功。")
