"""
backend/repositories/nodal_connection_repository.py

Nodal Connectionの永続化。既定では
`backend/data/registry/nodal_connections.json` に配列として保存する。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from domain import NodalConnection


class NodalConnectionRepository:
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

    def list_all(self, building_id: Optional[str] = None) -> List[NodalConnection]:
        connections = [NodalConnection.from_dict(d) for d in self._read()]
        if building_id is not None:
            connections = [c for c in connections if c.building_id == building_id]
        return connections

    def get(self, connection_id: str) -> Optional[NodalConnection]:
        for c in self.list_all():
            if c.connection_id == connection_id:
                return c
        return None

    def create(self, connection: NodalConnection) -> NodalConnection:
        rows = self._read()
        if any(r["connection_id"] == connection.connection_id for r in rows):
            raise ValueError(f"connection_id '{connection.connection_id}' は既に存在します。")
        rows.append(connection.to_dict())
        self._write(rows)
        return connection

    def update(self, connection: NodalConnection) -> NodalConnection:
        rows = self._read()
        updated = False
        new_rows = []
        for r in rows:
            if r["connection_id"] == connection.connection_id:
                new_rows.append(connection.to_dict())
                updated = True
            else:
                new_rows.append(r)
        if not updated:
            raise ValueError(f"connection_id '{connection.connection_id}' が見つかりません。")
        self._write(new_rows)
        return connection

    def delete(self, connection_id: str) -> None:
        rows = self._read()
        rows = [r for r in rows if r["connection_id"] != connection_id]
        self._write(rows)
