"""
backend/repositories/spatial_voxel_label_repository.py

SpatialVoxelLabel(space_id・local_spatial_idごとの構造ラベル解決結果)の
永続化。space_idごとに1ファイル(`backend/data/voxel_labels/{space_id}.json`、
local_spatial_id -> SpatialVoxelLabel の辞書)に保存する。

【重要】local_spatial_idは、Local Spaceごとに独立した意味を持つ文字列
(同じ"z/f/x/y"でも別Local Spaceでは別voxel)。このリポジトリは必ず
space_idごとに別ファイルへ分離することで、この前提を構造的に保証する
(異なるspace_idのラベルが同じ辞書に混在することはあり得ない)。

Plane同様、Base Map・Plane・CoordinateDefinitionから再生成可能なderived
dataという位置づけのため、save_all()は上書きセマンティクス。
SPATIAL_VOXEL_STATE(spatial_state/state_store.py)とは別ファイル・別
モジュールであり、混在させない。

【メモリキャッシュについて(ユーザー指示: 2026-08-31、ロードマップStep 4)】
実データ(G002)でこのJSONファイルが約421MBに達することを確認しており、
Viewerの色分け(Structural Label表示)のたびに毎回パースすると顕著な
遅延になる。load_all()の返り値をファイルのmtimeと共にプロセス内メモリへ
キャッシュし、ファイルが変更されていなければ再パースしない(source data
自体・保存形式は無変更。純粋に読み込み側の性能最適化)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from domain.structural_label import SpatialVoxelLabel


class SpatialVoxelLabelRepository:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Dict[str, Tuple[float, Dict[str, SpatialVoxelLabel]]] = {}

    def _path(self, space_id: str) -> Path:
        return self.base_dir / f"{space_id}.json"

    def save_all(self, space_id: str, voxel_labels: List[SpatialVoxelLabel]) -> None:
        path = self._path(space_id)
        data = {v.local_spatial_id: v.to_dict() for v in voxel_labels}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._memory_cache.pop(space_id, None)  # 保存内容が変わったので、次回load_allで読み直す

    def load_all(self, space_id: str) -> Dict[str, SpatialVoxelLabel]:
        path = self._path(space_id)
        if not path.exists():
            return {}

        mtime = path.stat().st_mtime
        cached = self._memory_cache.get(space_id)
        if cached is not None and cached[0] == mtime:
            return cached[1]

        raw = json.loads(path.read_text(encoding="utf-8"))
        result = {sid: SpatialVoxelLabel.from_dict(d) for sid, d in raw.items()}
        self._memory_cache[space_id] = (mtime, result)
        return result

    def get(self, space_id: str, local_spatial_id: str) -> SpatialVoxelLabel | None:
        return self.load_all(space_id).get(local_spatial_id)
