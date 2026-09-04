"""
backend/voxel_color_strategy.py の動作確認テスト(ロードマップStep 4)。

実行方法(リポジトリルートから):
    python backend/tests/test_voxel_color_strategy.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.structural_label import LabelCandidate, SpatialVoxelLabel, StructuralLabel  # noqa: E402
from domain.visualization import VisualizationMode  # noqa: E402
from local_spatial_id_hierarchy import parent_local_spatial_id  # noqa: E402
from space_definition_generator import finest_zoom_level, generate_space_definition  # noqa: E402
from visualization_colors import CATEGORY_TO_CODE  # noqa: E402
from voxel_color_strategy import build_color_codes_for_mode  # noqa: E402

_TOL = 1e-9


def _make_room_points(sx=10.0, sy=4.0, sz=3.0, nx=6, ny=6, nz=4) -> np.ndarray:
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    return np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)


def _make_space(space_def_id="TVCS") -> dict:
    return generate_space_definition(_make_room_points(), space_def_id, rotation_rad=0.0)


def _label(sid, space_id, label):
    return SpatialVoxelLabel(
        space_id=space_id, local_spatial_id=sid,
        label_candidates=[LabelCandidate(label=label, fitness=1.0)],
        resolved_label=label,
    )


def test_default_mode_all_same_code():
    codes, tallies = build_color_codes_for_mode(
        "spaceA", _make_space(), 9, 9, ["9/0/0/0", "9/0/1/0", "9/0/0/1"], VisualizationMode.DEFAULT,
    )
    assert len(codes) == 3
    assert all(c == CATEGORY_TO_CODE["DEFAULT"] for c in codes)
    assert tallies == []
    print("test_default_mode_all_same_code: OK")


def test_finest_label_joins_by_space_id_and_local_spatial_id():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    labels = {"11/0/0/0": _label("11/0/0/0", "spaceA", StructuralLabel.FLOOR)}
    ids = ["11/0/0/0", "11/0/1/0"]  # 2つ目はラベル無し
    codes, tallies = build_color_codes_for_mode(
        "spaceA", space_def, finest, finest, ids, VisualizationMode.STRUCTURAL_LABEL, finest_labels=labels,
    )
    assert codes[0] == CATEGORY_TO_CODE["FLOOR"]
    assert codes[1] == CATEGORY_TO_CODE["NO_LABEL"]
    assert tallies[0].space_id == "spaceA" and tallies[0].local_spatial_id == "11/0/0/0"
    print("test_finest_label_joins_by_space_id_and_local_spatial_id: OK")


def test_same_id_string_different_space_id_not_mixed():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    # spaceAのラベル辞書には"11/0/0/0"がFLOORとして存在するが、
    # spaceB用の呼び出しには(呼び出し側の責務として)渡さない -> NO_LABELになるはず
    labels_for_space_a = {"11/0/0/0": _label("11/0/0/0", "spaceA", StructuralLabel.FLOOR)}
    codes_b, _ = build_color_codes_for_mode(
        "spaceB", space_def, finest, finest, ["11/0/0/0"], VisualizationMode.STRUCTURAL_LABEL,
        finest_labels={},  # spaceBには対応するラベルが無い(正しいjoin対象は呼び出し側が用意する)
    )
    assert codes_b[0] == CATEGORY_TO_CODE["NO_LABEL"], (
        "spaceAのラベルがspaceBのcoloringに混入してしまった"
    )
    codes_a, _ = build_color_codes_for_mode(
        "spaceA", space_def, finest, finest, ["11/0/0/0"], VisualizationMode.STRUCTURAL_LABEL,
        finest_labels=labels_for_space_a,
    )
    assert codes_a[0] == CATEGORY_TO_CODE["FLOOR"]
    print("test_same_id_string_different_space_id_not_mixed: OK")


def test_parent_level_aggregation_uses_majority():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 1  # factor=2

    # 子8個のうち6個FLOOR・2個WALL -> 親はFLOOR
    child_ids = [f"{finest}/{f}/{x}/{y}" for f in (0, 1) for x in (0, 1) for y in (0, 1)]
    labels = {}
    for i, cid in enumerate(child_ids):
        labels[cid] = _label(cid, "spaceA", StructuralLabel.FLOOR if i < 6 else StructuralLabel.WALL)

    parent_id = parent_local_spatial_id(child_ids[0], target, space_def)
    codes, tallies = build_color_codes_for_mode(
        "spaceA", space_def, target, finest, [parent_id], VisualizationMode.STRUCTURAL_LABEL,
        finest_labels=labels,
    )
    assert codes[0] == CATEGORY_TO_CODE["FLOOR"]
    assert tallies[0].label_counts == {"FLOOR": 6, "WALL": 2}
    assert tallies[0].total_labeled_child_count == 8
    print("test_parent_level_aggregation_uses_majority: OK")


def test_parent_aggregation_uses_parent_local_spatial_id():
    """親IDの決定にlocal_spatial_id_hierarchy.parent_local_spatial_id()が
    実際に使われていることを、その関数の出力と突き合わせて確認する。"""
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 2  # factor=4

    child_id = f"{finest}/5/9/13"
    expected_parent = parent_local_spatial_id(child_id, target, space_def)
    labels = {child_id: _label(child_id, "spaceA", StructuralLabel.CEILING)}

    codes, tallies = build_color_codes_for_mode(
        "spaceA", space_def, target, finest, [expected_parent], VisualizationMode.STRUCTURAL_LABEL,
        finest_labels=labels,
    )
    assert tallies[0].local_spatial_id == expected_parent
    assert codes[0] == CATEGORY_TO_CODE["CEILING"]
    print("test_parent_aggregation_uses_parent_local_spatial_id: OK")


def test_ambiguous_unresolved_not_overwritten_at_parent_level():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    target = finest - 1
    child_ids = [f"{finest}/{f}/{x}/{y}" for f in (0, 1) for x in (0, 1) for y in (0, 1)]
    # 4個UNRESOLVED・4個FLOORの同数 -> "FLOOR" < "UNRESOLVED" の辞書順ではなく、
    # ここではUNRESOLVEDを多数決で優勢にするため個数を偏らせる
    labels = {}
    for i, cid in enumerate(child_ids):
        labels[cid] = _label(cid, "spaceA", StructuralLabel.UNRESOLVED if i < 5 else StructuralLabel.FLOOR)

    parent_id = parent_local_spatial_id(child_ids[0], target, space_def)
    codes, tallies = build_color_codes_for_mode(
        "spaceA", space_def, target, finest, [parent_id], VisualizationMode.STRUCTURAL_LABEL,
        finest_labels=labels,
    )
    assert codes[0] == CATEGORY_TO_CODE["UNRESOLVED"], "UNRESOLVEDが優勢なのに別カテゴリに上書きされた"
    assert tallies[0].label_counts.get("UNRESOLVED") == 5
    assert tallies[0].label_counts.get("FLOOR") == 3
    print("test_ambiguous_unresolved_not_overwritten_at_parent_level: OK")


def test_instance_order_matches_color_buffer_order():
    space_def = _make_space()
    finest = finest_zoom_level(space_def)
    ids = ["11/0/0/0", "11/0/1/0", "11/1/0/0"]
    labels = {
        "11/0/0/0": _label("11/0/0/0", "spaceA", StructuralLabel.FLOOR),
        "11/1/0/0": _label("11/1/0/0", "spaceA", StructuralLabel.CEILING),
    }
    codes, tallies = build_color_codes_for_mode(
        "spaceA", space_def, finest, finest, ids, VisualizationMode.STRUCTURAL_LABEL, finest_labels=labels,
    )
    # codes[i] は ids[i] に対応していなければならない
    assert codes[0] == CATEGORY_TO_CODE["FLOOR"]      # ids[0] = "11/0/0/0"
    assert codes[1] == CATEGORY_TO_CODE["NO_LABEL"]   # ids[1] = "11/0/1/0"(ラベル無し)
    assert codes[2] == CATEGORY_TO_CODE["CEILING"]    # ids[2] = "11/1/0/0"
    assert [t.local_spatial_id for t in tallies] == ids, "tallyの順序もinstance順序と一致しなければならない"
    print("test_instance_order_matches_color_buffer_order: OK")


def test_unsupported_mode_raises():
    try:
        build_color_codes_for_mode("spaceA", _make_space(), 9, 9, ["9/0/0/0"], "NOT_A_MODE")
        raise AssertionError("未対応のmodeが受理されてしまった")
    except ValueError:
        pass
    print("test_unsupported_mode_raises: OK")


if __name__ == "__main__":
    test_default_mode_all_same_code()
    test_finest_label_joins_by_space_id_and_local_spatial_id()
    test_same_id_string_different_space_id_not_mixed()
    test_parent_level_aggregation_uses_majority()
    test_parent_aggregation_uses_parent_local_spatial_id()
    test_ambiguous_unresolved_not_overwritten_at_parent_level()
    test_instance_order_matches_color_buffer_order()
    test_unsupported_mode_raises()
    print()
    print("全テスト成功。")
