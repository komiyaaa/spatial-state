"""
backend/space_definition_generator.py の動作確認テスト。

実行方法(リポジトリルートから):
    python backend/tests/test_space_definition_generator.py

対象: 2026-08-29の変更(明示的rotation_radの受け付け、minimum voxel sizeを
厳密な最小値として扱う=target_zoom_levelのための自動縮小を廃止)。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from space_definition_generator import (  # noqa: E402
    MIN_BASE_UNIT_SIZE,
    MIN_VOXEL_SIZE,
    _build_unit_size_table,
    finest_zoom_level,
    generate_space_definition,
)

_TOL = 1e-9


def _make_box_points(nx=6, ny=6, nz=4, sx=10.0, sy=4.0, sz=3.0) -> np.ndarray:
    """軸に沿った直方体状の点群(PCAが自然に主軸を検出できる、細長い形)を作る。"""
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    grid = np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3)
    return grid


def test_explicit_rotation_bypasses_pca_and_is_not_normalized():
    points = _make_box_points()
    # PCAなら-45〜45度に正規化されるはずの範囲外の値をあえて指定し、
    # 正規化されずそのまま使われることを確認する。
    explicit_rad = math.radians(123.456)
    space_def = generate_space_definition(points, "T001", base_unit_size=0.5, rotation_rad=explicit_rad)
    assert math.isclose(space_def["rad"], explicit_rad, abs_tol=_TOL), (
        f"明示的rotation_radがそのまま使われていない: {space_def['rad']} != {explicit_rad}"
    )
    assert math.isclose(space_def["degree"], 123.456, abs_tol=1e-6)
    print("test_explicit_rotation_bypasses_pca_and_is_not_normalized: OK")


def test_pca_auto_detection_still_works_when_rotation_rad_is_none():
    points = _make_box_points()
    space_def = generate_space_definition(points, "T002", base_unit_size=0.5, rotation_rad=None)
    # PCA自動検出時は-45°〜45°に正規化される
    assert -45.0 - 1e-6 <= space_def["degree"] <= 45.0 + 1e-6, (
        f"PCA自動検出のdegreeが正規化範囲外: {space_def['degree']}"
    )
    print("test_pca_auto_detection_still_works_when_rotation_rad_is_none: OK")


def test_origin_equals_bounds_index2_for_both_paths():
    points = _make_box_points()
    for rotation_rad in (None, math.radians(17.0)):
        space_def = generate_space_definition(points, "T003", base_unit_size=0.5, rotation_rad=rotation_rad)
        origin = space_def["origin"]
        bounds_2 = space_def["bounds"][2]
        for a, b in zip(origin, bounds_2):
            assert math.isclose(a, b, abs_tol=1e-6), f"origin != bounds[2] (rotation_rad={rotation_rad})"
    print("test_origin_equals_bounds_index2_for_both_paths: OK")


def test_build_unit_size_table_strict_minimum_no_shrink():
    # required_size=10m, base_unit_size=0.3m: 0.3,0.6,1.2,2.4,4.8,9.6,19.2 (>=10) -> 7段
    table = _build_unit_size_table(required_size=10.0, base_unit_size=0.3)
    sizes = [table[str(i)] for i in range(len(table))]
    # 最終(最も細かい)段が、指定したbase_unit_sizeちょうどであること(縮小されない)
    assert math.isclose(sizes[-1], 0.3, abs_tol=_TOL), f"minimum voxel sizeが縮小された: {sizes[-1]}"
    # 降順であること、最上位が全体を包含すること
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] >= 10.0
    assert sizes[0] / 2 < 10.0  # 「包含できた最初のサイズ」であることの確認(それより1段粗いと収まらない)
    print(f"test_build_unit_size_table_strict_minimum_no_shrink: OK (levels={len(sizes)}, sizes={sizes})")


def test_zoom_level_count_is_independent_per_space():
    # 同じrequired_sizeでも、base_unit_sizeが異なれば段数が変わってよい(target_zoom_levelに縛られない)
    table_a = _build_unit_size_table(required_size=100.0, base_unit_size=0.1)
    table_b = _build_unit_size_table(required_size=100.0, base_unit_size=1.0)
    assert len(table_a) != len(table_b), "minimum voxel sizeを変えても段数が変わっていない"
    assert math.isclose(table_a[str(len(table_a) - 1)], 0.1, abs_tol=_TOL)
    assert math.isclose(table_b[str(len(table_b) - 1)], 1.0, abs_tol=_TOL)
    print(
        f"test_zoom_level_count_is_independent_per_space: OK "
        f"(0.1m -> {len(table_a)}段, 1.0m -> {len(table_b)}段)"
    )


def test_base_unit_size_below_minimum_raises():
    points = _make_box_points()
    try:
        generate_space_definition(points, "T004", base_unit_size=MIN_BASE_UNIT_SIZE / 2, rotation_rad=0.0)
        raise AssertionError("MIN_BASE_UNIT_SIZE未満が受理されてしまった")
    except ValueError:
        pass
    print("test_base_unit_size_below_minimum_raises: OK")


def test_default_minimum_voxel_size_is_3cm():
    # 2026-08-29: 構造平面ラベリング機能の追加に伴い、既定値は常に0.03m(3cm)固定
    assert math.isclose(MIN_VOXEL_SIZE, 0.03, abs_tol=1e-9)
    points = _make_box_points(sx=10.0, sy=4.0, sz=3.0)
    space_def = generate_space_definition(points, "T005", rotation_rad=0.0)  # base_unit_size省略
    finest = finest_zoom_level(space_def)
    assert math.isclose(space_def["unit-size"][str(finest)], 0.03, abs_tol=1e-9), (
        f"既定のminimum voxel sizeが3cmになっていない: {space_def['unit-size'][str(finest)]}"
    )
    print(f"test_default_minimum_voxel_size_is_3cm: OK (zoom_level_count={finest + 1})")


def test_zoom_level_count_varies_with_space_size():
    # 建物サイズが異なれば、同じ0.03m基準でもzoom level数が変わってよい
    small = generate_space_definition(_make_box_points(sx=2.0, sy=1.0, sz=1.0), "T006", rotation_rad=0.0)
    large = generate_space_definition(_make_box_points(sx=50.0, sy=20.0, sz=3.0), "T007", rotation_rad=0.0)
    assert len(small["unit-size"]) != len(large["unit-size"]), (
        "Local Spaceの大きさを変えてもzoom level数が変化していない"
    )
    # 両方とも最小値は3cmで共通
    assert math.isclose(small["unit-size"][str(finest_zoom_level(small))], 0.03, abs_tol=1e-9)
    assert math.isclose(large["unit-size"][str(finest_zoom_level(large))], 0.03, abs_tol=1e-9)
    print(
        f"test_zoom_level_count_varies_with_space_size: OK "
        f"(small={len(small['unit-size'])}段, large={len(large['unit-size'])}段)"
    )


if __name__ == "__main__":
    test_explicit_rotation_bypasses_pca_and_is_not_normalized()
    test_pca_auto_detection_still_works_when_rotation_rad_is_none()
    test_origin_equals_bounds_index2_for_both_paths()
    test_build_unit_size_table_strict_minimum_no_shrink()
    test_zoom_level_count_is_independent_per_space()
    test_base_unit_size_below_minimum_raises()
    test_default_minimum_voxel_size_is_3cm()
    test_zoom_level_count_varies_with_space_size()
    print()
    print("全テスト成功。")
