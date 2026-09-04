"""
backend/repositories/local_space_repository.py

ローカル空間(LOCAL_SPACE)の永続化(新スキーマ)。

- `coordinate_definition` は既存の `backend/space_definitions/{tokutei_code}.json`
  を唯一の参照先とする(複製・移動はしない。生成ロジックは
  space_definition_generator.py のまま変更しない)。
- それ以外の新フィールド(`real_estate_id`)は、新設の `local_spaces.json`
  (既定では `backend/data/registry/` 配下)に保持する。**現行の
  `backend/local_spaces.json` とは別ファイルであり、既存ファイルの形式には
  一切影響しない**(移行(migration)は別スクリプトが担当する)。

【resolved placementについて(2026-09-02削除)】
以前はここに`placements.json`+`save_placement()`という、LocalSpace 1件に
つきPlacement 1件を直接持たせる経路があったが、実際のNodal Information
解決パイプラインは一度もこれを呼ばず、building×component単位で結果を
キャッシュする別経路(repositories/spatial_resolution_result_repository.py)
だけが実際に使われていた。死んでいた経路を削除し、後者を正式仕様として
統一した(domain/local_space.py のコメント参照)。resolved placementは
`GET /api/spatial-resolution/results/<building_id>` から取得すること。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

from domain import CoordinateDefinition, LocalSpace


class LocalSpaceRepository:
    def __init__(self, base_dir: Path, space_definitions_dir: Path):
        self.local_spaces_path = base_dir / "local_spaces.json"
        self.space_definitions_dir = space_definitions_dir
        if not self.local_spaces_path.exists():
            self._write(self.local_spaces_path, [])

    @staticmethod
    def _read(path: Path):
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_coordinate_definition(self, space_id: str, tokutei_code: str) -> Optional[CoordinateDefinition]:
        """座標定義ファイルの正式な永続化キーはspace_id
        (`space_definitions/{space_id}.json`)。tokutei_code単独キー
        (`space_definitions/{tokutei_code}.json`)は既存データ互換のための
        read-onlyなlegacy fallbackであり、新規にこちらへ書き込むことは
        しない(ER図反映、2026-09-02。tokutei_codeはBuilding配下でのみ
        一意な識別コードであり、単独ではグローバルに一意ではないため、
        複数buildingが同じtokutei_codeを使うと衝突しうる)。
        両方存在する場合は必ずspace_id側を優先する。"""
        space_id_path = self.space_definitions_dir / f"{space_id}.json"
        if space_id_path.exists():
            return CoordinateDefinition.from_dict(json.loads(space_id_path.read_text(encoding="utf-8-sig")))

        legacy_path = self.space_definitions_dir / f"{tokutei_code}.json"
        if legacy_path.exists():
            return CoordinateDefinition.from_dict(json.loads(legacy_path.read_text(encoding="utf-8-sig")))

        return None

    def list_all(self, building_id: Optional[str] = None) -> List[LocalSpace]:
        rows = self._read(self.local_spaces_path) or []
        spaces = []
        for row in rows:
            if building_id is not None and row["building_id"] != building_id:
                continue
            spaces.append(
                LocalSpace(
                    space_id=row["space_id"],
                    building_id=row["building_id"],
                    tokutei_code=row["tokutei_code"],
                    floor=row["floor"],
                    zoom_level=row["zoom_level"],
                    registered_at=row["registered_at"],
                    coordinate_definition=self._load_coordinate_definition(row["space_id"], row["tokutei_code"]),
                    real_estate_id=row.get("real_estate_id"),
                )
            )
        return spaces

    def get(self, space_id: str) -> Optional[LocalSpace]:
        for space in self.list_all():
            if space.space_id == space_id:
                return space
        return None

    def delete(self, space_id: str) -> None:
        """local_spaces.jsonから該当行を削除する(Local Space削除機能、
        2026-09-03追加)。coordinate_definitionファイル自体はここでは
        削除しない(呼び出し側のアーカイブ処理が別途担当する)。"""
        rows = self._read(self.local_spaces_path) or []
        new_rows = [r for r in rows if r["space_id"] != space_id]
        if len(new_rows) == len(rows):
            raise ValueError(f"space_id '{space_id}' が見つかりません。")
        self._write(self.local_spaces_path, new_rows)

    def create(
        self,
        building_id: str,
        tokutei_code: str,
        floor: int,
        zoom_level: int,
        real_estate_id: Optional[str] = None,
    ) -> LocalSpace:
        if not tokutei_code or not tokutei_code.strip():
            raise ValueError("tokutei_code は空文字にできません。")

        rows = self._read(self.local_spaces_path) or []
        if any(r["building_id"] == building_id and r["tokutei_code"] == tokutei_code for r in rows):
            raise ValueError(
                f"tokutei_code '{tokutei_code}' はこの建物(building_id={building_id})内で"
                f"既に使用されています。tokutei_codeはbuilding内で一意である必要があります。"
            )

        space_id = f"{building_id}-{tokutei_code}"
        if any(r["space_id"] == space_id for r in rows):
            raise ValueError(f"space_id '{space_id}' は既に存在します。")

        row = {
            "space_id": space_id,
            "building_id": building_id,
            "tokutei_code": tokutei_code,
            "floor": floor,
            "zoom_level": zoom_level,
            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "real_estate_id": real_estate_id,
        }
        rows.append(row)
        self._write(self.local_spaces_path, rows)

        return LocalSpace(
            space_id=space_id,
            building_id=building_id,
            tokutei_code=tokutei_code,
            floor=floor,
            zoom_level=zoom_level,
            registered_at=row["registered_at"],
            coordinate_definition=self._load_coordinate_definition(space_id, tokutei_code),
            real_estate_id=real_estate_id,
        )
