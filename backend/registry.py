"""
backend/registry.py

建物(BUILDING)・ローカル空間(LOCAL_SPACE)の一覧を、JSONファイルで永続化する
簡易ストア(backend/buildings.json / backend/local_spaces.json)。

base_maps/manifest.json・backend/space_definitions/*.json と同じ実データ
(使い捨ての中間生成物ではなく、git管理する対象)という位置づけのため、
backend/data/(README記載の通りGit管理外)ではなく、backend/直下に置く。

state_store.py(SpatialStateTrackerの永続化)とはファイル形式の思想は同じ
(dataclass等は使わず、素朴なdictのリストとしてそのまま読み書きする)だが、
保存先ディレクトリの位置づけが異なる点に注意。GUIから「建物を追加」→
「ローカル空間を追加」する一連のフローが、ここに記録される。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional


class Registry:
    def __init__(self, data_dir: Path):
        self.buildings_path = data_dir / "buildings.json"
        self.local_spaces_path = data_dir / "local_spaces.json"
        self._seed_if_missing()

    def _seed_if_missing(self) -> None:
        """初回起動時、ファイルが無ければ従来ハードコードされていたデータを
        シードとして書き込む(既存の表示を壊さないため)。"""
        if not self.buildings_path.exists():
            self._write(self.buildings_path, [
                {"building_id": "ichigaya_tamachi", "real_estate_number": "未設定",
                 "name": "市ヶ谷田町校舎", "address": ""},
            ])
        if not self.local_spaces_path.exists():
            self._write(self.local_spaces_path, [
                {"space_id": "ichigaya_tamachi-G002", "building_id": "ichigaya_tamachi",
                 "tokutei_code": "G002", "floor": 1, "zoom_level": 9,
                 "registered_at": "2026-07-20T09:00:00+09:00"},
            ])

    @staticmethod
    def _read(path: Path) -> list:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, data: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- buildings ---

    def list_buildings(self) -> list:
        return self._read(self.buildings_path)

    def create_building(self, name: str, real_estate_number: str = "", address: str = "") -> dict:
        if not name or not name.strip():
            raise ValueError("建物名(name)は必須です。")

        buildings = self.list_buildings()
        existing_ids = {b["building_id"] for b in buildings}
        building_id = _unique_id(_slugify(name), existing_ids)

        building = {
            "building_id": building_id,
            "real_estate_number": real_estate_number or "未設定",
            "name": name,
            "address": address,
        }
        buildings.append(building)
        self._write(self.buildings_path, buildings)
        return building

    # --- local spaces ---
    #
    # 【非推奨(ロードマップPhase 3.7)】以下2メソッド・backend/local_spaces.json は、
    # server.pyからは呼ばれなくなった。Local Spaceの永続化は
    # repositories.local_space_repository.LocalSpaceRepository
    # (backend/data/registry/local_spaces.json)を唯一のsource of truthとする。
    # 理由: このRegistry(backend/local_spaces.json)とLocalSpaceRepository
    # (backend/data/registry/local_spaces.json)の2つに書き込み先が分裂しており、
    # POST /api/local-spaces で新規作成したLocal Spaceが、Nodal Information側
    # (LocalSpatialIdResolver等、backend/data/registry/を参照する)から見えない、
    # という不整合が実際に確認された(2026-09-01時点の実データで、両ファイルの
    # ichigaya_tamachi-G002のzoom_level/registered_atが食い違っていたことで発覚)。
    # 恒久的な二重書き込みにはせず、新規作成の書き込み先を
    # LocalSpaceRepository側に一本化した(server.py参照)。このRegistry側は
    # 削除せず残しているが、新規に呼び出し元を追加しないこと。

    def list_local_spaces(self, building_id: Optional[str] = None) -> list:
        spaces = self._read(self.local_spaces_path)
        if building_id is not None:
            spaces = [s for s in spaces if s["building_id"] == building_id]
        return spaces

    def create_local_space(self, building_id: str, tokutei_code: str, floor: int, zoom_level: int) -> dict:
        if not tokutei_code or not tokutei_code.strip():
            raise ValueError("特定コード(tokutei_code)は必須です。")

        spaces = self._read(self.local_spaces_path)
        space_id = f"{building_id}-{tokutei_code}"
        if any(s["space_id"] == space_id for s in spaces):
            raise ValueError(
                f"space_id '{space_id}' は既に存在します"
                f"(building_id・tokutei_codeの組み合わせが重複しています)。"
            )

        space = {
            "space_id": space_id,
            "building_id": building_id,
            "tokutei_code": tokutei_code,
            "floor": floor,
            "zoom_level": zoom_level,
            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        spaces.append(space)
        self._write(self.local_spaces_path, spaces)
        return space


def _slugify(name: str) -> str:
    """名称からID用のslugを作る(英字以外は除去)。日本語のみの名称など、
    英字成分が残らない場合は空文字を返す(呼び出し側でuuidにフォールバックする)。

    数字だけが残るケース(例: "テスト校舎2" -> "2")は、他の建物とIDが
    衝突しやすく紛らわしいため、英字を含まないslugは採用しない。
    """
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip()).strip("_").lower()
    if not re.search(r"[a-z]", slug):
        return ""
    return slug


def _unique_id(base: str, existing_ids: set) -> str:
    """baseが空、または既存と衝突する場合に、一意なIDへ調整する。
    (日本語名称などbaseが空になるケースが多いため、その場合はuuidで代替する)"""
    if not base:
        return f"building_{uuid.uuid4().hex[:8]}"
    if base not in existing_ids:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing_ids:
        suffix += 1
    return f"{base}_{suffix}"
