"""
backend/domain/nodal_connection.py

Nodal Connection: 2つの空間(Local↔Local または Local↔Global)の間にある、
複数の対応結節点(NodalEndpoint)の集合と、そこから推定される座標変換(solution)。

【Phase区分】solutionの実際の計算(N点最小二乗フィット、yaw+translation推定)は
backend/services/spatial_transform_solver.py(Phase 3で実装予定)が担当する。
このモジュールはデータ構造のみを定義し、未計算時は solution.status="UNSOLVED"
のまま保持できる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ConnectionEndpointType(str, Enum):
    LOCAL = "LOCAL"
    GLOBAL = "GLOBAL"


class ConnectionType(str, Enum):
    """意味属性のみ。座標変換(solution)の算出には使用しない(V2指示書§5.4)。"""

    ENTRANCE = "entrance"
    DOOR = "door"
    PASSAGE = "passage"
    STAIRS = "stairs"
    ELEVATOR = "elevator"
    VERTICAL_CONNECTION = "vertical_connection"
    OTHER = "other"


class SolutionStatus(str, Enum):
    UNSOLVED = "UNSOLVED"
    SOLVED = "SOLVED"
    WARNING_HIGH_RESIDUAL = "WARNING_HIGH_RESIDUAL"
    UNSOLVABLE = "UNSOLVABLE"


@dataclass
class ConnectionEndpointRef:
    """この接続がどの空間とどの空間を結ぶかを示す参照(対応点そのものではない)。"""

    type: ConnectionEndpointType
    space_id: Optional[str] = None  # LOCALの場合必須、GLOBALの場合None

    @staticmethod
    def from_dict(data: dict) -> "ConnectionEndpointRef":
        return ConnectionEndpointRef(type=ConnectionEndpointType(data["type"]), space_id=data.get("space_id"))

    def to_dict(self) -> dict:
        return {"type": self.type.value, "space_id": self.space_id}


@dataclass
class Correspondence:
    """1組の対応点ペア(NodalEndpoint.endpoint_id同士の参照)。"""

    pair_id: str
    node_a_id: str
    node_b_id: str

    @staticmethod
    def from_dict(data: dict) -> "Correspondence":
        return Correspondence(pair_id=data["pair_id"], node_a_id=data["node_a_id"], node_b_id=data["node_b_id"])

    def to_dict(self) -> dict:
        return {"pair_id": self.pair_id, "node_a_id": self.node_a_id, "node_b_id": self.node_b_id}


@dataclass
class ConnectionSolution:
    """N点最小二乗フィットの結果(Phase 3でspatial_transform_solverが算出)。"""

    status: SolutionStatus = SolutionStatus.UNSOLVED
    n_correspondences: int = 0
    yaw_rad: Optional[float] = None
    translation: Optional[list] = None  # [tx, ty, tz]
    rmse_m: Optional[float] = None
    max_residual_m: Optional[float] = None
    residuals: list = field(default_factory=list)
    updated_at: Optional[str] = None

    @staticmethod
    def unsolved() -> "ConnectionSolution":
        return ConnectionSolution()

    @staticmethod
    def from_dict(data: Optional[dict]) -> "ConnectionSolution":
        if not data:
            return ConnectionSolution.unsolved()
        return ConnectionSolution(
            status=SolutionStatus(data.get("status", "UNSOLVED")),
            n_correspondences=data.get("n_correspondences", 0),
            yaw_rad=data.get("yaw_rad"),
            translation=data.get("translation"),
            rmse_m=data.get("rmse_m"),
            max_residual_m=data.get("max_residual_m"),
            residuals=data.get("residuals", []),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "n_correspondences": self.n_correspondences,
            "yaw_rad": self.yaw_rad,
            "translation": self.translation,
            "rmse_m": self.rmse_m,
            "max_residual_m": self.max_residual_m,
            "residuals": self.residuals,
            "updated_at": self.updated_at,
        }


@dataclass
class NodalConnection:
    connection_id: str
    building_id: str
    endpoint_space_a: ConnectionEndpointRef
    endpoint_space_b: ConnectionEndpointRef
    correspondences: list  # list[Correspondence]
    connection_type: ConnectionType = ConnectionType.OTHER
    solution: ConnectionSolution = field(default_factory=ConnectionSolution.unsolved)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self):
        a, b = self.endpoint_space_a, self.endpoint_space_b
        if (
            a.type == ConnectionEndpointType.LOCAL
            and b.type == ConnectionEndpointType.LOCAL
            and a.space_id == b.space_id
        ):
            raise ValueError("同一Local Spaceの自己接続は禁止されています。")

    @staticmethod
    def from_dict(data: dict) -> "NodalConnection":
        return NodalConnection(
            connection_id=data["connection_id"],
            building_id=data["building_id"],
            endpoint_space_a=ConnectionEndpointRef.from_dict(data["endpoint_space_a"]),
            endpoint_space_b=ConnectionEndpointRef.from_dict(data["endpoint_space_b"]),
            correspondences=[Correspondence.from_dict(c) for c in data.get("correspondences", [])],
            connection_type=ConnectionType(data.get("connection_type", "other")),
            solution=ConnectionSolution.from_dict(data.get("solution")),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict:
        return {
            "connection_id": self.connection_id,
            "building_id": self.building_id,
            "endpoint_space_a": self.endpoint_space_a.to_dict(),
            "endpoint_space_b": self.endpoint_space_b.to_dict(),
            "connection_type": self.connection_type.value,
            "correspondences": [c.to_dict() for c in self.correspondences],
            "solution": self.solution.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
