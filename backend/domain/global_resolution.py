"""
backend/domain/global_resolution.py

Global anchor(Local↔GLOBALのNodalConnection)を持つconnected componentの
Global Resolution結果を表す値オブジェクト(ロードマップPhase 3.4)。

【最重要の前提(ユーザー指示: 2026-09-02、Phase 3.5でget_center/get_bounds実装済み)】
GlobalSpatialIdResolver.get_center()/get_bounds()はPhase 3.5で実装済み
(backend/spatial_id/global_spatial_id.py、ガイドライン§2.4.2の逆変換)。
極地空間ID等、今回もスコープ外のままの経路では、ダミー座標・仮座標を
一切導入せず、素直に「未解決」(GlobalResolutionStatus.ANCHOR_UNRESOLVABLE)
として扱う。

【target_epsgの保持(ユーザー指示: 2026-09-02、Phase 3.5b)】
Global Resolutionの過程で、GeographicPoint(度単位)は
spatial_id.geographic_projection.to_projected()によりProjectedPoint
(メートル単位、target_epsgのCRS)へ変換されてからfit_rigid_transform_2d()へ
渡される(degree座標を直接Kabschへ渡さない設計)。この`target_epsg`は
ProjectedPoint内部だけで消費して捨てず、`AnchorEstimate.target_epsg`・
`ComponentGlobalResolution.target_epsg`として、最終的なderived result
(RigidTransform2Dを解釈する側)まで残す。これにより、「この
transform_root_to_globalの並進(メートル)がどの投影CRSに基づくか」を
常に追跡できるようにする。

【connected component statusとglobal resolution statusの分離】
Local↔Localの相対配置(domain.component_placement.ComponentPlacementResult、
ロードマップPhase 3.3)と、Global座標への解決状況は、別の状態機械として
扱う。anchor(Local↔GLOBAL接続)が無いcomponentでも、Local↔Localの相対配置
(ComponentPlacementResult)自体は正常に成立しうる(status=RESOLVED)。その
場合、GlobalResolutionStatus側はNO_ANCHOR(Global座標は不明なだけで、
component-local placement自体は有効)として区別する。

【resolved global metadataの位置づけ】
ComponentGlobalResolutionは、Nodal Information(NodalConnection.solution)
から常に再計算できるderived dataであり、source of truthではない
(このモジュール自体は永続化を行わない)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from domain.nodal_connection import SolutionStatus
from domain.transform import RigidTransform2D


class GlobalResolutionStatus(str, Enum):
    NO_ANCHOR = "NO_ANCHOR"  # このcomponentにはLocal↔GLOBAL接続が1つも無い
    BLOCKED_BY_LOCAL_CONFLICT = "BLOCKED_BY_LOCAL_CONFLICT"  # Local↔Local側がCONFLICTのため、Global解決を試みていない
    ANCHOR_UNRESOLVABLE = "ANCHOR_UNRESOLVABLE"  # anchorはあるが、GlobalSpatialIdResolverが物理座標を返せない(NotImplementedError等)
    ANCHOR_INSUFFICIENT = "ANCHOR_INSUFFICIENT"  # 有効なanchorはあるが対応点が1点のみで、yawを確定できない
    RESOLVED = "RESOLVED"  # 1つ以上の有効なanchorから、矛盾なくGlobal transformを確定できた
    GLOBAL_CONFLICT = "GLOBAL_CONFLICT"  # 複数の有効なanchorが許容誤差を超えて不整合(fail closed)


class AnchorUnresolvableReason(str, Enum):
    INSUFFICIENT_CORRESPONDENCES = "INSUFFICIENT_CORRESPONDENCES"  # 対応点が1点以下
    GLOBAL_RESOLVER_NOT_IMPLEMENTED = "GLOBAL_RESOLVER_NOT_IMPLEMENTED"  # GlobalSpatialIdResolver.get_center()がNotImplementedError
    DEGENERATE = "DEGENERATE"  # 点群の広がりが不足していて回転が数値的に不定


@dataclass
class AnchorEstimate:
    """1つのLocal↔GLOBAL NodalConnection(anchor)から推定した、
    T_local_to_global(そのLocal Spaceの座標系1からGlobal物理座標への変換)。
    """

    connection_id: str
    local_space_id: str
    fit_status: SolutionStatus  # SOLVED / WARNING_HIGH_RESIDUAL / UNSOLVABLE(既存のenumを再利用)
    unresolvable_reason: Optional[AnchorUnresolvableReason] = None
    transform_local_to_global: Optional[RigidTransform2D] = None
    rmse_m: Optional[float] = None
    max_residual_m: Optional[float] = None
    n_correspondences: int = 0
    target_epsg: Optional[int] = None  # 実際に投影(geographic_projection.to_projected)に使ったCRS。未到達ならNone。

    @staticmethod
    def from_dict(data: dict) -> "AnchorEstimate":
        """derived resultの永続化(ロードマップPhase 3.6)から復元するための
        シリアライズ補助。数式・規約は無変更、単純な辞書化/復元のみ
        (2026-09-03、保存済みSpatial Resolution Resultを読み込んでGlobal
        export serviceへ渡すために追加)。"""
        return AnchorEstimate(
            connection_id=data["connection_id"],
            local_space_id=data["local_space_id"],
            fit_status=SolutionStatus(data["fit_status"]),
            unresolvable_reason=(
                AnchorUnresolvableReason(data["unresolvable_reason"]) if data.get("unresolvable_reason") else None
            ),
            transform_local_to_global=(
                RigidTransform2D.from_dict(data["transform_local_to_global"])
                if data.get("transform_local_to_global") else None
            ),
            rmse_m=data.get("rmse_m"),
            max_residual_m=data.get("max_residual_m"),
            n_correspondences=data.get("n_correspondences", 0),
            target_epsg=data.get("target_epsg"),
        )

    def to_dict(self) -> dict:
        return {
            "connection_id": self.connection_id,
            "local_space_id": self.local_space_id,
            "fit_status": self.fit_status.value,
            "unresolvable_reason": self.unresolvable_reason.value if self.unresolvable_reason else None,
            "transform_local_to_global": (
                self.transform_local_to_global.to_dict() if self.transform_local_to_global else None
            ),
            "rmse_m": self.rmse_m,
            "max_residual_m": self.max_residual_m,
            "n_correspondences": self.n_correspondences,
            "target_epsg": self.target_epsg,
        }


@dataclass
class GlobalAnchorConflict:
    """複数の有効なanchorから導いたcomponent root基準のGlobal transform候補
    同士が、許容誤差を超えて食い違った1件。"""

    connection_id_a: str
    connection_id_b: str
    local_space_id_a: str
    local_space_id_b: str
    yaw_diff_rad: float
    translation_diff_m: float

    @staticmethod
    def from_dict(data: dict) -> "GlobalAnchorConflict":
        return GlobalAnchorConflict(
            connection_id_a=data["connection_id_a"],
            connection_id_b=data["connection_id_b"],
            local_space_id_a=data["local_space_id_a"],
            local_space_id_b=data["local_space_id_b"],
            yaw_diff_rad=data["yaw_diff_rad"],
            translation_diff_m=data["translation_diff_m"],
        )

    def to_dict(self) -> dict:
        return {
            "connection_id_a": self.connection_id_a,
            "connection_id_b": self.connection_id_b,
            "local_space_id_a": self.local_space_id_a,
            "local_space_id_b": self.local_space_id_b,
            "yaw_diff_rad": self.yaw_diff_rad,
            "translation_diff_m": self.translation_diff_m,
        }


@dataclass
class ComponentGlobalResolution:
    """1つのconnected componentのGlobal Resolution結果。"""

    component_id: str
    status: GlobalResolutionStatus
    transform_root_to_global: Optional[RigidTransform2D] = None
    member_transforms_to_global: Dict[str, RigidTransform2D] = field(default_factory=dict)
    anchor_estimates: List[AnchorEstimate] = field(default_factory=list)
    conflicts: List[GlobalAnchorConflict] = field(default_factory=list)
    target_epsg: Optional[int] = None  # anchor推定に実際に使ったCRS(anchorが1件も対象にならなかった場合はNone)

    @staticmethod
    def from_dict(data: dict) -> "ComponentGlobalResolution":
        """保存済みSpatial Resolution Result(repositories/spatial_resolution_result_repository.py
        経由でJSON化されたもの)から復元する。ここでは値の変換・再計算は一切
        行わない(2026-09-03、Global export serviceが既存のderived resultを
        そのまま使うために追加)。"""
        return ComponentGlobalResolution(
            component_id=data["component_id"],
            status=GlobalResolutionStatus(data["status"]),
            transform_root_to_global=(
                RigidTransform2D.from_dict(data["transform_root_to_global"])
                if data.get("transform_root_to_global") else None
            ),
            member_transforms_to_global={
                space_id: RigidTransform2D.from_dict(t)
                for space_id, t in data.get("member_transforms_to_global", {}).items()
            },
            anchor_estimates=[AnchorEstimate.from_dict(a) for a in data.get("anchor_estimates", [])],
            conflicts=[GlobalAnchorConflict.from_dict(c) for c in data.get("conflicts", [])],
            target_epsg=data.get("target_epsg"),
        )

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "status": self.status.value,
            "transform_root_to_global": (
                self.transform_root_to_global.to_dict() if self.transform_root_to_global else None
            ),
            "member_transforms_to_global": {
                space_id: t.to_dict() for space_id, t in self.member_transforms_to_global.items()
            },
            "anchor_estimates": [a.to_dict() for a in self.anchor_estimates],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "target_epsg": self.target_epsg,
        }
