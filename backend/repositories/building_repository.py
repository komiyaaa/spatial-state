"""
backend/repositories/building_repository.py

建物(BUILDING)の永続化。既存 backend/registry.py の建物部分を、新しい
ドメインモデル(domain.Building)を返す形に切り出したもの。

【重要】保存先ファイルの形式・場所は現状維持(既定で backend/buildings.json)。
既存の registry.py・server.py はこのモジュールをまだ参照しない。
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import List, Optional

from domain import Building


class BuildingRepository:
    def __init__(self, path: Path):
        self.path = path
        if not self.path.exists():
            self._write([])

    def _read(self) -> List[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: List[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_all(self) -> List[Building]:
        return [Building.from_dict(d) for d in self._read()]

    def get(self, building_id: str) -> Optional[Building]:
        for b in self.list_all():
            if b.building_id == building_id:
                return b
        return None

    def create(self, name: str, real_estate_number: str = "", address: str = "") -> Building:
        if not name or not name.strip():
            raise ValueError("建物名(name)は必須です。")
        rows = self._read()
        existing_ids = {r["building_id"] for r in rows}
        building = Building(
            building_id=_unique_id(_slugify(name), existing_ids),
            real_estate_number=real_estate_number or "未設定",
            name=name,
            address=address,
        )
        rows.append(building.to_dict())
        self._write(rows)
        return building


def _slugify(name: str) -> str:
    """名称からID用のslugを作る(英字以外は除去)。数字だけが残るケース
    (例: "テスト校舎2" -> "2")は他と衝突しやすいため、英字を含まないslugは
    採用しない(空文字を返し、呼び出し側でuuidにフォールバックする)。"""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip()).strip("_").lower()
    if not re.search(r"[a-z]", slug):
        return ""
    return slug


def _unique_id(base: str, existing_ids: set) -> str:
    if not base:
        return f"building_{uuid.uuid4().hex[:8]}"
    if base not in existing_ids:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing_ids:
        suffix += 1
    return f"{base}_{suffix}"
