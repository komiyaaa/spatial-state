"""
backend/local_spatial_id_hierarchy.py の動作確認テスト(ロードマップStep 3の
整理、2026-08-30)。parent_local_spatial_id()が座標変換を一切経由せず、
ID階層関係だけで親IDを直接決定できることを確認する。

実行方法(リポジトリルートから):
    python backend/tests/test_local_spatial_id_hierarchy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from local_spatial_id_hierarchy import parent_local_spatial_id, parse_local_spatial_id  # noqa: E402

# 座標変換(origin/rad)を一切使わない、unit-sizeのみのCoordinateDefinition。
# 「voxel_center等の物理座標へ一度も変換していない」ことを明示するため、
# originを敢えて含めていない(それでも動くことがこのテストの主眼)。
_COORDINATE_DEFINITION = {
    "unit-size": {
        "0": 1.92, "1": 0.96, "2": 0.48, "3": 0.24,
        "4": 0.12, "5": 0.06, "6": 0.03,
    },
}


def test_parse_local_spatial_id():
    assert parse_local_spatial_id("6/3/5/7") == (6, 3, 5, 7)
    assert parse_local_spatial_id("0/-1/-2/-3") == (0, -1, -2, -3)
    print("test_parse_local_spatial_id: OK")


def test_direct_id_to_parent_id_one_level():
    # zoom 6(finest, 0.03m) -> zoom 5(0.06m): factor=2
    parent = parent_local_spatial_id("6/3/5/7", 5, _COORDINATE_DEFINITION)
    assert parent == "5/1/2/3", f"期待値と異なる: {parent}"
    print("test_direct_id_to_parent_id_one_level: OK")


def test_direct_id_to_parent_id_two_levels():
    # zoom 6 -> zoom 4(0.12m): factor=4
    parent = parent_local_spatial_id("6/9/10/11", 4, _COORDINATE_DEFINITION)
    zoom, f, x, y = parse_local_spatial_id(parent)
    assert zoom == 4
    assert f == 9 // 4 and x == 10 // 4 and y == 11 // 4
    print(f"test_direct_id_to_parent_id_two_levels: OK ({parent})")


def test_identity_when_target_equals_child_zoom():
    result = parent_local_spatial_id("6/3/5/7", 6, _COORDINATE_DEFINITION)
    assert result == "6/3/5/7"
    print("test_identity_when_target_equals_child_zoom: OK")


def test_negative_indices_use_floor_division():
    # Pythonの // は負数もfloor方向に丸める(floor()と一致させる、既存のforward変換と同じ規約)
    parent = parent_local_spatial_id("6/-3/-5/-1", 5, _COORDINATE_DEFINITION)
    zoom, f, x, y = parse_local_spatial_id(parent)
    assert f == -3 // 2 == -2
    assert x == -5 // 2 == -3
    assert y == -1 // 2 == -1
    print(f"test_negative_indices_use_floor_division: OK ({parent})")


def test_target_zoom_not_in_hierarchy_raises():
    try:
        parent_local_spatial_id("6/0/0/0", 999, _COORDINATE_DEFINITION)
        raise AssertionError("存在しないtarget_zoom_levelが受理されてしまった")
    except ValueError:
        pass
    print("test_target_zoom_not_in_hierarchy_raises: OK")


def test_target_finer_than_child_raises():
    try:
        parent_local_spatial_id("4/0/0/0", 6, _COORDINATE_DEFINITION)
        raise AssertionError("子より細かいtarget_zoom_levelが受理されてしまった")
    except ValueError:
        pass
    print("test_target_finer_than_child_raises: OK")


def test_child_zoom_not_in_hierarchy_raises():
    try:
        parent_local_spatial_id("999/0/0/0", 5, _COORDINATE_DEFINITION)
        raise AssertionError("存在しない子zoom levelが受理されてしまった")
    except ValueError:
        pass
    print("test_child_zoom_not_in_hierarchy_raises: OK")


def test_non_power_of_two_hierarchy_raises():
    # voxel_size比が2の階乗になっていないunit-size(不正なCoordinateDefinition)
    bad_definition = {"unit-size": {"0": 0.05, "1": 0.03}}  # 比が5/3、2の階乗でない
    try:
        parent_local_spatial_id("1/0/0/0", 0, bad_definition)
        raise AssertionError("2の階乗でない階層が受理されてしまった")
    except ValueError:
        pass
    print("test_non_power_of_two_hierarchy_raises: OK")


def test_missing_unit_size_raises():
    try:
        parent_local_spatial_id("6/0/0/0", 5, {"origin": [0, 0, 0]})
        raise AssertionError("unit-size欠落のcoordinate_definitionが受理されてしまった")
    except ValueError:
        pass
    print("test_missing_unit_size_raises: OK")


def test_does_not_require_origin_or_rad():
    """parent ID決定に座標変換(origin/rad)が一切不要であることを、それらを
    含まないCoordinateDefinitionでも正しく動作することで直接確認する。"""
    assert "origin" not in _COORDINATE_DEFINITION and "rad" not in _COORDINATE_DEFINITION
    parent = parent_local_spatial_id("6/4/4/4", 5, _COORDINATE_DEFINITION)
    assert parent == "5/2/2/2"
    print("test_does_not_require_origin_or_rad: OK")


if __name__ == "__main__":
    test_parse_local_spatial_id()
    test_direct_id_to_parent_id_one_level()
    test_direct_id_to_parent_id_two_levels()
    test_identity_when_target_equals_child_zoom()
    test_negative_indices_use_floor_division()
    test_target_zoom_not_in_hierarchy_raises()
    test_target_finer_than_child_raises()
    test_child_zoom_not_in_hierarchy_raises()
    test_non_power_of_two_hierarchy_raises()
    test_missing_unit_size_raises()
    test_does_not_require_origin_or_rad()
    print()
    print("全テスト成功。")
