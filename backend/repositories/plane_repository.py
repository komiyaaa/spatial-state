"""
backend/repositories/plane_repository.py

Plane(RANSACで抽出した構造平面)の永続化。space_idごとに1ファイル
(`backend/data/planes/{space_id}.json`、配列)に保存する。

Base Mapから再生成可能なderived dataという位置づけのため、
save_planes()は「Detect Planesを再実行したら丸ごと置き換える」という
上書きセマンティクスにしている(差分マージはしない)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from domain.structural_label import PLANE_LABELS, Plane, StructuralLabel


class PlaneRepository:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, space_id: str) -> Path:
        return self.base_dir / f"{space_id}.json"

    def save_planes(self, space_id: str, planes: List[Plane]) -> None:
        path = self._path(space_id)
        path.write_text(
            json.dumps([p.to_dict() for p in planes], ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_planes(self, space_id: str) -> List[Plane]:
        path = self._path(space_id)
        if not path.exists():
            return []
        return [Plane.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]

    def update_plane_label(self, space_id: str, plane_id: str, confirmed_label: StructuralLabel) -> Plane:
        if confirmed_label not in PLANE_LABELS:
            raise ValueError(
                f"confirmed_labelに不正な値です(FLOOR/CEILING/WALL/IGNORE/UNASSIGNEDのみ有効): "
                f"{confirmed_label!r}"
            )
        planes = self.load_planes(space_id)
        updated: Optional[Plane] = None
        for p in planes:
            if p.plane_id == plane_id:
                p.confirmed_label = confirmed_label
                updated = p
                break
        if updated is None:
            raise ValueError(f"plane_id '{plane_id}' が space_id '{space_id}' の中に見つかりません。")
        self.save_planes(space_id, planes)
        return updated
