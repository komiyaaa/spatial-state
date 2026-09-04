"""
backend/services/global_coordinate_service.py

precise_registered.ply等のworld/provisional座標の点群を、Spatial Resolutionで
RESOLVED済みのGlobal座標へ変換するための共通utility(2026-09-03)。

【確認済みのchain(調査のみのタスクで確認済み、数式は無変更のまま再利用する)】
    world/provisional (precise_registered.ply等の生座標)
    → intrinsic (そのspace_id自身のCoordinateDefinition: origin/rad)
    → resolved frame (member_transforms_to_global[space_id]: yaw+translation)

【新しい座標解決ロジックは作らない】
- world→intrinsic変換は `point_to_spatial_id.world_points_to_intrinsic()` を
  そのまま呼ぶだけ(CoordinateDefinitionの回転規約を複製しない。
  world_points_to_spatial_ids()のfloor手前の式をこの関数と共有している)。
- intrinsic→resolved frameの変換は `domain.transform.RigidTransform2D` の
  規約(yaw + 3次元並進のみ、scale/reflection無し)をそのまま使う。
- `member_transforms_to_global`は、Nodal Information(source of truth)から
  `services/global_resolution_service.py`が計算するderived resultであり、
  ここでは呼び出し側が渡した`ComponentGlobalResolution`をそのまま使うだけで、
  独自の再解決・再計算は行わない。

Nodal Information・Spatial Resolution・Registration(VGICP)の既存ロジック、
CoordinateDefinitionの生成・永続化には一切触れていない。
precise_registered.ply自体も書き換えない(呼び出し側が渡した点群を変換した
結果をメモリ上で返すだけ)。export API・GUIはこの変更には含まない。
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

from domain.global_resolution import ComponentGlobalResolution, GlobalResolutionStatus
from domain.transform import RigidTransform2D
from point_to_spatial_id import world_points_to_intrinsic

Point3 = Tuple[float, float, float]


class GlobalCoordinateResolutionError(RuntimeError):
    """このspace_idについて、Global座標への変換に必要なresolved transformが
    まだ存在しない(fail-closed。scale/reflection等で無理に補完したり、
    未解決のまま近似値を返したりはしない)。"""


def _apply_rigid_transform_2d_batch(transform: RigidTransform2D, points: np.ndarray) -> np.ndarray:
    """RigidTransform2D.apply()と数式・結果が完全に一致するnumpyベクトル化版。

    点群を1点ずつPythonループで`apply()`するのは大規模点群(precise_registered.ply
    は数十万点規模)で低速なため、同じ式をnumpyでベクトル化して適用する
    (新しい回転式ではない、既存apply()実装の単純な並列化)。
    `tests/test_global_coordinate_service.py`で、1点ずつ`RigidTransform2D.apply()`
    を呼んだ結果と完全に一致することを検証している。
    """
    cos_t = np.cos(transform.yaw_rad)
    sin_t = np.sin(transform.yaw_rad)
    tx, ty, tz = transform.translation
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    out_x = cos_t * x - sin_t * y + tx
    out_y = sin_t * x + cos_t * y + ty
    out_z = z + tz
    return np.stack([out_x, out_y, out_z], axis=1)


def world_points_to_resolved_global(
    points: np.ndarray,
    space_id: str,
    space_def: dict,
    global_resolution: ComponentGlobalResolution,
) -> np.ndarray:
    """
    ワールド座標(N, 3。precise_registered.ply等、そのLocal Space自身の
    生の点群座標)を、以下の順で resolved Global座標(target_epsgのメートル
    座標)へ変換する:

        1. world → intrinsic (space_defのorigin/rad、point_to_spatial_id.py
           の既存変換規約をそのまま再利用)
        2. intrinsic → global (global_resolution.member_transforms_to_global[space_id]、
           RigidTransform2D.applyの既存規約をそのまま再利用)

    fail-closed: `global_resolution.status`がRESOLVEDでない場合、または
    このspace_idが`member_transforms_to_global`に存在しない場合(=そのcomponentの
    memberではない、またはGlobal解決がそもそも成立していない)は、黙って
    フォールバックせず`GlobalCoordinateResolutionError`を送出する。

    :param points: (N, 3) のワールド座標配列
    :param space_id: 変換対象のLocal Spaceのspace_id
    :param space_def: そのspace_idのCoordinateDefinition(dict、"origin"・"rad"を含む)
    :param global_resolution: 対象space_idが属するcomponentの
        `ComponentGlobalResolution`(`services/global_resolution_service.py`が
        算出したderived result。ここでは再計算しない)
    :returns: (N, 3) のresolved Global座標配列
    """
    if global_resolution.status != GlobalResolutionStatus.RESOLVED:
        raise GlobalCoordinateResolutionError(
            f"space_id '{space_id}' が属するcomponentはGlobal未解決です"
            f"(status={global_resolution.status.value})。Global座標へは変換できません。"
        )
    transform = global_resolution.member_transforms_to_global.get(space_id)
    if transform is None:
        raise GlobalCoordinateResolutionError(
            f"space_id '{space_id}' はmember_transforms_to_globalに含まれていません"
            f"(このcomponentのmemberではない可能性があります)。"
        )

    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError(f"pointsは(N, 3)の配列である必要があります: shape={points.shape}")

    intrinsic = world_points_to_intrinsic(points, space_def)
    return _apply_rigid_transform_2d_batch(transform, intrinsic)


def world_point_to_resolved_global(
    point: Point3,
    space_id: str,
    space_def: dict,
    global_resolution: ComponentGlobalResolution,
) -> Point3:
    """1点版(world_points_to_resolved_global()のラッパー)。"""
    result = world_points_to_resolved_global(
        np.asarray([point], dtype=np.float64), space_id, space_def, global_resolution,
    )
    return tuple(float(v) for v in result[0])
