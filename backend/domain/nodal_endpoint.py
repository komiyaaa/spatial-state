"""
backend/domain/nodal_endpoint.py

結節点(Nodal Endpoint)。同一の実空間位置を示す点を、複数のLocal Space間、
または Local Space と Global 座標系との間で対応付けるための最小単位。

【重要】GLOBAL側は global_spatial_id のみを source of truth として保持し、
緯度経度等の物理座標を重複して保存しない(ユーザー指示: 2026-08-28)。
NodalConnection.solution の計算に物理座標が必要な場合は、
backend/spatial_id/global_spatial_id.py の GlobalSpatialIdResolver 経由で
その都度取得する。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class NodalEndpointType(str, Enum):
    LOCAL = "LOCAL"
    GLOBAL = "GLOBAL"


@dataclass
class NodalEndpoint:
    endpoint_id: str
    type: NodalEndpointType
    label: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    # type=LOCAL の場合に必須
    space_id: Optional[str] = None
    local_spatial_id: Optional[str] = None
    local_point: Optional[list] = None  # 表示用にキャッシュした代表座標(任意、derived_from_spatial_id)

    # type=GLOBAL の場合に必須。これがsource of truth(緯度経度等は保存しない)。
    global_spatial_id: Optional[str] = None

    def __post_init__(self):
        if self.type == NodalEndpointType.LOCAL:
            if not self.space_id or not self.local_spatial_id:
                raise ValueError("LOCALエンドポイントには space_id と local_spatial_id が必須です。")
        elif self.type == NodalEndpointType.GLOBAL:
            if not self.global_spatial_id:
                raise ValueError("GLOBALエンドポイントには global_spatial_id が必須です。")

    @staticmethod
    def from_dict(data: dict) -> "NodalEndpoint":
        return NodalEndpoint(
            endpoint_id=data["endpoint_id"],
            type=NodalEndpointType(data["type"]),
            label=data.get("label"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            space_id=data.get("space_id"),
            local_spatial_id=data.get("local_spatial_id"),
            local_point=data.get("local_point"),
            global_spatial_id=data.get("global_spatial_id"),
        )

    def to_dict(self) -> dict:
        return {
            "endpoint_id": self.endpoint_id,
            "type": self.type.value,
            "label": self.label,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "space_id": self.space_id,
            "local_spatial_id": self.local_spatial_id,
            "local_point": self.local_point,
            "global_spatial_id": self.global_spatial_id,
        }
