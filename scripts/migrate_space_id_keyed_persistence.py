#!/usr/bin/env python
"""
scripts/migrate_space_id_keyed_persistence.py

Base Map(base_maps/*.las 等)・CoordinateDefinition(space_definitions/*.json)の
正式な永続化キーを、tokutei_code単独からspace_idへ移行する、一回限りの
非破壊的コピー移行スクリプト(ER図反映、2026-09-02)。

【背景】tokutei_codeはBuilding配下でのみ一意な識別コードであり(グローバル
一意性は恒久設計にしない)、space_idのみがグローバルに一意な識別子である。
にもかかわらず base_maps/{tokutei_code}.ext・space_definitions/{tokutei_code}.json
はtokutei_code単独をファイルキーにしていたため、異なるbuildingが同じ
tokutei_codeを使うと衝突しうる不具合があった(監査で発見)。
backend/server.py側の読み込みは既にspace_id優先/tokutei_codeフォールバック
に変更済み(このスクリプトはデータ側の移行のみを担当)。

【非破壊の原則】
- 既存の {tokutei_code}.json / {tokutei_code}.ext は一切削除・変更しない。
- {space_id}.json / {space_id}.ext として"コピー"を追加するだけ。
- base_maps/manifest.jsonは、tokutei_codeキーのエントリを残したまま、
  space_idキーのエントリを追記する(既存エントリの上書きはしない)。
- 既にspace_idキーのファイル/エントリが存在する場合はスキップする(idempotent)。

使い方:
    python scripts/migrate_space_id_keyed_persistence.py            # --dry-run と同じ(既定)
    python scripts/migrate_space_id_keyed_persistence.py --dry-run  # 計画を表示するだけ、書き込みなし
    python scripts/migrate_space_id_keyed_persistence.py --apply    # 実際にコピーする(事前に必ずbackupを取る)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

LOCAL_SPACES_PATH = BACKEND_DIR / "data" / "registry" / "local_spaces.json"
SPACE_DEFINITIONS_DIR = BACKEND_DIR / "space_definitions"
BASE_MAPS_DIR = REPO_ROOT / "base_maps"
MANIFEST_PATH = BASE_MAPS_DIR / "manifest.json"

DEFAULT_BACKUP_ROOT = BACKEND_DIR / "data" / "_migration_backup"


@dataclass
class SpaceCopyPlan:
    space_id: str
    tokutei_code: str
    space_definition_copy: Optional[Path] = None  # コピー先(space_definitions/{space_id}.json)。Noneなら対象外
    base_map_copy: Optional[Path] = None  # コピー先(base_maps/{space_id}{ext})。Noneなら対象外
    manifest_entry: Optional[dict] = None  # 追記するmanifestエントリ。Noneなら対象外
    notes: List[str] = field(default_factory=list)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def build_migration_plan() -> List[SpaceCopyPlan]:
    """local_spaces.json(LocalSpaceRepositoryのsource of truth)の全space_idについて、
    space_id側ファイルがまだ無いものだけを移行対象として計画する。読み込みのみ、
    書き込みは行わない。"""
    local_spaces = _read_json(LOCAL_SPACES_PATH, [])
    manifest = _read_json(MANIFEST_PATH, [])
    manifest_ids = {e.get("id") for e in manifest}

    plans: List[SpaceCopyPlan] = []
    for row in local_spaces:
        space_id = row["space_id"]
        tokutei_code = row["tokutei_code"]
        plan = SpaceCopyPlan(space_id=space_id, tokutei_code=tokutei_code)

        # --- space_definitions ---
        space_id_def_path = SPACE_DEFINITIONS_DIR / f"{space_id}.json"
        tokutei_def_path = SPACE_DEFINITIONS_DIR / f"{tokutei_code}.json"
        if space_id_def_path.exists():
            plan.notes.append(f"space_definitions/{space_id}.json は既に存在するためスキップ")
        elif tokutei_def_path.exists():
            plan.space_definition_copy = space_id_def_path
        else:
            plan.notes.append(f"space_definitions/{tokutei_code}.json が見つからないためコピー対象外")

        # --- base_maps ---
        if space_id in manifest_ids:
            plan.notes.append(f"base_maps manifestに id='{space_id}' のエントリが既に存在するためスキップ")
        else:
            tokutei_entry = next((e for e in manifest if e.get("id") == tokutei_code), None)
            if tokutei_entry is None:
                plan.notes.append(f"base_maps manifestに id='{tokutei_code}' のエントリが見つからないためコピー対象外")
            else:
                src = BASE_MAPS_DIR / tokutei_entry["file"]
                if not src.exists():
                    plan.notes.append(f"base_maps/{tokutei_entry['file']} が実ファイルとして存在しないためコピー対象外")
                else:
                    ext = src.suffix
                    dest_filename = f"{space_id}{ext}"
                    plan.base_map_copy = BASE_MAPS_DIR / dest_filename
                    plan.manifest_entry = {"id": space_id, "label": space_id, "file": dest_filename}

        plans.append(plan)
    return plans


def _backup_existing_data(backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    if SPACE_DEFINITIONS_DIR.exists():
        shutil.copytree(SPACE_DEFINITIONS_DIR, backup_dir / "space_definitions", dirs_exist_ok=True)
    if BASE_MAPS_DIR.exists():
        shutil.copytree(BASE_MAPS_DIR, backup_dir / "base_maps", dirs_exist_ok=True)


def apply_migration(plans: List[SpaceCopyPlan], backup_dir: Path) -> dict:
    """実際にコピーする。既存ファイルの削除・上書きは一切行わない。"""
    _backup_existing_data(backup_dir)

    copied_space_defs, copied_base_maps = [], []
    manifest = _read_json(MANIFEST_PATH, [])
    manifest_changed = False

    for plan in plans:
        if plan.space_definition_copy is not None:
            src = SPACE_DEFINITIONS_DIR / f"{plan.tokutei_code}.json"
            if not plan.space_definition_copy.exists():  # 冪等性の最終防衛(計画時と実行時のズレに備える)
                shutil.copy2(src, plan.space_definition_copy)
                copied_space_defs.append(plan.space_definition_copy)

        if plan.base_map_copy is not None and plan.manifest_entry is not None:
            tokutei_entry = next((e for e in manifest if e.get("id") == plan.tokutei_code), None)
            if tokutei_entry is not None and not plan.base_map_copy.exists():
                src = BASE_MAPS_DIR / tokutei_entry["file"]
                shutil.copy2(src, plan.base_map_copy)
                copied_base_maps.append(plan.base_map_copy)
                if not any(e.get("id") == plan.manifest_entry["id"] for e in manifest):
                    manifest.append(plan.manifest_entry)
                    manifest_changed = True

    if manifest_changed:
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "copied_space_defs": copied_space_defs,
        "copied_base_maps": copied_base_maps,
        "manifest_changed": manifest_changed,
    }


def print_report(plans: List[SpaceCopyPlan], mode: str, backup_dir: Optional[Path] = None,
                  apply_result: Optional[dict] = None) -> None:
    print("=" * 70)
    print(f"migrate_space_id_keyed_persistence.py -- mode: {mode}")
    print("=" * 70)

    for plan in plans:
        print(f"\n[{plan.space_id}] (tokutei_code={plan.tokutei_code})")
        if plan.space_definition_copy:
            print(f"  space_definitions: {plan.tokutei_code}.json -> {plan.space_definition_copy.name} をコピー")
        if plan.base_map_copy:
            print(f"  base_maps: -> {plan.base_map_copy.name} をコピー + manifestへ id='{plan.space_id}' を追記")
        for note in plan.notes:
            print(f"  ({note})")

    if mode == "dry-run":
        print(f"\n--dry-run のため未実行。--apply 時は既定で {DEFAULT_BACKUP_ROOT}/{{timestamp}}/ にbackupを作成します。")
    else:
        print(f"\nbackup先: {backup_dir}")
        print(f"コピーしたspace_definitions: {[p.name for p in apply_result['copied_space_defs']] or 'なし'}")
        print(f"コピーしたbase_maps: {[p.name for p in apply_result['copied_base_maps']] or 'なし'}")
        print(f"manifest更新: {'あり' if apply_result['manifest_changed'] else 'なし'}")
        print("\n元ファイル(tokutei_code単独キー)は一切削除・変更していません。")
    print("=" * 70)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="計画を表示するだけで、何も書き込まない(既定)")
    mode_group.add_argument("--apply", action="store_true", help="実際にコピーする(事前に必ずbackupを取る)")
    parser.add_argument("--backup-dir", type=Path, default=None,
                         help=f"backup先ディレクトリ(既定: {DEFAULT_BACKUP_ROOT}/{{timestamp}}/)")
    args = parser.parse_args(argv)

    plans = build_migration_plan()

    if not args.apply:
        print_report(plans, mode="dry-run")
        return 0

    backup_dir = args.backup_dir or (DEFAULT_BACKUP_ROOT / time.strftime("%Y%m%dT%H%M%S"))
    result = apply_migration(plans, backup_dir)
    print_report(plans, mode="apply", backup_dir=backup_dir, apply_result=result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
