"""
backend/visualization_label_aggregation_policy.py

finestのStructural Label(SpatialVoxelLabel.resolved_label)群を、上位
zoom levelの表示voxel1個の色カテゴリへ集約する際の判断基準
(ロードマップStep 4)。

【既存policyとの違い(ユーザー指示: 2026-08-31)】
backend/structural_label_resolution_policy.py(StructuralLabelResolution
PolicyConfig)は、Planeの支持点(fitness)からvoxel labelそのものを決定
する既存のdomain policyであり、Structural Labelのsource dataの生成に
対応する。本モジュールはそれとは別の、Visualization専用の後段policyで
あり、「既に確定しているfinest labelの集合を、上位levelでどう1つの表示
カテゴリにまとめるか」だけを扱う。混同しないこと。

初期実装は単純多数決(最多カウントのlabelを採用、同数なら文字列の昇順で
決定的に1つ選ぶ)。resolve_visualization_category()のシグネチャさえ
変えなければ、将来別のstrategy(閾値ベース・重み付け等)に差し替えられる。

unresolved/ambiguousも他のlabelと全く同様にカウント対象であり、特別に
除外・上書きしない(evidenceとしてlabel_countsにそのまま残るため、
「本来競合していたのに単純多数決で綺麗な結果に見せかける」ことはない。
label_countsを保持したまま呼び出し側が別のstrategyへ切り替えられる)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VisualizationAggregationPolicyConfig:
    """将来の拡張用(閾値・重み付け等)。初期実装(単純多数決)では未使用だが、
    シグネチャを固定しておくことで将来の差し替えを容易にする。"""

    version: str = "v1"


def resolve_visualization_category(
    label_counts: dict, policy: VisualizationAggregationPolicyConfig | None = None
) -> str:
    """{label文字列: count} から、最終的な表示カテゴリ(1つの文字列)を決める。

    - label_countsが空(この表示voxelの下にラベル付き子が1つも無い)なら
      "NO_LABEL"を返す。
    - それ以外は最多カウントのlabelを採用する(単純多数決)。同数の場合は
      決定的にするためlabel名の昇順で最初のものを選ぶ。

    【注意】同数tie時の「label名の昇順」は、表示を決定的にするための
    Visualization専用の暫定的な取り決めに過ぎず、domain上の優先順位
    (例: WALLがFLOORより優先される、等の意味論)を表すものではない。
    将来、tie-breakに意味のある優先順位を持たせたくなった場合は、
    この関数(または新しいpolicy)を差し替えること。
    """
    if not label_counts:
        return "NO_LABEL"
    max_count = max(label_counts.values())
    candidates = sorted(label for label, count in label_counts.items() if count == max_count)
    return candidates[0]
