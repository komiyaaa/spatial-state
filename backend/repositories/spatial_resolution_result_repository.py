"""
backend/repositories/spatial_resolution_result_repository.py

building_idごとに、直近のSpatial Resolution実行結果(ロードマップPhase 3.6、
services.spatial_resolution_service.resolve_building()の出力)を保存する。

【重要】これはderived cache(source of truthの複製ではない)。source of
truthはあくまでNodal Information(backend/data/registry/nodal_endpoints.json・
nodal_connections.json)であり、ここに保存する内容はいつでも
POST /api/spatial-resolution/resolve を再実行して上書き・再生成できる。
1 building_id = 1ファイル(直近の実行結果のみ保持、履歴は持たない)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

from domain.spatial_resolution_result import ComponentResolutionResult


class SpatialResolutionResultRepository:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, building_id: str) -> Path:
        return self.base_dir / f"{building_id}.json"

    def save(self, building_id: str, results: List[ComponentResolutionResult], target_epsg: int) -> dict:
        data = {
            "building_id": building_id,
            "resolved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "target_epsg": target_epsg,
            "components": [r.to_dict() for r in results],
        }
        self._path(building_id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data

    def load(self, building_id: str) -> Optional[dict]:
        path = self._path(building_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def invalidate(self, building_id: str) -> bool:
        """building_idのキャッシュ済み結果を破棄する(Local Space削除機能、
        2026-09-03追加)。derived cacheのため、次回
        POST /api/spatial-resolution/resolve で再生成される想定。
        ファイルが存在しなかった場合はFalseを返す。"""
        path = self._path(building_id)
        if not path.exists():
            return False
        path.unlink()
        return True
