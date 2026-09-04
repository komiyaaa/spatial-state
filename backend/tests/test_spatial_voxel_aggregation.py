"""
backend/spatial_voxel_aggregation.py の動作確認テスト(ロードマップStep 3)。

実行方法(リポジトリルートから):
    python backend/tests/test_spatial_voxel_aggregation.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from local_spatial_id_hierarchy import parse_local_spatial_id  # noqa: E402
from point_cloud_voxelization import _voxel_center_from_local_spatial_id  # noqa: E402
from space_definition_generator import finest_zoom_level, generate_space_definition  # noqa: E402
from spatial_voxel_aggregation import aggregate_finest_positions_to_zoom_level  # noqa: E402

_TOL = 1e-6


def _make_room_points(sx=10.0, sy=4.0, sz=3.0, nx=6, ny=6, nz=4) -> np.ndarray:
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    return np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)


def _make_space(space_def_id="TAGG", rotation_rad=0.0) -> dict:
    points = _make_room_points()
    return generate_space_definition(points, space_def_id, rotation_rad=rotation_rad)


def _finest_center(space_def, f, x, y):
    finest = finest_zoom_level(space_def)
    return _voxel_center_from_local_spatial_id(f"{finest}/{f}/{x}/{y}", space_def)


def test_3cm_to_6cm_aggregation():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 1  # 1段粗い(0.03m -> 0.06m)
    assert math.isclose(space_def["unit-size"][str(target)], space_def["unit-size"][str(finest)] * 2, abs_tol=_TOL)

    positions = np.array([_finest_center(space_def, 0, 0, 0), _finest_center(space_def, 0, 1, 0)])
    result = aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, target)
    assert len(result) == 1, "finest(0,0,0)と(1,0,0)は同じ親(x//2==0)に入るはず"
    assert result[0].zoom_level == target
    assert result[0].source_voxel_count == 2
    print("test_3cm_to_6cm_aggregation: OK")


def test_3cm_to_12cm_aggregation():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 2  # 2段粗い(0.03m -> 0.12m、factor=4)
    positions = np.array([_finest_center(space_def, 0, i, 0) for i in range(4)])
    result = aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, target)
    assert len(result) == 1, "x=0,1,2,3は同じ親(x//4==0)に入るはず"
    assert result[0].source_voxel_count == 4
    assert math.isclose(result[0].voxel_size, space_def["unit-size"][str(finest)] * 4, abs_tol=_TOL)
    print("test_3cm_to_12cm_aggregation: OK")


def test_eight_children_become_one_parent():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 1  # factor=2、2x2x2=8個の子が1親に対応する
    positions = np.array([
        _finest_center(space_def, f, x, y)
        for f in (0, 1) for x in (0, 1) for y in (0, 1)
    ])
    assert len(positions) == 8
    result = aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, target)
    assert len(result) == 1, f"8個の子は1つの親に集約されるはずが{len(result)}個になった"
    assert result[0].source_voxel_count == 8
    print("test_eight_children_become_one_parent: OK")


def test_parent_id_format_is_z_f_x_y():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 1
    positions = np.array([_finest_center(space_def, 3, 5, 7)])
    result = aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, target)
    zoom, f, x, y = parse_local_spatial_id(result[0].local_spatial_id)
    assert zoom == target
    assert f == 3 // 2 and x == 5 // 2 and y == 7 // 2
    print(f"test_parent_id_format_is_z_f_x_y: OK ({result[0].local_spatial_id})")


def test_parent_voxel_center_is_theoretical_grid_center():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 1
    positions = np.array([_finest_center(space_def, 2, 4, 6)])
    result = aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, target)

    expected_center = _voxel_center_from_local_spatial_id(result[0].local_spatial_id, space_def)
    for a, b in zip(result[0].voxel_center, expected_center):
        assert math.isclose(a, b, abs_tol=_TOL), "voxel_centerが親gridの理論中心と一致しない"
    print("test_parent_voxel_center_is_theoretical_grid_center: OK")


def test_uses_only_its_own_unit_size_hierarchy():
    space_small = _make_space(space_def_id="Small")
    # 別のLocal Space(仮に単位系が違うとして)のunit-sizeを混ぜて使っていないことを、
    # 実際にspace_small自身のunit-sizeだけを参照して結果が決まることで確認する
    finest = finest_zoom_level(space_small)
    target = finest - 1
    positions = np.array([_finest_center(space_small, 0, 0, 0)])
    result = aggregate_finest_positions_to_zoom_level("spaceSmall", space_small, positions, finest, target)
    assert math.isclose(result[0].voxel_size, space_small["unit-size"][str(target)], abs_tol=_TOL)
    print("test_uses_only_its_own_unit_size_hierarchy: OK")


def test_same_id_string_distinct_across_spaces():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 1
    positions = np.array([_finest_center(space_def, 0, 0, 0), _finest_center(space_def, 1, 0, 0)])

    result_a = aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, target)
    result_b = aggregate_finest_positions_to_zoom_level("spaceB", space_def, positions, finest, target)
    assert result_a[0].local_spatial_id == result_b[0].local_spatial_id
    assert result_a[0].space_id != result_b[0].space_id
    combined = {(v.space_id, v.local_spatial_id) for v in result_a + result_b}
    assert len(combined) == 2, "同じID文字列が異なるspace_idとして区別されていない"
    print("test_same_id_string_distinct_across_spaces: OK")


def test_finest_level_selection_matches_original_set():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    positions = np.array([
        _finest_center(space_def, 0, 0, 0),
        _finest_center(space_def, 0, 1, 0),
        _finest_center(space_def, 1, 0, 0),
    ])
    # target == finest(Δ=0)の場合、集約は恒等になるはず
    result = aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, finest)
    assert len(result) == len(positions), "finest選択時に元のvoxel集合と一致しない"
    assert all(v.source_voxel_count == 1 for v in result)
    result_centers = sorted(tuple(round(c, 6) for c in v.voxel_center) for v in result)
    input_centers = sorted(tuple(round(c, 6) for c in p) for p in positions)
    assert result_centers == input_centers
    print("test_finest_level_selection_matches_original_set: OK")


def test_target_finer_than_finest_raises():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    positions = np.array([_finest_center(space_def, 0, 0, 0)])
    try:
        aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, finest + 1)
        raise AssertionError("finestより細かいzoom_levelが受理されてしまった")
    except ValueError:
        pass
    print("test_target_finer_than_finest_raises: OK")


def test_nonexistent_zoom_level_raises():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    positions = np.array([_finest_center(space_def, 0, 0, 0)])
    try:
        aggregate_finest_positions_to_zoom_level("spaceA", space_def, positions, finest, -999)
        raise AssertionError("存在しないzoom_levelが受理されてしまった")
    except ValueError:
        pass
    print("test_nonexistent_zoom_level_raises: OK")


def test_malformed_space_def_raises():
    positions = np.array([[0.0, 0.0, 0.0]])
    try:
        aggregate_finest_positions_to_zoom_level("spaceA", {"origin": [0, 0, 0]}, positions, 5, 4)
        raise AssertionError("unit-size/rad欠落のspace_defが受理されてしまった")
    except ValueError:
        pass
    print("test_malformed_space_def_raises: OK")


def test_empty_positions_raises():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    try:
        aggregate_finest_positions_to_zoom_level("spaceA", space_def, np.zeros((0, 3)), finest, finest - 1)
        raise AssertionError("空のfinest_positionsが受理されてしまった")
    except ValueError:
        pass
    print("test_empty_positions_raises: OK")


if __name__ == "__main__":
    test_3cm_to_6cm_aggregation()
    test_3cm_to_12cm_aggregation()
    test_eight_children_become_one_parent()
    test_parent_id_format_is_z_f_x_y()
    test_parent_voxel_center_is_theoretical_grid_center()
    test_uses_only_its_own_unit_size_hierarchy()
    test_same_id_string_distinct_across_spaces()
    test_finest_level_selection_matches_original_set()
    test_target_finer_than_finest_raises()
    test_nonexistent_zoom_level_raises()
    test_malformed_space_def_raises()
    test_empty_positions_raises()
    print()
    print("全テスト成功。")
