"""
backend/point_to_spatial_id.py の動作確認テスト。

実行方法(リポジトリルートから):
    python backend/tests/test_point_to_spatial_id.py

対象: 2026-08-29のzoom semantics統一(X/Y/Z共通のunit-size[zoom_level]に
変更し、Z方向の独立系列・クランプ処理を廃止した)。あらゆる形状(floor型・
縦長)でfinest levelがX/Y/Zとも常に3cmになることを確認する。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from point_to_spatial_id import world_points_to_spatial_ids  # noqa: E402
from space_definition_generator import finest_zoom_level, generate_space_definition  # noqa: E402
from spatial_id_constants import MIN_VOXEL_SIZE  # noqa: E402

_TOL = 1e-9


def _make_room_points(sx=10.0, sy=4.0, sz=3.0, nx=6, ny=6, nz=4) -> np.ndarray:
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    return np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)


def _finest_voxel_size(space_def: dict) -> float:
    zoom_level = finest_zoom_level(space_def)
    return space_def["unit-size"][str(zoom_level)]


def test_floor_type_space_finest_xyz_is_3cm():
    # 通常のfloor型空間(footprint >> height)
    points = _make_room_points(sx=10.0, sy=4.0, sz=3.0)
    space_def = generate_space_definition(points, "TPZF1", rotation_rad=0.0)
    voxel_size = _finest_voxel_size(space_def)
    assert math.isclose(voxel_size, MIN_VOXEL_SIZE, abs_tol=_TOL), (
        f"floor型空間でfinest voxel sizeが3cmでない: {voxel_size}"
    )
    print(f"test_floor_type_space_finest_xyz_is_3cm: OK (voxel_size={voxel_size})")


def test_tall_narrow_space_finest_xyz_is_3cm():
    # 5m x 5m x 12mの縦長空間(以前はZ側がクランプされ0.06mになっていた不具合ケース)
    points = _make_room_points(sx=5.0, sy=5.0, sz=12.0)
    space_def = generate_space_definition(points, "TPZTall", rotation_rad=0.0)
    voxel_size = _finest_voxel_size(space_def)
    assert math.isclose(voxel_size, MIN_VOXEL_SIZE, abs_tol=_TOL), (
        f"縦長空間でfinest voxel sizeが3cmでない(旧不具合の再発): {voxel_size}"
    )
    print(f"test_tall_narrow_space_finest_xyz_is_3cm: OK (voxel_size={voxel_size})")


def test_xy_extent_greater_than_z_extent():
    points = _make_room_points(sx=50.0, sy=20.0, sz=4.0)
    space_def = generate_space_definition(points, "TPZ_XYgtZ", rotation_rad=0.0)
    voxel_size = _finest_voxel_size(space_def)
    assert math.isclose(voxel_size, MIN_VOXEL_SIZE, abs_tol=_TOL)
    print(f"test_xy_extent_greater_than_z_extent: OK (voxel_size={voxel_size})")


def test_z_extent_greater_than_xy_extent():
    points = _make_room_points(sx=3.0, sy=3.0, sz=30.0)
    space_def = generate_space_definition(points, "TPZ_ZgtXY", rotation_rad=0.0)
    voxel_size = _finest_voxel_size(space_def)
    assert math.isclose(voxel_size, MIN_VOXEL_SIZE, abs_tol=_TOL)
    print(f"test_z_extent_greater_than_xy_extent: OK (voxel_size={voxel_size})")


def test_all_zoom_levels_use_same_size_for_xyz():
    """全zoom levelについて、X/Y/Zが同じunit-size[zoom_level]を使うことを、
    world_points_to_spatial_idsの実際の変換結果から検証する。

    rotation_rad=0のとき、既存の変換式(local_y = rel_x*sin(theta) -
    rel_y*cos(theta))によりlocal_y = -rel_yとなる(Y軸反転、既存仕様通り)。
    そのため、ワールド+Y方向へのオフセットはlocal側では負の変位になる。
    このテストはY方向には-offsetを与えて正のindexになるようにしている
    (符号の意味はpoint_to_spatial_id.pyのdocstring・det=-1の説明を参照。
    このテスト自体は「X/Y/Zとも同じvoxel_sizeで割られているか」だけを
    確認するものであり、Y軸反転の妥当性はPhase 1/3.1で別途確認済み)。
    """
    points = _make_room_points(sx=5.0, sy=5.0, sz=12.0)
    space_def = generate_space_definition(points, "TPZ_AllLevels", rotation_rad=0.0)

    origin = np.array(space_def["origin"])
    for zoom_level_str, voxel_size in space_def["unit-size"].items():
        zoom_level = int(zoom_level_str)
        # originから同じ距離(voxel_size * 1.5)だけX/Y/Z各方向にずらした点が、
        # すべて同じ絶対値のindex(1)を得ることを確認する(=同じサイズで割られている)
        offset = voxel_size * 1.5
        p_x = (origin + np.array([offset, 0.0, 0.0])).reshape(1, 3)
        p_y = (origin + np.array([0.0, -offset, 0.0])).reshape(1, 3)  # Y軸反転を考慮
        p_z = (origin + np.array([0.0, 0.0, offset])).reshape(1, 3)

        id_x = world_points_to_spatial_ids(p_x, space_def, zoom_level)[0]
        id_y = world_points_to_spatial_ids(p_y, space_def, zoom_level)[0]
        id_z = world_points_to_spatial_ids(p_z, space_def, zoom_level)[0]

        _, f_x, x_x, y_x = id_x.split("/")
        _, f_y, x_y, y_y = id_y.split("/")
        _, f_z, x_z, y_z = id_z.split("/")

        assert x_x == "1" and f_x == "0" and y_x == "0", f"zoom={zoom_level}: X方向の結果が不正: {id_x}"
        assert y_y == "1" and f_y == "0" and x_y == "0", f"zoom={zoom_level}: Y方向の結果が不正: {id_y}"
        assert f_z == "1" and x_z == "0" and y_z == "0", f"zoom={zoom_level}: Z方向の結果が不正: {id_z}"

    print(f"test_all_zoom_levels_use_same_size_for_xyz: OK (levels={len(space_def['unit-size'])})")


def test_world_points_to_spatial_ids_uses_3cm_grid():
    points = _make_room_points(sx=10.0, sy=4.0, sz=3.0)
    space_def = generate_space_definition(points, "TPZ2", rotation_rad=0.0)
    zoom_level = finest_zoom_level(space_def)

    origin_z = space_def["origin"][2]
    p1 = np.array([[space_def["origin"][0] + 1.0, space_def["origin"][1] + 1.0, origin_z + 0.005]])
    p2 = np.array([[space_def["origin"][0] + 1.0, space_def["origin"][1] + 1.0, origin_z + 0.035]])

    id1 = world_points_to_spatial_ids(p1, space_def, zoom_level)[0]
    id2 = world_points_to_spatial_ids(p2, space_def, zoom_level)[0]
    f1 = int(id1.split("/")[1])
    f2 = int(id2.split("/")[1])
    assert f2 == f1 + 1, f"3cm刻みのf indexになっていない(f1={f1}, f2={f2})"
    print(f"test_world_points_to_spatial_ids_uses_3cm_grid: OK (f1={f1}, f2={f2})")


if __name__ == "__main__":
    test_floor_type_space_finest_xyz_is_3cm()
    test_tall_narrow_space_finest_xyz_is_3cm()
    test_xy_extent_greater_than_z_extent()
    test_z_extent_greater_than_xy_extent()
    test_all_zoom_levels_use_same_size_for_xyz()
    test_world_points_to_spatial_ids_uses_3cm_grid()
    print()
    print("全テスト成功。")
