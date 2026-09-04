"""
backend/repositories/nodal_endpoint_repository.py

結節点(NodalEndpoint)の永続化。既定では
`backend/data/registry/nodal_endpoints.json` に配列として保存する
(存在しなければ空配列で初期化。backend/registry.py の _seed_if_missing と
同じ思想)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from domain import NodalEndpoint


class NodalEndpointRepository:
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

    def list_all(self, space_id: Optional[str] = None) -> List[NodalEndpoint]:
        endpoints = [NodalEndpoint.from_dict(d) for d in self._read()]
        if space_id is not None:
            endpoints = [e for e in endpoints if e.space_id == space_id]
        return endpoints

    def get(self, endpoint_id: str) -> Optional[NodalEndpoint]:
        for e in self.list_all():
            if e.endpoint_id == endpoint_id:
                return e
        return None

    def create(self, endpoint: NodalEndpoint) -> NodalEndpoint:
        rows = self._read()
        if any(r["endpoint_id"] == endpoint.endpoint_id for r in rows):
            raise ValueError(f"endpoint_id '{endpoint.endpoint_id}' は既に存在します。")
        rows.append(endpoint.to_dict())
        self._write(rows)
        return endpoint

    def update(self, endpoint: NodalEndpoint) -> NodalEndpoint:
        """既存のNodalEndpointを置き換える(ロードマップPhase 3.6、
        PUT /api/nodal-endpoints/<id>用)。NodalConnectionRepository.update()と
        同じ素朴な置換方式。"""
        rows = self._read()
        updated = False
        new_rows = []
        for r in rows:
            if r["endpoint_id"] == endpoint.endpoint_id:
                new_rows.append(endpoint.to_dict())
                updated = True
            else:
                new_rows.append(r)
        if not updated:
            raise ValueError(f"endpoint_id '{endpoint.endpoint_id}' が見つかりません。")
        self._write(new_rows)
        return endpoint

    def delete(self, endpoint_id: str) -> None:
        rows = self._read()
        rows = [r for r in rows if r["endpoint_id"] != endpoint_id]
        self._write(rows)
