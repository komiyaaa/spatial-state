"""
backend/voxel_color_strategy.py

Local Space ViewerのSpatial ID voxel表示に、意味(Structural Label等)に
応じた色を付けるための、Viewerから独立したColorStrategy層
(ロードマップStep 4: Visualization / Coloring Strategy)。

【設計方針(ユーザー指示: 2026-08-31)】
- Viewer(JS)自身がStructural Label等のdomain意味をif/elifで判定する
  構造にしない。色の決定はすべてbackend側のこのモジュールが担い、
  Viewerには「どのカテゴリ番号か(color_code、uint8)」+「カテゴリ番号→
  RGBの小さな対応表(legend、visualization_colors.build_legend)」だけを
  渡す。Viewerはlegend[code]を引くだけのデータ駆動な描画に徹する
  (instanceColorの計算のみ)。
- VisualizationMode(domain/visualization.py。DEFAULT/STRUCTURAL_LABEL、
  将来SPATIAL_STATE等を追加できる)ごとに、対象voxel一覧(positions.binと
  同じinstance順序)に対するcolor_codeを計算する。
- Structural Labelは(space_id, local_spatial_id)をkeyにjoinする
  (local_spatial_id文字列だけをspace横断のglobal unique keyとして
  扱わない。呼び出し側は必ず1 space_id分のfinest_labels辞書だけを渡す)。
  finestではSpatialVoxelLabelを直接参照し、finestより粗いlevelでは
  local_spatial_id_hierarchy.parent_local_spatial_id()でfinest labelを
  target zoomへ集約する。upper level labelは新規永続化せず、常にこの
  関数でderiveし直す。
- 上位levelの集約結果決定はvisualization_label_aggregation_policy.pyに
  委譲し、structural_label_resolution_policy.py(Plane競合解決、既存の
  別のdomain policy)とは混同しない。
- 巨大JSONを避けるため、color_codeはuint8のNumPy配列として返す
  (呼び出し側でpositions.binと同様バイナリ化する想定)。legendは
  カテゴリ数個(9個)だけの小さな辞書で十分。
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from domain.structural_label import SpatialVoxelLabel
from domain.visualization import LabelVisualizationTally, VisualizationMode
from local_spatial_id_hierarchy import parent_local_spatial_id
from visualization_colors import CATEGORY_TO_CODE
from visualization_label_aggregation_policy import (
    VisualizationAggregationPolicyConfig,
    resolve_visualization_category,
)


def build_default_color_codes(voxel_count: int) -> np.ndarray:
    """DEFAULTモード: 全voxel同じ色(既存の単色voxel表示と同じ見た目)。"""
    return np.full(voxel_count, CATEGORY_TO_CODE["DEFAULT"], dtype=np.uint8)


def build_structural_label_color_codes(
    space_id: str,
    space_def: dict,
    zoom_level: int,
    finest_zoom_level: int,
    ordered_local_spatial_ids: list,
    finest_labels: Dict[str, SpatialVoxelLabel],
    policy: Optional[VisualizationAggregationPolicyConfig] = None,
):
    """STRUCTURAL_LABELモードのcolor_codeを計算する。

    - ordered_local_spatial_ids: 表示するvoxel(positions.binと同じ順序)の
      Local Spatial ID一覧。finestの場合はfinest ID、上位levelの場合は
      その親IDの一覧(spatial_voxel_aggregationが生成したものと同じ)。
    - finest_labels: このspace_id専用のSpatialVoxelLabelRepository.load_all()
      の結果(space_id, local_spatial_id)で既にjoin済みの辞書。呼び出し側が
      他のspace_idのラベルを混入させないこと。

    戻り値: (color_codes: np.ndarray[uint8], tallies: list[LabelVisualizationTally])
    """
    policy = policy or VisualizationAggregationPolicyConfig()
    codes = np.zeros(len(ordered_local_spatial_ids), dtype=np.uint8)
    tallies = []

    if zoom_level == finest_zoom_level:
        # finestでは(space_id, local_spatial_id)で直接join するだけ。
        # 上位levelのような集約は発生しない。
        for i, sid in enumerate(ordered_local_spatial_ids):
            label = finest_labels.get(sid)
            if label is None:
                category = "NO_LABEL"
                counts: dict = {}
                total = 0
            else:
                category = label.resolved_label.value
                counts = {category: 1}
                total = 1
            codes[i] = CATEGORY_TO_CODE[category]
            tallies.append(LabelVisualizationTally(
                space_id=space_id, local_spatial_id=sid, zoom_level=zoom_level,
                label_counts=counts, total_labeled_child_count=total, resolved_category=category,
            ))
        return codes, tallies

    # 上位level: 全finest labelを、parent_local_spatial_id()でtarget zoomの
    # 親IDへ集約する(ID階層関係のみ、座標変換を経由しない)。
    parent_tally: Dict[str, dict] = {}
    for finest_id, label in finest_labels.items():
        parent_id = parent_local_spatial_id(finest_id, zoom_level, space_def)
        bucket = parent_tally.setdefault(parent_id, {})
        label_value = label.resolved_label.value
        bucket[label_value] = bucket.get(label_value, 0) + 1

    for i, parent_id in enumerate(ordered_local_spatial_ids):
        counts = parent_tally.get(parent_id, {})
        total = sum(counts.values())
        category = resolve_visualization_category(counts, policy)
        codes[i] = CATEGORY_TO_CODE[category]
        tallies.append(LabelVisualizationTally(
            space_id=space_id, local_spatial_id=parent_id, zoom_level=zoom_level,
            label_counts=dict(counts), total_labeled_child_count=total, resolved_category=category,
        ))

    return codes, tallies


def build_color_codes_for_mode(
    space_id: str,
    space_def: dict,
    zoom_level: int,
    finest_zoom_level: int,
    ordered_local_spatial_ids: list,
    mode: VisualizationMode,
    finest_labels: Optional[Dict[str, SpatialVoxelLabel]] = None,
    policy: Optional[VisualizationAggregationPolicyConfig] = None,
):
    """VisualizationModeに応じたcolor_codeを計算する唯一の入口。

    Viewer(JS)側はmode名を渡すだけでよく、mode固有の判定ロジックは
    すべてこの関数(と、ここが呼ぶ各build_*関数)に閉じている。
    """
    if mode == VisualizationMode.DEFAULT:
        return build_default_color_codes(len(ordered_local_spatial_ids)), []
    if mode == VisualizationMode.STRUCTURAL_LABEL:
        if finest_labels is None:
            raise ValueError("STRUCTURAL_LABELモードにはfinest_labelsが必要です。")
        return build_structural_label_color_codes(
            space_id, space_def, zoom_level, finest_zoom_level,
            ordered_local_spatial_ids, finest_labels, policy,
        )
    raise ValueError(f"未対応のVisualizationModeです: {mode}")
