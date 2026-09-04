"""
backend/domain/structural_label.py

構造平面(Plane)ラベリング機能のドメインモデル。ユーザー指示(2026-08-29)に
よる3層構造:

1. Plane: Base Map点群からRANSACで抽出した平面そのもの(点群上の意味)。
   Base Mapから再生成可能なderived dataであり、CoordinateDefinitionには
   埋め込まない。
2. SpatialVoxelLabel: そのLocal Space自身の最小(3cm)Local Spatial IDに
   対応付けられた構造ラベル(voxel上の意味)。同一voxelに複数Planeの
   ラベルが競合しうるため、単純な文字列1個ではなく、候補一覧
   (label_candidates)と解決結果(resolved_label)を分けて保持する。
   競合解決アルゴリズム自体はここに置かず、
   backend/structural_label_resolution_policy.py
   が担当する(このモジュールはデータ構造のみ)。
3. LabelFitnessHistoryEntry: 競合判定・fitnessの履歴(監査・後日のチューニング
   用)。SPATIAL_VOXEL_LABEL(現在の解決結果)とは別レイヤーであり、
   混在させない。

【SPATIAL_VOXEL_STATEとの違い】
STATE(spatial_state パッケージ)は「voxelが占有されているか」という
observationベースの状態。LABEL(このモジュール)は「voxelがWALL/FLOOR/
CEILING等、何として解釈されるか」という構造的な意味づけ。互いに独立した
レイヤーであり、このモジュールはspatial_stateパッケージを一切importしない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StructuralLabel(str, Enum):
    FLOOR = "FLOOR"
    CEILING = "CEILING"
    WALL = "WALL"
    IGNORE = "IGNORE"
    UNASSIGNED = "UNASSIGNED"
    # voxel解決結果専用(Planeのconfirmed_labelとしては通常使わない)
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


# Plane.confirmed_label / suggested_label として許容するラベル
# (AMBIGUOUS/UNRESOLVEDはvoxel解決結果専用のため、Planeには使わない)
PLANE_LABELS = (
    StructuralLabel.FLOOR,
    StructuralLabel.CEILING,
    StructuralLabel.WALL,
    StructuralLabel.IGNORE,
    StructuralLabel.UNASSIGNED,
)

# voxel structural labelを生成する対象となる、構造的に意味のあるPlaneラベル
CONTRIBUTING_PLANE_LABELS = frozenset({StructuralLabel.FLOOR, StructuralLabel.CEILING, StructuralLabel.WALL})


@dataclass
class Plane(object):
    """RANSACで抽出した1枚の平面(Base Map点群上の意味。座標はワールド座標系)。"""

    plane_id: str
    space_id: str
    coefficients: list  # [a, b, c, d] (ax+by+cz+d=0)
    normal: list  # [nx, ny, nz] (正規化済み)
    centroid: list  # [x, y, z] (ワールド座標)
    point_count: int
    point_indices: list  # 元の点群配列中のインデックス(int のリスト)
    suggested_label: StructuralLabel = StructuralLabel.UNASSIGNED
    confirmed_label: StructuralLabel = StructuralLabel.UNASSIGNED
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self):
        if not self.plane_id or not self.space_id:
            raise ValueError("Planeにはplane_id・space_idが必須です。")
        if len(self.coefficients) != 4:
            raise ValueError(f"coefficientsは[a,b,c,d]の4要素である必要があります: {self.coefficients!r}")
        if len(self.normal) != 3:
            raise ValueError(f"normalは3要素である必要があります: {self.normal!r}")
        if len(self.centroid) != 3:
            raise ValueError(f"centroidは3要素である必要があります: {self.centroid!r}")
        if self.point_count != len(self.point_indices):
            raise ValueError(
                f"point_count({self.point_count})とpoint_indicesの長さ"
                f"({len(self.point_indices)})が一致しません。"
            )
        if self.suggested_label not in PLANE_LABELS:
            raise ValueError(f"suggested_labelに不正な値です: {self.suggested_label!r}")
        if self.confirmed_label not in PLANE_LABELS:
            raise ValueError(f"confirmed_labelに不正な値です: {self.confirmed_label!r}")

    @staticmethod
    def from_dict(data: dict) -> "Plane":
        return Plane(
            plane_id=data["plane_id"],
            space_id=data["space_id"],
            coefficients=list(data["coefficients"]),
            normal=list(data["normal"]),
            centroid=list(data["centroid"]),
            point_count=data["point_count"],
            point_indices=list(data["point_indices"]),
            suggested_label=StructuralLabel(data.get("suggested_label", "UNASSIGNED")),
            confirmed_label=StructuralLabel(data.get("confirmed_label", data.get("suggested_label", "UNASSIGNED"))),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict:
        return {
            "plane_id": self.plane_id,
            "space_id": self.space_id,
            "coefficients": self.coefficients,
            "normal": self.normal,
            "centroid": self.centroid,
            "point_count": self.point_count,
            "point_indices": self.point_indices,
            "suggested_label": self.suggested_label.value,
            "confirmed_label": self.confirmed_label.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class LabelCandidate:
    """1つのvoxelに対する、1つのラベルの支持状況。"""

    label: StructuralLabel
    fitness: float  # 初期実装では「支持点数の割合」等、説明可能な単純な値
    source_plane_ids: list = field(default_factory=list)

    def __post_init__(self):
        if self.fitness < 0:
            raise ValueError(f"fitnessは0以上である必要があります: {self.fitness}")

    @staticmethod
    def from_dict(data: dict) -> "LabelCandidate":
        return LabelCandidate(
            label=StructuralLabel(data["label"]),
            fitness=data["fitness"],
            source_plane_ids=list(data.get("source_plane_ids", [])),
        )

    def to_dict(self) -> dict:
        return {"label": self.label.value, "fitness": self.fitness, "source_plane_ids": self.source_plane_ids}


@dataclass
class SpatialVoxelLabel:
    """(space_id, local_spatial_id)ごとの構造ラベル解決結果。

    【重要】local_spatial_idは、そのspace_id自身のCoordinateDefinitionにおける
    最小(3cm)zoom levelのIDのみを記録する(上位zoom levelのラベルは保存しない。
    将来、finest-level labels + STRUCTURAL_LABEL_POLICYから集約・伝播して
    導出するものとする)。同じ"z/f/x/y"文字列でも、異なるspace_idでは無関係の
    voxelであるため、識別は必ず(space_id, local_spatial_id)の組で行うこと。
    """

    space_id: str
    local_spatial_id: str
    label_candidates: list  # list[LabelCandidate]
    resolved_label: StructuralLabel = StructuralLabel.UNRESOLVED
    source_plane_ids: list = field(default_factory=list)
    confirmed: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self):
        if not self.space_id or not self.local_spatial_id:
            raise ValueError("SpatialVoxelLabelにはspace_id・local_spatial_idが必須です。")

    @staticmethod
    def from_dict(data: dict) -> "SpatialVoxelLabel":
        return SpatialVoxelLabel(
            space_id=data["space_id"],
            local_spatial_id=data["local_spatial_id"],
            label_candidates=[LabelCandidate.from_dict(c) for c in data.get("label_candidates", [])],
            resolved_label=StructuralLabel(data.get("resolved_label", "UNRESOLVED")),
            source_plane_ids=list(data.get("source_plane_ids", [])),
            confirmed=data.get("confirmed", False),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict:
        return {
            "space_id": self.space_id,
            "local_spatial_id": self.local_spatial_id,
            "label_candidates": [c.to_dict() for c in self.label_candidates],
            "resolved_label": self.resolved_label.value,
            "source_plane_ids": self.source_plane_ids,
            "confirmed": self.confirmed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class LabelFitnessHistoryEntry:
    """競合判定・fitness計算の履歴(監査・将来のポリシー較正用)。

    既存構想のLABEL_FITNESS_HISTORY(docs/spatial_id_design_memo_v2.md)は
    構造ラベルの事前分布(prior_alpha0等)向けのfitness_score蓄積だったが、
    本エントリはPhase違いの新しい用途(voxel構造ラベルの競合解決履歴)。
    名称を共有しつつ将来接続できるよう、必要なキー(space_id・
    local_spatial_id・resolved_label等)を揃えてある。SPATIAL_VOXEL_LABEL
    (現在値)とは別ファイル・別レイヤーで保持し、混在させないこと。
    """

    history_id: str
    space_id: str
    local_spatial_id: str
    timestamp: str
    candidate_labels: list  # list[LabelCandidate]
    resolved_label: StructuralLabel
    source_plane_ids: list = field(default_factory=list)
    policy_version: Optional[str] = None

    @staticmethod
    def from_dict(data: dict) -> "LabelFitnessHistoryEntry":
        return LabelFitnessHistoryEntry(
            history_id=data["history_id"],
            space_id=data["space_id"],
            local_spatial_id=data["local_spatial_id"],
            timestamp=data["timestamp"],
            candidate_labels=[LabelCandidate.from_dict(c) for c in data.get("candidate_labels", [])],
            resolved_label=StructuralLabel(data["resolved_label"]),
            source_plane_ids=list(data.get("source_plane_ids", [])),
            policy_version=data.get("policy_version"),
        )

    def to_dict(self) -> dict:
        return {
            "history_id": self.history_id,
            "space_id": self.space_id,
            "local_spatial_id": self.local_spatial_id,
            "timestamp": self.timestamp,
            "candidate_labels": [c.to_dict() for c in self.candidate_labels],
            "resolved_label": self.resolved_label.value,
            "source_plane_ids": self.source_plane_ids,
            "policy_version": self.policy_version,
        }
