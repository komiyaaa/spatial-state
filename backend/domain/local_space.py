"""
backend/domain/local_space.py

ローカル空間(LOCAL_SPACE)のドメインモデル。

- CoordinateDefinition: ベースマップから決まる、Local Space単体の座標定義
  (provisional)。既存 backend/space_definition_generator.py が生成する
  space_definitions/*.json とフィールド互換(生成ロジック・値の意味は不変)。
  PCAによる回転角は「Local Space内部を整理するための暫定値」であり、
  Globalに対する真の方位角と解釈しないこと。

【resolved placementについて(2026-09-02、ER図反映で正式化)】
当初はLocalSpace 1件につきPlacement 1件を直接持たせる設計(旧`Placement`/
`PlacementStatus`、`LocalSpaceRepository.save_placement()`)を試みたが、
実際のNodal Information解決パイプライン(services/spatial_resolution_service.py、
services/global_resolution_service.py)は一度も書き込まず、building×component
単位でのみ解決結果をキャッシュする設計(repositories/spatial_resolution_result_repository.py、
domain/spatial_resolution_result.py の ComponentResolutionResult)へ発展した。
この2つの経路が併存し、前者が常にUNRESOLVEDのまま死んでいたため、後者を
正式なderived/recomputableな結果として採用し、前者(Placement/PlacementStatus/
save_placement/placements.json)は削除した。resolved placementを取得したい
場合は `GET /api/spatial-resolution/results/<building_id>` を使うこと。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CoordinateDefinition:
    """既存 space_definitions/*.json と同一形式(provisional)。"""

    id: str
    degree: float
    rad: float
    height: float
    origin: list
    unit_size: dict  # JSON上のキーは "unit-size"(ハイフンはPython識別子に使えないため)
    bounds: list

    @staticmethod
    def from_dict(data: dict) -> "CoordinateDefinition":
        return CoordinateDefinition(
            id=data["id"],
            degree=data["degree"],
            rad=data["rad"],
            height=data["height"],
            origin=data["origin"],
            unit_size=data["unit-size"],
            bounds=data["bounds"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "degree": self.degree,
            "rad": self.rad,
            "height": self.height,
            "origin": self.origin,
            "unit-size": self.unit_size,
            "bounds": self.bounds,
        }


@dataclass
class LocalSpace:
    space_id: str
    building_id: str
    tokutei_code: str
    floor: int
    zoom_level: int
    registered_at: str
    coordinate_definition: Optional[CoordinateDefinition] = None
    # 不動産IDルールガイドラインへの将来の連携用。tokutei_code(Local Space内での
    # 識別子)とは独立した概念であり、同一フィールドとして扱わないこと
    # (ユーザー指示: 2026-08-28)。
    real_estate_id: Optional[str] = None

    def __post_init__(self):
        if not self.tokutei_code or not self.tokutei_code.strip():
            raise ValueError("tokutei_code は空文字にできません。")

    @staticmethod
    def from_dict(data: dict) -> "LocalSpace":
        coordinate_definition = data.get("coordinate_definition")
        return LocalSpace(
            space_id=data["space_id"],
            building_id=data["building_id"],
            tokutei_code=data["tokutei_code"],
            floor=data["floor"],
            zoom_level=data["zoom_level"],
            registered_at=data["registered_at"],
            coordinate_definition=(
                CoordinateDefinition.from_dict(coordinate_definition) if coordinate_definition else None
            ),
            real_estate_id=data.get("real_estate_id"),
        )

    def to_dict(self) -> dict:
        return {
            "space_id": self.space_id,
            "building_id": self.building_id,
            "tokutei_code": self.tokutei_code,
            "floor": self.floor,
            "zoom_level": self.zoom_level,
            "registered_at": self.registered_at,
            "coordinate_definition": (
                self.coordinate_definition.to_dict() if self.coordinate_definition else None
            ),
            "real_estate_id": self.real_estate_id,
        }
