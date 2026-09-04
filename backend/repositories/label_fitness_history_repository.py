"""
backend/repositories/label_fitness_history_repository.py

LabelFitnessHistoryEntry(構造ラベル競合判定の履歴)の永続化。space_idごとに
1ファイル(`backend/data/structural_label_fitness_history/{space_id}.json`、
配列)に追記していく。

【既存のLABEL_FITNESS_HISTORYとの違い・注意】
spatial_state/state_store.py にも"label_fitness_history"という語が既に
登場するが、あちらはSpatial State側の別概念(構造ラベルごとのVGICP
fitness_score蓄積、w_fit計算用、SpatialStateTracker.fitness_history)であり、
このリポジトリが扱う「voxel構造ラベルの競合解決履歴」とは完全に別物・
別ファイルである(ディレクトリも異なる: tracker_state/ vs
structural_label_fitness_history/)。混同しないこと。

save_all()ではなくappend()なのは、これが監査ログ(追記のみ)という
位置づけのため(SPATIAL_VOXEL_LABELは「現在の解決結果」を上書き保存するが、
このリポジトリは過去の判定を消さない)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from domain.structural_label import LabelFitnessHistoryEntry


class LabelFitnessHistoryRepository:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, space_id: str) -> Path:
        return self.base_dir / f"{space_id}.json"

    def append(self, space_id: str, entries: List[LabelFitnessHistoryEntry]) -> None:
        if not entries:
            return
        path = self._path(space_id)
        existing = []
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
        existing.extend(e.to_dict() for e in entries)
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_all(self, space_id: str) -> List[LabelFitnessHistoryEntry]:
        path = self._path(space_id)
        if not path.exists():
            return []
        return [LabelFitnessHistoryEntry.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]
