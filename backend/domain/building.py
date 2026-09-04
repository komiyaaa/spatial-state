"""
backend/domain/building.py

建物(BUILDING)のドメインモデル。V2指示書§5.1の通り、既存スキーマを維持する
(不動産番号等のルールも変更しない)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Building:
    building_id: str
    real_estate_number: str
    name: str
    address: str

    @staticmethod
    def from_dict(data: dict) -> "Building":
        return Building(
            building_id=data["building_id"],
            real_estate_number=data.get("real_estate_number", ""),
            name=data["name"],
            address=data.get("address", ""),
        )

    def to_dict(self) -> dict:
        return {
            "building_id": self.building_id,
            "real_estate_number": self.real_estate_number,
            "name": self.name,
            "address": self.address,
        }
