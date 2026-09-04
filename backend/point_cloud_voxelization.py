"""
backend/point_cloud_voxelization.py

Base Map点群を、そのLocal Space自身のCoordinateDefinition(space_def)を使い、
finest(3cm)Local Spatial IDへ変換したうえでvoxel単位に集約する(ロードマップ
Step 1: 「Base Map point cloud → finest Local Spatial ID generation」+
「voxel aggregation」)。

【設計方針(ユーザー指示: 2026-08-29)】
- point → Local Spatial IDの変換は、point_to_spatial_id.py
  (world_points_to_spatial_ids)のみを使う(唯一のforward変換実装)。
  ここで新たに座標変換ロジックを実装しない。
- finest zoomはfixedな番号を使わず、そのspace_def自身の
  unit-size(space_definition_generator.finest_zoom_level)から決定する。
  他のLocal Spaceのunit-sizeは一切参照しない。
- 生成・保存するのはfinest levelのみ。上位zoomのSpatial IDはここでは
  生成しない(将来Step 3でderivedする)。
- Structural Label(backend/domain/structural_label.py、
  backend/plane_to_voxel_labels.py)の責務・保存形式には一切触れない。
  このモジュールが返すSpatialVoxelは、(space_id, local_spatial_id)という
  同じキーを持つだけの、独立した別データである。

【voxel_centerの逆変換について(2026-08-29導入、2026-09-01にPhase 3.1へ統合、
2026-09-02に座標系の名前を明確化)】
Viewerでcube instanceを配置するには、点分布に依存しない「Spatial ID grid
cellの幾何学的中心」が必要になった。これはLocal Spatial ID(f/x/y)から、
そのLocal Space自身のCoordinateDefinition(origin/rad/unit-size)だけを
使って求める、ID→座標の最小限の逆変換である。Base Map点群と同じ座標系
(spatial_id.local_spatial_idモジュールが言う「座標系2: provisional/world
coordinate」)を返す必要がある(このモジュールと同じシーンに点群を描画する
Viewer用途のため)。

この逆変換の実装は、ロードマップPhase 3.1で実装した
spatial_id.local_spatial_id.resolve_provisional_world_center()に統合済み
(数式・挙動は無変更、二重実装を避けるための移設)。
**注意(2026-09-02)**: 同モジュールにはorigin/rotationを使わない別の座標系
(座標系1: intrinsic local physical coordinate、resolve_local_center()。
Nodal correspondence専用)も存在するが、このモジュール(Viewer用途)が
使うのは必ずresolve_provisional_world_center()の方である。
`_voxel_center_from_local_spatial_id`は既存の呼び出し元(このモジュール自身、
spatial_voxel_aggregation.py、関連テスト)との後方互換のためのエイリアス名
として残している。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from domain.spatial_voxel import SpatialVoxel
from point_to_spatial_id import world_points_to_spatial_ids
from space_definition_generator import finest_zoom_level
from spatial_id.local_spatial_id import (
    resolve_provisional_world_center as _voxel_center_from_local_spatial_id,
)


def _validate_space_def(space_def: dict) -> None:
    """world_points_to_spatial_idsへ渡す前のfail-fastな事前チェック
    (大量のpoints配列を変換する前に、明らかに不正なspace_defを弾く)。
    voxel_center自体の逆変換ロジックはspatial_id.local_spatial_id.resolve_local_center
    に統合済み(そちらも同じキーの存在チェックを行うが、対象・タイミングが
    異なるため、ここでの事前チェックとは別物として残す)。"""
    for key in ("origin", "rad", "unit-size"):
        if key not in space_def:
            raise ValueError(f"space_defが不正です('{key}'が必要): {sorted(space_def.keys())}")
    if not space_def["unit-size"]:
        raise ValueError("space_def['unit-size']が空です(zoom levelを1つも決定できません)。")


def voxelize_base_map_points(
    space_id: str,
    space_def: dict,
    points: np.ndarray,
    colors: Optional[np.ndarray] = None,
) -> list:
    """Base Map点群(ワールド座標)を、このLocal Space自身のfinest Local
    Spatial IDへ変換し、voxelごとに集約したlist[SpatialVoxel]を返す。

    - space_id: このLocal Space自身のspace_id(結果のSpatialVoxel.space_idに
      使う。他のLocal Spaceのvoxelと混同しないための識別キー)。
    - space_def: このLocal Space自身のCoordinateDefinition(生JSON辞書。
      "origin"/"rad"/"unit-size"が必要)。他のLocal Spaceのものを渡しては
      ならない。
    - points: ワールド座標の点群(N, 3)。
    - colors: 任意。pointsと同じ順序・同じ点数の色配列(N, 3、0-1想定)。
      指定した場合、各voxelのmean_colorを計算する。

    各SpatialVoxelのvoxel_centerは点分布に依存しない幾何学的中心
    (Viewerのinstance位置に使う)、point_centroidは実際の支持点の重心
    (参考値)であり、両者は別の値になりうる(domain.spatial_voxel参照)。

    finest zoomは、このspace_def自身のunit-size系列から
    space_definition_generator.finest_zoom_level()で決定する
    (fixedなzoom番号は使わない)。
    """
    _validate_space_def(space_def)

    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[0] == 0:
        raise ValueError("points が空です(voxel集約には点群データが必要です)。")
    if colors is not None:
        colors = np.asarray(colors, dtype=np.float64)
        if colors.shape[0] != points.shape[0]:
            raise ValueError(
                f"colorsの点数がpointsと一致しません(points={points.shape[0]}, colors={colors.shape[0]})。"
            )

    zoom_level = finest_zoom_level(space_def)
    voxel_size = space_def["unit-size"][str(zoom_level)]
    spatial_ids = world_points_to_spatial_ids(points, space_def, zoom_level)

    # local_spatial_id -> このLocal Space内の点インデックス一覧
    groups: dict = {}
    for i, sid in enumerate(spatial_ids):
        groups.setdefault(sid, []).append(i)

    voxels = []
    for sid, indices in groups.items():
        idx = np.asarray(indices, dtype=np.int64)
        group_points = points[idx]
        point_centroid = group_points.mean(axis=0).tolist()
        voxel_center = _voxel_center_from_local_spatial_id(sid, space_def)
        mean_color = colors[idx].mean(axis=0).tolist() if colors is not None else None

        voxels.append(SpatialVoxel(
            space_id=space_id,
            local_spatial_id=sid,
            zoom_level=zoom_level,
            voxel_size=voxel_size,
            point_count=len(idx),
            voxel_center=voxel_center,
            point_centroid=point_centroid,
            mean_color=mean_color,
        ))

    return voxels
