"""
backend/domain/component_placement.py

connected component内でのLocal Space同士の相対配置(component-local frame)
の結果を表す値オブジェクト(ロードマップPhase 3.3)。

【位置づけ】これはGlobal座標(緯度経度等)ではない。あるcomponent内で
任意に選んだ1つのLocal Space(root)を基準にした、相対的なRigidTransform2D
(root自身の座標系1: intrinsic local physical coordinate、
spatial_id.local_spatial_id参照)。Global anchorの有無とは無関係に、
NodalConnectionでLocal↔Local接続されている限り常に計算できる。

source of truthはNodal Information(NodalConnection.solution)であり、
ComponentPlacementResult自体は常に再計算可能なderived dataとして扱う。

【永続化について(ロードマップPhase 3.6)】
to_dict()は、repositories.spatial_resolution_result_repositoryが
derived cacheとして保存する際に使うシリアライズ補助。この値オブジェクト
自体をsource of truthにするわけではない(いつでもNodal Informationから
再計算し、上書きしてよい)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from domain.transform import RigidTransform2D


class ComponentStatus(str, Enum):
    RESOLVED = "RESOLVED"  # spanning treeで全memberに矛盾なく到達できた
    CONFLICT = "CONFLICT"  # 非tree辺(cycle)が許容誤差を超えて不整合だった


@dataclass
class ComponentConflict:
    """1件の非tree辺(cycle)で検出された不整合。"""

    connection_id: Optional[str]
    space_id: str  # 不整合が検出されたspace_id(この空間への2経路が食い違った)
    expected: RigidTransform2D  # spanning tree経由で先に確定していた値
    via_edge: RigidTransform2D  # この非tree辺経由で計算した値
    yaw_diff_rad: float
    translation_diff_m: float

    def to_dict(self) -> dict:
        return {
            "connection_id": self.connection_id,
            "space_id": self.space_id,
            "expected": self.expected.to_dict(),
            "via_edge": self.via_edge.to_dict(),
            "yaw_diff_rad": self.yaw_diff_rad,
            "translation_diff_m": self.translation_diff_m,
        }


@dataclass
class ComponentPlacementResult:
    """1つのconnected componentの相対配置結果。"""

    component_id: str
    member_space_ids: List[str]
    root_space_id: str
    transforms: Dict[str, RigidTransform2D]  # {space_id: T_space_to_componentFrame}。rootはidentity。
    status: ComponentStatus
    conflicts: List[ComponentConflict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "member_space_ids": list(self.member_space_ids),
            "root_space_id": self.root_space_id,
            "transforms": {space_id: t.to_dict() for space_id, t in self.transforms.items()},
            "status": self.status.value,
            "conflicts": [c.to_dict() for c in self.conflicts],
        }
