"""
backend/point_cloud_voxelization.py の動作確認テスト(ロードマップStep 1)。

実行方法(リポジトリルートから):
    python backend/tests/test_point_cloud_voxelization.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from point_cloud_voxelization import voxelize_base_map_points  # noqa: E402
from point_to_spatial_id import world_points_to_spatial_ids  # noqa: E402
from space_definition_generator import finest_zoom_level, generate_space_definition  # noqa: E402

_TOL = 1e-9


def _make_room_points(sx=10.0, sy=4.0, sz=3.0, nx=6, ny=6, nz=4) -> np.ndarray:
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    return np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)


def _make_space(sx=10.0, sy=4.0, sz=3.0, space_def_id="TSV") -> dict:
    points = _make_room_points(sx, sy, sz)
    return generate_space_definition(points, space_def_id, rotation_rad=0.0)


def test_base_map_points_convert_to_finest_local_spatial_ids():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    points = _make_room_points()
    voxels = voxelize_base_map_points("spaceA", space_def, points)
    assert len(voxels) > 0
    for v in voxels:
        zoom_str, f, x, y = v.local_spatial_id.split("/")
        assert int(zoom_str) == finest, f"finest zoomが使われていない: {v.local_spatial_id}"
        assert v.zoom_level == finest
    print(f"test_base_map_points_convert_to_finest_local_spatial_ids: OK (voxel数={len(voxels)})")


def test_finest_voxel_size_is_3cm():
    space_def = _make_space()
    points = _make_room_points()
    voxels = voxelize_base_map_points("spaceA", space_def, points)
    for v in voxels:
        assert math.isclose(v.voxel_size, 0.03, abs_tol=_TOL), f"voxel_sizeが3cmでない: {v.voxel_size}"
    print("test_finest_voxel_size_is_3cm: OK")


def test_points_in_same_voxel_are_aggregated():
    space_def = _make_space()
    origin = np.array(space_def["origin"])
    # 3cm未満の範囲に5点集める(同一voxelに入る)
    base = origin + np.array([1.0, 1.0, 1.0])
    cluster = base + np.random.default_rng(0).uniform(-0.01, 0.01, size=(5, 3))
    voxels = voxelize_base_map_points("spaceA", space_def, cluster)
    assert len(voxels) == 1, f"同一voxelに集約されるはずが複数voxelになった: {len(voxels)}"
    assert voxels[0].point_count == 5
    print("test_points_in_same_voxel_are_aggregated: OK")


def test_points_in_different_voxels_are_separated():
    space_def = _make_space()
    origin = np.array(space_def["origin"])
    # 1mずつ離れた点(voxel_size=0.03mなので確実に別voxel)
    pts = origin + np.array([
        [1.0, 1.0, 1.0],
        [2.0, 1.0, 1.0],
        [3.0, 1.0, 1.0],
    ])
    voxels = voxelize_base_map_points("spaceA", space_def, pts)
    assert len(voxels) == 3, f"別voxelに分離されるはずが集約された: {len(voxels)}"
    assert all(v.point_count == 1 for v in voxels)
    print("test_points_in_different_voxels_are_separated: OK")


def test_voxels_are_identified_by_space_id_and_local_spatial_id():
    space_def = _make_space()
    points = _make_room_points()
    voxels = voxelize_base_map_points("spaceA", space_def, points)
    keys = [(v.space_id, v.local_spatial_id) for v in voxels]
    assert len(keys) == len(set(keys)), "(space_id, local_spatial_id)に重複がある"
    print("test_voxels_are_identified_by_space_id_and_local_spatial_id: OK")


def test_same_local_spatial_id_string_is_distinct_across_spaces():
    space_def = _make_space()
    points = _make_room_points()
    voxels_a = voxelize_base_map_points("spaceA", space_def, points)
    voxels_b = voxelize_base_map_points("spaceB", space_def, points)  # 同じspace_def・同じ点群

    ids_a = {v.local_spatial_id for v in voxels_a}
    ids_b = {v.local_spatial_id for v in voxels_b}
    assert ids_a == ids_b, "前提が崩れている(同じ入力なら同じLocal Spatial ID集合になるはず)"

    combined = {}
    for v in voxels_a + voxels_b:
        key = (v.space_id, v.local_spatial_id)
        assert key not in combined, f"異なるspace_idのvoxelが衝突した: {key}"
        combined[key] = v
    assert len(combined) == len(voxels_a) + len(voxels_b), (
        "同じLocal Spatial ID文字列が別Local Spaceとして扱われていない"
    )
    print("test_same_local_spatial_id_string_is_distinct_across_spaces: OK")


def test_uses_only_its_own_coordinate_definition():
    # 単位系(unit-size)がまったく異なる2つのLocal Spaceを用意する
    space_def_small = _make_space(sx=2.0, sy=2.0, sz=2.0, space_def_id="SmallSpace")
    space_def_large = _make_space(sx=200.0, sy=100.0, sz=5.0, space_def_id="LargeSpace")
    assert space_def_small["unit-size"] != space_def_large["unit-size"], "前提: unit-size系列が異なること"

    points_small = _make_room_points(sx=2.0, sy=2.0, sz=2.0)
    points_large = _make_room_points(sx=200.0, sy=100.0, sz=5.0)

    voxels_small = voxelize_base_map_points("spaceSmall", space_def_small, points_small)
    voxels_large = voxelize_base_map_points("spaceLarge", space_def_large, points_large)

    # どちらもfinestは3cmだが、段数(zoom_level)自体は空間ごとに異なってよい
    assert voxels_small[0].zoom_level != voxels_large[0].zoom_level, (
        "異なるLocal Spaceのunit-size段数が混同されている"
    )
    for v in voxels_small + voxels_large:
        assert math.isclose(v.voxel_size, 0.03, abs_tol=_TOL)
    print("test_uses_only_its_own_coordinate_definition: OK")


def test_malformed_coordinate_definition_raises():
    points = _make_room_points()

    try:
        voxelize_base_map_points("spaceA", {"origin": [0, 0, 0], "rad": 0.0}, points)  # unit-size欠落
        raise AssertionError("unit-size欠落のspace_defが受理されてしまった")
    except ValueError:
        pass

    try:
        voxelize_base_map_points("spaceA", {"rad": 0.0, "unit-size": {"0": 0.03}}, points)  # origin欠落
        raise AssertionError("origin欠落のspace_defが受理されてしまった")
    except ValueError:
        pass

    try:
        voxelize_base_map_points("spaceA", {"origin": [0, 0, 0], "rad": 0.0, "unit-size": {}}, points)
        raise AssertionError("空のunit-sizeが受理されてしまった")
    except ValueError:
        pass

    space_def = _make_space()
    try:
        voxelize_base_map_points("spaceA", space_def, np.zeros((0, 3)))
        raise AssertionError("空のpointsが受理されてしまった")
    except ValueError:
        pass

    print("test_malformed_coordinate_definition_raises: OK")


def test_mean_color_is_computed_when_colors_given():
    space_def = _make_space()
    origin = np.array(space_def["origin"])
    cluster = origin + np.array([1.0, 1.0, 1.0]) + np.array([[0, 0, 0], [0.001, 0, 0]])
    colors = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    voxels = voxelize_base_map_points("spaceA", space_def, cluster, colors=colors)
    assert len(voxels) == 1
    assert voxels[0].mean_color is not None
    expected = [0.5, 0.5, 0.0]  # mean([1,0,0], [0,1,0])
    for c, e in zip(voxels[0].mean_color, expected):
        assert math.isclose(c, e, abs_tol=_TOL)
    print("test_mean_color_is_computed_when_colors_given: OK")


def test_voxel_center_unaffected_by_point_position_within_voxel():
    space_def = _make_space()
    origin = np.array(space_def["origin"])
    base = origin + np.array([1.0, 1.0, 1.0])
    points_a = (base + np.array([0.001, 0.002, -0.001])).reshape(1, 3)
    points_b = (base + np.array([-0.004, 0.003, 0.004])).reshape(1, 3)

    voxels_a = voxelize_base_map_points("spaceA", space_def, points_a)
    voxels_b = voxelize_base_map_points("spaceA", space_def, points_b)
    assert voxels_a[0].local_spatial_id == voxels_b[0].local_spatial_id, "前提: 同じvoxelに入ること"

    for ca, cb in zip(voxels_a[0].voxel_center, voxels_b[0].voxel_center):
        assert math.isclose(ca, cb, abs_tol=_TOL), (
            f"同一voxel内で支持点位置を変えたのにvoxel_centerが変化した: {ca} != {cb}"
        )
    print("test_voxel_center_unaffected_by_point_position_within_voxel: OK")


def test_point_centroid_changes_with_point_position():
    space_def = _make_space()
    origin = np.array(space_def["origin"])
    base = origin + np.array([1.0, 1.0, 1.0])
    points_a = (base + np.array([0.001, 0.002, -0.001])).reshape(1, 3)
    points_b = (base + np.array([-0.004, 0.003, 0.004])).reshape(1, 3)

    voxels_a = voxelize_base_map_points("spaceA", space_def, points_a)
    voxels_b = voxelize_base_map_points("spaceA", space_def, points_b)
    assert voxels_a[0].local_spatial_id == voxels_b[0].local_spatial_id, "前提: 同じvoxelに入ること"

    differs = any(
        not math.isclose(ca, cb, abs_tol=1e-6)
        for ca, cb in zip(voxels_a[0].point_centroid, voxels_b[0].point_centroid)
    )
    assert differs, "支持点位置を変えたのにpoint_centroidが変化しなかった"
    print("test_point_centroid_changes_with_point_position: OK")


def test_adjacent_voxel_centers_are_0_03m_apart():
    space_def = _make_space()
    origin = np.array(space_def["origin"])
    voxel_size = space_def["unit-size"][str(finest_zoom_level(space_def))]
    p1 = (origin + np.array([1.5 * voxel_size, 1.0, 1.0])).reshape(1, 3)
    p2 = (origin + np.array([2.5 * voxel_size, 1.0, 1.0])).reshape(1, 3)

    v1 = voxelize_base_map_points("spaceA", space_def, p1)[0]
    v2 = voxelize_base_map_points("spaceA", space_def, p2)[0]
    assert v1.local_spatial_id != v2.local_spatial_id, "前提: 隣接する別voxelであること"

    dist = math.dist(v1.voxel_center, v2.voxel_center)
    assert math.isclose(dist, voxel_size, abs_tol=_TOL), f"隣接voxel間隔が{voxel_size}mでない: {dist}"
    print(f"test_adjacent_voxel_centers_are_0_03m_apart: OK (dist={dist})")


def test_voxel_center_respects_rotation():
    points = _make_room_points()
    space_def = generate_space_definition(points, "TSV_ROT", rotation_rad=math.radians(37.0))
    p = np.array([[space_def["origin"][0] + 2.0, space_def["origin"][1] + 1.0, space_def["origin"][2] + 0.5]])
    voxels = voxelize_base_map_points("spaceRot", space_def, p)
    assert len(voxels) == 1
    v = voxels[0]

    zoom_str, f_str, x_str, y_str = v.local_spatial_id.split("/")
    voxel_size = space_def["unit-size"][zoom_str]
    local_x = (int(x_str) + 0.5) * voxel_size
    local_y = (int(y_str) + 0.5) * voxel_size
    local_z = (int(f_str) + 0.5) * voxel_size
    theta = space_def["rad"]

    # そのCoordinateDefinitionのrotationを使った期待値
    rel_x = local_x * math.cos(theta) + local_y * math.sin(theta)
    rel_y = local_x * math.sin(theta) - local_y * math.cos(theta)
    expected = [rel_x + space_def["origin"][0], rel_y + space_def["origin"][1], local_z + space_def["origin"][2]]
    for a, b in zip(v.voxel_center, expected):
        assert math.isclose(a, b, abs_tol=_TOL), f"voxel_centerがCoordinateDefinitionのrotationに従っていない"

    # 回転を無視(theta=0)した素朴な計算とは異なる値になること(rotationが実際に効いていることの確認)
    naive = [local_x + space_def["origin"][0], local_y + space_def["origin"][1], local_z + space_def["origin"][2]]
    assert not math.isclose(v.voxel_center[0], naive[0], abs_tol=1e-6) or not math.isclose(
        v.voxel_center[1], naive[1], abs_tol=1e-6
    ), "rotation=0とrotation=37度で同じvoxel_centerになってしまった(rotationが無視されている)"
    print("test_voxel_center_respects_rotation: OK")


def test_voxel_center_round_trips_to_same_local_spatial_id():
    points = _make_room_points()
    space_def = generate_space_definition(points, "TSV_RT", rotation_rad=math.radians(15.0))
    voxels = voxelize_base_map_points("spaceRT", space_def, points)
    zoom_level = finest_zoom_level(space_def)

    for v in voxels[:30]:  # 全件だと重いのでサンプリング
        recomputed_id = world_points_to_spatial_ids(np.array([v.voxel_center]), space_def, zoom_level)[0]
        assert recomputed_id == v.local_spatial_id, (
            f"voxel_centerを再変換しても元のlocal_spatial_idに戻らない: "
            f"{v.voxel_center} -> {recomputed_id} != {v.local_spatial_id}"
        )
    print(f"test_voxel_center_round_trips_to_same_local_spatial_id: OK (検証件数={min(30, len(voxels))})")


if __name__ == "__main__":
    test_base_map_points_convert_to_finest_local_spatial_ids()
    test_finest_voxel_size_is_3cm()
    test_points_in_same_voxel_are_aggregated()
    test_points_in_different_voxels_are_separated()
    test_voxels_are_identified_by_space_id_and_local_spatial_id()
    test_same_local_spatial_id_string_is_distinct_across_spaces()
    test_uses_only_its_own_coordinate_definition()
    test_malformed_coordinate_definition_raises()
    test_mean_color_is_computed_when_colors_given()
    test_voxel_center_unaffected_by_point_position_within_voxel()
    test_point_centroid_changes_with_point_position()
    test_adjacent_voxel_centers_are_0_03m_apart()
    test_voxel_center_respects_rotation()
    test_voxel_center_round_trips_to_same_local_spatial_id()
    print()
    print("全テスト成功。")
