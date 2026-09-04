"""
backend/domain/visualization.py

Local Space ViewerのSpatial ID voxel色分け(ロードマップStep 4:
Visualization / Coloring Strategy)に関わるデータ構造。

VisualizationMode: Viewerがvoxelをどんな意味に基づいて色分けするかを表す
拡張可能なenum。今回はDEFAULT・STRUCTURAL_LABELの2つのみ実装するが、
将来SPATIAL_STATE・OCCUPANCY_PROBABILITY・OBSERVATION_COUNT・
UPDATE_CONFIDENCE・CHANGE_STATUS・LABEL_FITNESS等を同じ構造(Viewerは
VisualizationModeを選ぶだけ、色の意味づけはbackendのColorStrategy層が
担う)で追加できることを意図している。

LabelVisualizationTally: 上位zoom levelでStructural Labelを集約する際の、
「この表示voxelの下にどんなラベルの子がいくつあったか」という集計結果
(source of truthではなく、finestのSpatialVoxelLabelからその都度導出
されるderived data)。

【重要】これはbackend/structural_label_resolution_policy.py
(StructuralLabelResolutionPolicyConfig、Planeの支持点からvoxel labelその
ものを決定する既存のdomain policy)とは別の関心事である。
LabelVisualizationTallyは「既に確定しているfinest labelを、Visualization
用にどう1つの表示カテゴリへ集約するか」だけを扱う、表示専用の中間結果。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VisualizationMode(str, Enum):
    DEFAULT = "DEFAULT"
    STRUCTURAL_LABEL = "STRUCTURAL_LABEL"
    # 将来追加予定(今回は未実装):
    # SPATIAL_STATE, OCCUPANCY_PROBABILITY, OBSERVATION_COUNT,
    # UPDATE_CONFIDENCE, CHANGE_STATUS, LABEL_FITNESS


@dataclass
class LabelVisualizationTally:
    """1つの表示voxel(finestまたは上位levelの親)について、Structural
    Labelの構成を集計した結果。"""

    space_id: str
    local_spatial_id: str
    zoom_level: int
    label_counts: dict = field(default_factory=dict)  # {resolved_labelの文字列: count}
    total_labeled_child_count: int = 0  # label_countsの合計(ラベルを持つ子の総数)
    resolved_category: str = "NO_LABEL"  # 最終的な表示カテゴリ(StructuralLabelの値 or "NO_LABEL")
