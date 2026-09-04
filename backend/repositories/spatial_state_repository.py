"""
backend/repositories/spatial_state_repository.py

占有状態(spatial_state)永続化への薄いラッパー。既存 state_store.py・
spatial_state パッケージの内部ロジック(数式・パラメータ・更新アルゴリズム)は
一切変更しない(V2指示書§4: 更新エンジンは今回の対象外。ここではPhase 5以降で
他のrepositoryと統一的に扱えるよう、命名・呼び出し方の境界だけを揃える)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from spatial_state import Params, SpatialStateTracker
from state_store import StateStore


class SpatialStateRepository:
    def __init__(self, base_dir: Path):
        self._store = StateStore(base_dir)

    def load(self, space_id: str, params: Optional[Params] = None) -> SpatialStateTracker:
        return self._store.load(space_id, params)

    def save(self, space_id: str, tracker: SpatialStateTracker, session: Optional[dict] = None) -> None:
        self._store.save(space_id, tracker, session=session)
