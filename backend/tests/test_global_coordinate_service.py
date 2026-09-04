"""
backend/tests/test_global_coordinate_service.py

services/global_coordinate_service.py の動作確認テスト。

確認内容:
- identityに近いケース(rotation=0、resolved transform=identity。ただし
  Local座標系規約自体がY軸反転(det=-1)を含むため、単純な恒等にはならない
  ことも明示する)
- 非ゼロCoordinateDefinition rotation
- 非ゼロresolved yaw/translation
- z方向の並進
- 複数点(batch)がPythonループ・1点版と一致すること
- 未RESOLVED状態でのfail-closed(GlobalCoordinateResolutionError)
- space_idがmember_transforms_to_globalに無い場合のfail-closed
- 手計算した(world→intrinsic→global)chainと一致すること

実行方法(backendディレクトリから):
    python -m pytest tests/test_global_coordinate_service.py -v
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from domain.global_resolution import ComponentGlobalResolution, GlobalResolutionStatus  # noqa: E402
from domain.transform import RigidTransform2D  # noqa: E402
from services.global_coordinate_service import (  # noqa: E402
    GlobalCoordinateResolutionError,
    world_point_to_resolved_global,
    world_points_to_resolved_global,
)


def _manual_chain(point, origin, rad, transform: RigidTransform2D):
    """world→intrinsic→globalを、サービスを一切使わず素朴な式で計算する
    (point_to_spatial_id.world_points_to_intrinsicと同じ回転式・
    RigidTransform2D.applyと同じ式を、テスト側で独立に書き下したもの)。"""
    rel = np.array(point, dtype=np.float64) - np.array(origin, dtype=np.float64)
    cos_t, sin_t = math.cos(rad), math.sin(rad)
    local_x = rel[0] * cos_t + rel[1] * sin_t
    local_y = rel[0] * sin_t - rel[1] * cos_t
    local_z = rel[2]
    gx, gy, gz = transform.apply((local_x, local_y, local_z))
    return np.array([gx, gy, gz])


def _resolved(status, transforms=None) -> ComponentGlobalResolution:
    return ComponentGlobalResolution(
        component_id="c1", status=status, member_transforms_to_global=transforms or {},
    )


def test_identity_like_case():
    """rotation=0・resolved transform=identityでも、Local座標系規約自体の
    Y軸反転(det=-1)により単純な恒等にはならないことを明示する。"""
    space_def = {"origin": [0.0, 0.0, 0.0], "rad": 0.0}
    gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": RigidTransform2D.identity()})
    point = (1.0, 2.0, 3.0)

    result = world_point_to_resolved_global(point, "s1", space_def, gr)
    expected = _manual_chain(point, space_def["origin"], space_def["rad"], RigidTransform2D.identity())
    assert np.allclose(result, expected)
    assert np.allclose(result, [1.0, -2.0, 3.0]), f"Y軸反転が反映されていない: {result}"
    print(f"test_identity_like_case: OK (result={result})")


def test_nonzero_coordinate_definition_rotation():
    space_def = {"origin": [1.0, -2.0, 0.5], "rad": math.radians(37.0)}
    gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": RigidTransform2D.identity()})
    point = (4.4, 5.5, 2.2)

    result = world_point_to_resolved_global(point, "s1", space_def, gr)
    expected = _manual_chain(point, space_def["origin"], space_def["rad"], RigidTransform2D.identity())
    assert np.allclose(result, expected)
    print(f"test_nonzero_coordinate_definition_rotation: OK (result={result})")


def test_nonzero_resolved_yaw_translation():
    space_def = {"origin": [0.0, 0.0, 0.0], "rad": 0.0}
    transform = RigidTransform2D(yaw_rad=math.radians(50.0), translation=(10.0, -5.0, 0.0))
    gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": transform})
    point = (2.0, 3.0, 1.0)

    result = world_point_to_resolved_global(point, "s1", space_def, gr)
    expected = _manual_chain(point, space_def["origin"], space_def["rad"], transform)
    assert np.allclose(result, expected)
    print(f"test_nonzero_resolved_yaw_translation: OK (result={result})")


def test_z_translation():
    space_def = {"origin": [0.0, 0.0, 0.0], "rad": 0.0}
    transform = RigidTransform2D(yaw_rad=0.0, translation=(0.0, 0.0, 7.5))
    gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": transform})
    point = (1.0, 1.0, 1.0)

    result = world_point_to_resolved_global(point, "s1", space_def, gr)
    expected = _manual_chain(point, space_def["origin"], space_def["rad"], transform)
    assert np.allclose(result, expected)
    assert math.isclose(result[2], 1.0 + 7.5)
    print(f"test_z_translation: OK (result={result})")


def test_multiple_points_batch_matches_manual_and_single_point():
    space_def = {"origin": [3.0, 4.0, 0.0], "rad": math.radians(-22.0)}
    transform = RigidTransform2D(yaw_rad=math.radians(15.0), translation=(100.0, 200.0, -3.0))
    gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": transform})
    points = np.array([[0.0, 0.0, 0.0], [5.0, 5.0, 5.0], [-3.0, 7.0, 2.0], [10.0, -10.0, 1.0]])

    batch_result = world_points_to_resolved_global(points, "s1", space_def, gr)
    assert batch_result.shape == (4, 3)
    for i, p in enumerate(points):
        single = world_point_to_resolved_global(tuple(p), "s1", space_def, gr)
        assert np.allclose(batch_result[i], single), f"バッチ結果と1点版が不一致(index={i})"
        manual = _manual_chain(p, space_def["origin"], space_def["rad"], transform)
        assert np.allclose(batch_result[i], manual), f"手計算chainと不一致(index={i})"
    print(f"test_multiple_points_batch_matches_manual_and_single_point: OK ({len(points)}点)")


def test_unresolved_status_fails_closed():
    space_def = {"origin": [0.0, 0.0, 0.0], "rad": 0.0}
    for status in (
        GlobalResolutionStatus.NO_ANCHOR,
        GlobalResolutionStatus.BLOCKED_BY_LOCAL_CONFLICT,
        GlobalResolutionStatus.ANCHOR_UNRESOLVABLE,
        GlobalResolutionStatus.ANCHOR_INSUFFICIENT,
        GlobalResolutionStatus.GLOBAL_CONFLICT,
    ):
        gr = _resolved(status, {"s1": RigidTransform2D.identity()})
        with pytest.raises(GlobalCoordinateResolutionError):
            world_point_to_resolved_global((1.0, 2.0, 3.0), "s1", space_def, gr)
    print("test_unresolved_status_fails_closed: OK (5ステータス全てで送出を確認)")


def test_space_id_not_in_member_transforms_fails_closed():
    space_def = {"origin": [0.0, 0.0, 0.0], "rad": 0.0}
    gr = _resolved(GlobalResolutionStatus.RESOLVED, {"other_space": RigidTransform2D.identity()})
    with pytest.raises(GlobalCoordinateResolutionError):
        world_point_to_resolved_global((1.0, 2.0, 3.0), "s1", space_def, gr)
    print("test_space_id_not_in_member_transforms_fails_closed: OK")


if __name__ == "__main__":
    test_identity_like_case()
    test_nonzero_coordinate_definition_rotation()
    test_nonzero_resolved_yaw_translation()
    test_z_translation()
    test_multiple_points_batch_matches_manual_and_single_point()
    test_unresolved_status_fails_closed()
    test_space_id_not_in_member_transforms_fails_closed()
    print()
    print("全テスト成功。")
