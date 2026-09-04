"""
backend/spatial_voxel_aggregation.py

finest(3cm)Local Spatial IDをsource of truthとしたまま、表示時だけ上位
zoom levelへ集約する(ロードマップStep 3: Spatial ID display level switching)。

【設計方針(ユーザー指示: 2026-08-29、2026-08-30に整理)】
- upper level Spatial ID・voxel stateは一切保存しない。常にfinestの
  SpatialVoxel(のキャッシュ)からその都度derive する(このモジュールの
  結果自体はderived cacheとして保存されうるが、それをsource of truthに
  しない)。
- 親Local Spatial IDへの対応付け自体(ID階層関係だけによる決定)は、
  backend/local_spatial_id_hierarchy.py の parent_local_spatial_id() に
  切り出した(2026-08-30)。このモジュールは、finest voxelの物理座標
  (voxel_center)からfinest IDを求める箇所(world_points_to_spatial_ids、
  入力がワールド座標であるため不可避な変換)と、親IDが決まった後の集約
  (カウント・voxel_center算出)のオーケストレーションだけを担当する。
  「voxel_center → world_points_to_spatial_ids」という再変換は、あくまで
  「まだIDを持たない生のfinest座標配列からIDを得る」ために必要な処理で
  あり、parent ID自体の決定原理ではない(parent ID決定はID階層関係のみで
  行う、local_spatial_id_hierarchy.parent_local_spatial_id参照)。
- voxel_centerは、親Local Spatial IDに対し
  spatial_id.local_spatial_id.resolve_provisional_world_center()をそのまま
  呼んで求める、理論上のgrid cell中心(finest子voxelのcentroid平均等では
  決めない)。この関数はロードマップPhase 3.1で、point_cloud_voxelization.py
  の旧`_voxel_center_from_local_spatial_id`と統合された唯一の実装
  (数式・挙動は無変更)。Viewer用途(座標系2、Base Map点群と同じ座標系)であり、
  Nodal correspondence専用のresolve_local_center()(座標系1)とは別物
  (2026-09-02、spatial_id.local_spatial_idのモジュールdocstring参照)。
- 集約の入力(finest_positions)は、Base Map点群を再度読み込んで
  再voxelizeするのではなく、既存のfinest voxelキャッシュ(voxel_center
  の配列)をそのまま使う想定(backend/server.py参照)。
"""
from __future__ import annotations

import numpy as np

from domain.spatial_voxel import AggregatedSpatialVoxel
from local_spatial_id_hierarchy import parent_local_spatial_id
from point_to_spatial_id import world_points_to_spatial_ids
from spatial_id.local_spatial_id import (
    resolve_provisional_world_center as _voxel_center_from_local_spatial_id,
)


def aggregate_finest_positions_to_zoom_level(
    space_id: str,
    space_def: dict,
    finest_positions: np.ndarray,
    finest_zoom_level: int,
    target_zoom_level: int,
) -> list:
    """finestのvoxel_center群(ワールド座標、(N,3))を、target_zoom_levelの
    Local Spatial IDへ集約し、list[AggregatedSpatialVoxel]を返す。

    target_zoom_level == finest_zoom_levelの場合、集約は実質的に恒等
    (1 finest voxel = 1 表示voxel、source_voxel_count=1)になる。

    親IDの決定自体はparent_local_spatial_id()(ID階層関係のみ、座標変換を
    経由しない)に委譲する。ここでのworld_points_to_spatial_ids呼び出しは、
    「まだIDを持たない生のfinest座標配列からfinest IDを得る」ためだけの
    ものであり、親ID決定の本質ではない。
    """
    if "unit-size" not in space_def or "origin" not in space_def or "rad" not in space_def:
        raise ValueError(f"space_defが不正です(origin/rad/unit-sizeが必要): {sorted(space_def.keys())}")
    # fail-fast: 明らかに無効なtarget_zoom_levelなら、ワールド座標の
    # ベクトル化変換(finest_positionsが大量にありうる)を行う前に弾く。
    # 実際の親ID変換時の検証はparent_local_spatial_id()自身が行う
    # (直接呼び出す利用者にも同じ検証がかかる、二重チェックではあるが
    # ここでは「無駄な計算をしない」ためのガードとして残す)。
    if target_zoom_level > finest_zoom_level:
        raise ValueError(
            f"target_zoom_level({target_zoom_level})はfinest_zoom_level"
            f"({finest_zoom_level})を超えられません(それより細かいlevelは存在しません)。"
        )
    if str(target_zoom_level) not in space_def["unit-size"]:
        raise ValueError(
            f"zoom_level {target_zoom_level} はこのLocal Spaceのunit-sizeに存在しません"
            f"(有効なzoom_level: {sorted((int(k) for k in space_def['unit-size']))})。"
        )
    finest_positions = np.asarray(finest_positions, dtype=np.float64)
    if finest_positions.ndim != 2 or finest_positions.shape[0] == 0:
        raise ValueError("finest_positions が空です(集約にはfinest voxelのcacheが必要です)。")

    finest_ids = world_points_to_spatial_ids(finest_positions, space_def, finest_zoom_level)

    # 親local_spatial_id -> このLocal Spaceでこの親に属したfinest voxelの個数
    counts: dict = {}
    for sid in finest_ids:
        parent_id = parent_local_spatial_id(sid, target_zoom_level, space_def)
        counts[parent_id] = counts.get(parent_id, 0) + 1

    voxel_size = space_def["unit-size"][str(target_zoom_level)]
    result = []
    for parent_id, count in counts.items():
        center = _voxel_center_from_local_spatial_id(parent_id, space_def)
        result.append(AggregatedSpatialVoxel(
            space_id=space_id,
            local_spatial_id=parent_id,
            zoom_level=target_zoom_level,
            voxel_size=voxel_size,
            voxel_center=center,
            source_voxel_count=count,
        ))
    return result
