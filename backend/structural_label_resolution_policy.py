"""
backend/structural_label_resolution_policy.py

複数Planeのラベルが同一voxelに競合したとき、どのラベルを採用するか
(またはAMBIGUOUS/UNRESOLVEDとして保留するか)を決める、Plane競合解決の
初期実装。

【名称について(ユーザー指示: 2026-08-29)】
このモジュールが扱う「StructuralLabelResolutionPolicy」は、Plane→voxel
ラベル変換における競合解決の責務であり、docs/spatial_id_design_memo_v2.md
のSTRUCTURAL_LABEL_POLICY(Spatial State側、prior_alpha0・prior_beta0等の
ベイズ更新の事前分布を扱うテーブル)とは別の概念である。混同を避けるため、
モジュール名・クラス名に明示的に"Resolution"を含めている。Spatial State側の
既存STRUCTURAL_LABEL_POLICY構想は本実装では一切変更していない。

【設計方針】
- 競合解決ロジックをvoxelラベル生成コード(plane_to_voxel_labels.py)に
  直接埋め込まない。判断基準はすべてこのモジュールに集約する。
- 初期実装のfitnessは「支持点数の割合」という単純な値を前提とするが、
  将来fitnessの計算方法が変わっても、resolve_label()のシグネチャ
  (LabelCandidateのリストを受け取り、StructuralLabelを返す)は変えずに
  済む設計にしてある。
- 十分な確信が持てない場合はAMBIGUOUS、候補が無い場合はUNRESOLVEDを返す
  (自動的に「多い方」を機械的に採用して確定させない)。

将来、Plane競合解決とSpatial State側の事前分布を同じ設定エンティティに
統合するかどうかは未確定であり、本実装では統合していない。
"""
from __future__ import annotations

from dataclasses import dataclass

from domain.structural_label import StructuralLabel


@dataclass
class StructuralLabelResolutionPolicyConfig:
    """Plane競合解決の閾値。処理コード中に埋め込まず、ここに集約する。"""

    min_fitness_to_resolve: float = 0.6  # 最有力候補のfitnessがこれ未満ならUNRESOLVED
    min_dominance_ratio: float = 1.5  # 最有力候補が2位よりこの倍率以上強くないとAMBIGUOUS
    version: str = "v1"  # LabelFitnessHistoryEntry.policy_version に記録する識別子


def resolve_label(
    label_candidates: list, policy: StructuralLabelResolutionPolicyConfig | None = None
) -> StructuralLabel:
    """LabelCandidateのリストから、採用するStructuralLabelを1つ決める。

    - 候補が無ければUNRESOLVED
    - 最有力候補のfitnessが閾値未満ならUNRESOLVED(証拠不足)
    - 最有力候補が2位候補に対して十分優勢でなければAMBIGUOUS(競合未解決)
    - それ以外は最有力候補のlabelを採用する
    """
    policy = policy or StructuralLabelResolutionPolicyConfig()

    if not label_candidates:
        return StructuralLabel.UNRESOLVED

    ranked = sorted(label_candidates, key=lambda c: c.fitness, reverse=True)
    top = ranked[0]

    if top.fitness < policy.min_fitness_to_resolve:
        return StructuralLabel.UNRESOLVED

    if len(ranked) > 1:
        second = ranked[1]
        if second.fitness > 0 and (top.fitness / second.fitness) < policy.min_dominance_ratio:
            return StructuralLabel.AMBIGUOUS

    return top.label
