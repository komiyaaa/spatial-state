#!/usr/bin/env python
"""
scripts/migrate_gui_v1_to_v2.py

ClaudeCode_指示書_NodalInformation_SpatialNetwork_IntegratedView_V2.md の
Phase 2(domain + repository)に伴う、旧スキーマ→新スキーマのmigrationスクリプト。

旧スキーマ(読み込むだけで、内容は一切変更しない):
    backend/buildings.json
    backend/local_spaces.json
    backend/space_definitions/*.json

新スキーマ(--apply時のみ書き込む。backend/repositories/ が読み書きする形式):
    backend/data/registry/buildings.json         建物(旧とほぼ同一)
    backend/data/registry/local_spaces.json      real_estate_id(nullable)を追加
    backend/data/registry/placements.json        全LocalSpaceにUNRESOLVEDの
                                                  Placementを新規生成
                                                  (既存座標定義の degree/rad/origin は
                                                   あくまで provisional coordinate
                                                   definition であり、Global座標として
                                                   解釈しない。Global placementは、
                                                   今後Nodal Informationの2点以上の
                                                   対応とSpatial Resolverによって
                                                   初めてRESOLVEDになる)
    backend/data/registry/nodal_endpoints.json   空配列で新規作成
    backend/data/registry/nodal_connections.json 空配列で新規作成

    backend/space_definitions/{tokutei_code}.json は、新schemaのcanonicalな
    座標定義ファイルとして引き続きこの場所を直接参照する。

【旧命名揺れ(legacy filename)の吸収】(ユーザー指示: 2026-08-28)
    新schemaのcanonicalな命名は "{tokutei_code}.json"。旧データには
    "G002v3.json" のようなバージョン付き命名の揺れがあるため、
    "{tokutei_code}v*.json" も候補として探索する。
      - candidateがcanonical名でちょうど1件見つかる          -> "exact"
      - canonical名は無いが、legacy候補がちょうど1件         -> "legacy_unique"
        (--apply時、この1件をcanonical名にコピーする。元ファイルは削除しない)
      - legacy候補が複数ある場合                              -> "legacy_ambiguous"
        (自動選択しない。warningとして報告し、そのtokutei_codeの
         coordinate_definitionはcanonicalファイルが用意されるまで解決しない)
      - 候補が1件も無い場合                                    -> "missing"

使い方:
    python scripts/migrate_gui_v1_to_v2.py            # --dry-run と同じ(既定)
    python scripts/migrate_gui_v1_to_v2.py --dry-run  # 変換結果を表示するだけ、書き込みなし
    python scripts/migrate_gui_v1_to_v2.py --apply    # 実際に書き込む(事前に必ずbackupを取る)

設計上の要件:
    - --dry-run(既定): 何も書き込まない
    - --apply: 書き込む前に必ずbackupを取る(--backup-dirで場所を指定可能)
    - idempotent: 新スキーマ側の既存ファイルと内容が同一なら書き込みをスキップする
    - 失敗時の安全性(all-or-nothing): backend/data/registry/ の5ファイルは、
      まず一時staging領域(backend/data/registry.staging_xxxx/)へ全て生成する。
      1件でも生成に失敗した場合、backend/data/registry/ は一切変更されない
      (staging領域を破棄するだけで済む)。全ファイルの生成に成功した場合のみ、
      ディレクトリ単位のrenameでbackend/data/registry/へ反映する
      (_atomic_swap_directory()、Windows環境での安全なdirectory swap方法を
      実行環境で検証した上で採用。詳細はそのdocstring参照)。
      旧データ(backend/buildings.json、backend/space_definitions/*.json等)は
      本スクリプトが一切書き換え・削除しないため、失敗時も常に無傷
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

OLD_BUILDINGS_PATH = BACKEND_DIR / "buildings.json"
OLD_LOCAL_SPACES_PATH = BACKEND_DIR / "local_spaces.json"
OLD_SPACE_DEFINITIONS_DIR = BACKEND_DIR / "space_definitions"

NEW_REGISTRY_DIR = BACKEND_DIR / "data" / "registry"
DEFAULT_BACKUP_ROOT = BACKEND_DIR / "data" / "_migration_backup"


@dataclass
class SpaceDefinitionResolution:
    """tokutei_codeに対応する座標定義ファイルの探索結果。"""

    tokutei_code: str
    status: str  # "exact" | "legacy_unique" | "legacy_ambiguous" | "missing"
    resolved_path: Optional[Path]
    candidates: List[Path]
    canonical_path: Path


@dataclass
class MigrationPlan:
    """変換結果(書き込み前のプレビュー用データ)。"""

    buildings: list
    local_spaces: list
    placements: dict
    nodal_endpoints: list
    nodal_connections: list
    resolutions: List[SpaceDefinitionResolution] = field(default_factory=list)
    space_definition_copies: List[Tuple[Path, Path]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _unresolved_placement_dict() -> dict:
    return {
        "status": "UNRESOLVED",
        "global_origin": None,
        "global_rotation_rad": None,
        "resolution_source": None,
        "anchor_type": None,
        "anchor_connection_ids": [],
        "path_connection_ids": [],
        "rmse_m": None,
        "revision": 0,
        "updated_at": None,
    }


def _resolve_space_definition(tokutei_code: str) -> SpaceDefinitionResolution:
    """tokutei_codeに対応する座標定義ファイルを探す(canonical名優先、
    見つからなければ "{tokutei_code}v*.json" 形式のlegacy命名を探索する)。"""
    canonical_path = OLD_SPACE_DEFINITIONS_DIR / f"{tokutei_code}.json"
    if canonical_path.exists():
        return SpaceDefinitionResolution(
            tokutei_code=tokutei_code, status="exact",
            resolved_path=canonical_path, candidates=[canonical_path],
            canonical_path=canonical_path,
        )

    legacy_candidates = sorted(OLD_SPACE_DEFINITIONS_DIR.glob(f"{tokutei_code}v*.json"))
    if len(legacy_candidates) == 1:
        return SpaceDefinitionResolution(
            tokutei_code=tokutei_code, status="legacy_unique",
            resolved_path=legacy_candidates[0], candidates=legacy_candidates,
            canonical_path=canonical_path,
        )
    if len(legacy_candidates) > 1:
        return SpaceDefinitionResolution(
            tokutei_code=tokutei_code, status="legacy_ambiguous",
            resolved_path=None, candidates=legacy_candidates,
            canonical_path=canonical_path,
        )
    return SpaceDefinitionResolution(
        tokutei_code=tokutei_code, status="missing",
        resolved_path=None, candidates=[], canonical_path=canonical_path,
    )


def build_migration_plan() -> MigrationPlan:
    """旧データを読み込み、新スキーマの中身を組み立てる(書き込みは行わない)。"""
    old_buildings = _read_json(OLD_BUILDINGS_PATH, [])
    old_local_spaces = _read_json(OLD_LOCAL_SPACES_PATH, [])

    warnings: List[str] = []
    resolutions: List[SpaceDefinitionResolution] = []
    space_definition_copies: List[Tuple[Path, Path]] = []

    # 建物はスキーマ変更なし(V2指示書§5.1: 既存を維持する)
    new_buildings = list(old_buildings)

    new_local_spaces = []
    new_placements = {}
    for row in old_local_spaces:
        new_row = dict(row)
        new_row.setdefault("real_estate_id", None)
        new_local_spaces.append(new_row)

        # 【重要】既存座標定義(degree/rad/origin等)は、あくまでLocal Space生成時の
        # provisional coordinate definition。Global座標として解釈せず、
        # 全LocalSpaceをUNRESOLVEDで初期化する。Global placementは、今後
        # Nodal Informationの2点以上の対応とSpatial Resolverによって初めて
        # RESOLVEDになるものであり、既存値から勝手に導出しない
        # (ユーザー指示: 2026-08-28)。
        new_placements[row["space_id"]] = _unresolved_placement_dict()

        tokutei_code = row.get("tokutei_code")
        if not tokutei_code:
            continue

        resolution = _resolve_space_definition(tokutei_code)
        resolutions.append(resolution)

        if resolution.status == "exact":
            continue  # 既にcanonical名で存在する。何もしなくてよい
        elif resolution.status == "legacy_unique":
            space_definition_copies.append((resolution.resolved_path, resolution.canonical_path))
        elif resolution.status == "legacy_ambiguous":
            warnings.append(
                f"space_id={row['space_id']} (tokutei_code={tokutei_code}): "
                f"座標定義ファイルの候補が複数見つかりました"
                f"{[p.name for p in resolution.candidates]}。自動選択しません。"
                f"どちらを正とするか手動で決め、'{resolution.canonical_path.name}' として"
                f"配置してから再実行してください(それまでこの空間のcoordinate_definitionは"
                f"未解決のままになります)。"
            )
        else:  # missing
            warnings.append(
                f"space_id={row['space_id']} (tokutei_code={tokutei_code}): "
                f"座標定義ファイル '{resolution.canonical_path.name}' が見つかりません。"
            )

    return MigrationPlan(
        buildings=new_buildings,
        local_spaces=new_local_spaces,
        placements=new_placements,
        nodal_endpoints=[],
        nodal_connections=[],
        resolutions=resolutions,
        space_definition_copies=space_definition_copies,
        warnings=warnings,
    )


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)  # 途中で例外が起きても既存のpathは無傷のまま


def _backup_existing_data(backup_dir: Path) -> None:
    """--apply の直前に、読み込み元(旧データ)と、既存の新スキーマ(あれば)を
    まるごとbackupする。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    if OLD_BUILDINGS_PATH.exists():
        shutil.copy2(OLD_BUILDINGS_PATH, backup_dir / OLD_BUILDINGS_PATH.name)
    if OLD_LOCAL_SPACES_PATH.exists():
        shutil.copy2(OLD_LOCAL_SPACES_PATH, backup_dir / OLD_LOCAL_SPACES_PATH.name)
    if OLD_SPACE_DEFINITIONS_DIR.exists():
        shutil.copytree(OLD_SPACE_DEFINITIONS_DIR, backup_dir / "space_definitions", dirs_exist_ok=True)
    if NEW_REGISTRY_DIR.exists():
        shutil.copytree(NEW_REGISTRY_DIR, backup_dir / "registry_before_apply", dirs_exist_ok=True)


_REGISTRY_FILENAMES = (
    "buildings.json",
    "local_spaces.json",
    "placements.json",
    "nodal_endpoints.json",
    "nodal_connections.json",
)


def _target_files(plan: MigrationPlan) -> dict:
    """レポート表示・テスト用に、反映後の正式パス基準でファイル一覧を返す。"""
    data_by_name = {
        "buildings.json": plan.buildings,
        "local_spaces.json": plan.local_spaces,
        "placements.json": plan.placements,
        "nodal_endpoints.json": plan.nodal_endpoints,
        "nodal_connections.json": plan.nodal_connections,
    }
    return {NEW_REGISTRY_DIR / name: data for name, data in data_by_name.items()}


def _build_staging_registry(plan: MigrationPlan, staging_dir: Path) -> None:
    """新schemaの5ファイルを、一時staging領域へ全て生成する。

    ここでは backend/data/registry/ には一切触れない。1件でも書き込みに
    失敗した場合は、呼び出し側が staging_dir ごと破棄すれば、
    backend/data/registry/ の既存状態は無傷のまま残る。
    """
    data_by_name = {name: data for name, data in zip(_REGISTRY_FILENAMES, (
        plan.buildings, plan.local_spaces, plan.placements, plan.nodal_endpoints, plan.nodal_connections,
    ))}
    for name, data in data_by_name.items():
        _write_json_atomic(staging_dir / name, data)


def _diff_against_current_registry(staging_dir: Path) -> Tuple[List[Path], List[Path]]:
    """staging領域の内容を、現行の backend/data/registry/ と比較する
    (読み取りのみ。書き込みは行わない)。"""
    written, skipped = [], []
    for name in _REGISTRY_FILENAMES:
        staged_data = json.loads((staging_dir / name).read_text(encoding="utf-8"))
        current_path = NEW_REGISTRY_DIR / name
        if current_path.exists():
            current_data = json.loads(current_path.read_text(encoding="utf-8"))
            if current_data == staged_data:
                skipped.append(current_path)
                continue
        written.append(current_path)
    return written, skipped


def _atomic_swap_directory(staging_dir: Path, target_dir: Path) -> None:
    """target_dir を staging_dir の内容で、ディレクトリ単位で置き換える。

    【実行環境で検証済みの制約】Windows は os.replace()/MoveFileEx に
    よる「既存ディレクトリへの直接replace」を、対象が空ディレクトリで
    あっても常に拒否する(PermissionError [WinError 5])。そのため
    POSIXのrename(2)が持つ「空ディレクトリなら置換可能」という前提には
    頼らず、次の2段階の rename で反映する(いずれも「存在しない名前への
    rename」であり、Windows/POSIX双方で安全に行える):

        1. target_dir が存在するなら、一時名 "{target_dir.name}.old_xxxx"
           へ退避する(存在しない名前へのrenameなので安全)
        2. staging_dir を target_dir の名前へ rename する
           (target_dirは手順1で退避済みなので、この名前は空いている)

    手順2が失敗した場合は、手順1で退避したディレクトリを target_dir の
    名前へ戻すロールバックを試みる(ベストエフォート)。手順2が成功した
    場合、退避しておいた旧ディレクトリは削除する
    (旧データは _backup_existing_data() で既にbackup済みのため安全)。

    このため、本関数の実行後は原則として「旧registry」または
    「新registry」のどちらかが target_dir に完全な形で存在する
    (手順1・2それぞれのrename自体はOS的に不可分な操作であり、
    「一部のファイルだけ差し替わる」という状態にはならない)。
    """
    old_dir: Optional[Path] = None
    if target_dir.exists():
        old_dir = target_dir.parent / f"{target_dir.name}.old_{uuid.uuid4().hex[:8]}"
        os.rename(target_dir, old_dir)

    try:
        os.rename(staging_dir, target_dir)
    except Exception:
        if old_dir is not None and not target_dir.exists():
            # 反映に失敗したので、退避しておいた旧registryを復元する(ロールバック)
            os.rename(old_dir, target_dir)
        raise
    else:
        if old_dir is not None:
            shutil.rmtree(old_dir, ignore_errors=True)


def apply_migration(plan: MigrationPlan, backup_dir: Path) -> dict:
    """実際に新スキーマファイルを書き込む(all-or-nothing)。

    1. backup(要件: migrationによる変更より必ず先に完了させる)
    2. 新schema一式を一時staging領域に生成する(1件でも失敗したら
       staging領域を破棄するだけで、backend/data/registry/ は無傷のまま)
    3. 全て生成できた場合のみ、ディレクトリ単位のrenameで正式反映する
       (内容が現行registryと完全に同一なら、反映自体を省略する)
    4. legacy命名の座標定義ファイルを、canonical名として"コピー"する
       (この部分は元々1ファイル単位の追加専用の操作であり、既存ファイルを
       上書きすることは無い。方針は変更していない)
    """
    _backup_existing_data(backup_dir)

    staging_dir = NEW_REGISTRY_DIR.parent / f"registry.staging_{uuid.uuid4().hex[:8]}"
    try:
        _build_staging_registry(plan, staging_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    written, skipped = _diff_against_current_registry(staging_dir)

    if written:
        _atomic_swap_directory(staging_dir, NEW_REGISTRY_DIR)
    else:
        # 内容が現行registryと完全に同一 -> 反映不要。stagingを破棄するだけ
        shutil.rmtree(staging_dir, ignore_errors=True)

    copied, copy_skipped = [], []
    for source, target in plan.space_definition_copies:
        if target.exists():
            # 既にcanonicalファイルが存在する(2回目以降の実行) -> 何もしない
            copy_skipped.append(target)
            continue
        shutil.copy2(source, target)  # 元ファイル(source)は削除・変更しない
        copied.append(target)

    return {"written": written, "skipped": skipped, "copied": copied, "copy_skipped": copy_skipped}


def print_report(
    plan: MigrationPlan,
    mode: str,
    backup_dir: Optional[Path] = None,
    apply_result: Optional[dict] = None,
) -> None:
    print("=" * 70)
    print(f"migrate_gui_v1_to_v2.py -- mode: {mode}")
    print("=" * 70)

    print("\n[1] migration対象ファイル")
    print("  読み込み(変更しない):")
    print(f"    - {OLD_BUILDINGS_PATH}")
    print(f"    - {OLD_LOCAL_SPACES_PATH}")
    print(f"    - {OLD_SPACE_DEFINITIONS_DIR}/*.json")
    print("  書き込み(--apply時のみ):")
    for path in _target_files(plan):
        print(f"    - {path}")
    if plan.space_definition_copies:
        print("  コピー作成(--apply時のみ、元ファイルは維持):")
        for source, target in plan.space_definition_copies:
            print(f"    - {source.name} -> {target}")

    print("\n[legacy filename resolution結果]")
    if plan.resolutions:
        for r in plan.resolutions:
            print(f"  tokutei_code={r.tokutei_code}: status={r.status}"
                  + (f", resolved={r.resolved_path.name}" if r.resolved_path else "")
                  + (f", candidates={[p.name for p in r.candidates]}" if r.candidates else ""))
    else:
        print("  (対象なし)")

    print("\n[2] before/afterスキーマ(LOCAL_SPACEの例)")
    if plan.local_spaces:
        sample = plan.local_spaces[0]
        before = {k: v for k, v in sample.items() if k != "real_estate_id"}
        print(f"  before(local_spaces.json): {json.dumps(before, ensure_ascii=False)}")
        print(f"  after (local_spaces.json): {json.dumps(sample, ensure_ascii=False)}")
        print(
            "  after (placements.json、新設・全てUNRESOLVED): "
            + json.dumps({sample["space_id"]: plan.placements[sample["space_id"]]}, ensure_ascii=False)
        )
    else:
        print("  (local_spaces.json が空のため、変換例はありません)")

    print("\n[3] 変換件数")
    print(f"  建物: {len(plan.buildings)} 件")
    print(f"  ローカル空間: {len(plan.local_spaces)} 件")
    print(f"  座標定義ファイルのcanonical名コピー: {len(plan.space_definition_copies)} 件")

    if plan.warnings:
        print("\n[警告]")
        for w in plan.warnings:
            print(f"  - {w}")

    if mode == "dry-run":
        print("\n[4] backup先")
        print(f"  (--dry-run のため未実行。--apply 時は既定で {DEFAULT_BACKUP_ROOT}/{{timestamp}}/ に作成されます)")
        print("\n[5] rollback方法")
        print("  --apply を実行していないため、rollbackは不要です(旧データは変更されていません)。")
    else:
        print("\n[4] backup先")
        print(f"  {backup_dir}")
        print("\n[5] 書き込み結果")
        for p in apply_result["written"]:
            print(f"  書き込み: {p}")
        for p in apply_result["skipped"]:
            print(f"  スキップ(内容が同一のため): {p}")
        for p in apply_result["copied"]:
            print(f"  コピー作成: {p}")
        for p in apply_result["copy_skipped"]:
            print(f"  コピースキップ(既に存在するため): {p}")
        print("\n[6] rollback方法")
        print("  1. 旧データ(backend/buildings.json 等)は本スクリプトが変更・削除しないため、")
        print("     通常はrollback操作自体が不要です。")
        print(f"  2. 新schemaで新設したcanonical座標定義ファイル({', '.join(p.name for _, p in plan.space_definition_copies) or 'なし'})")
        print("     を取り消したい場合は、そのファイルを削除してください(元のlegacyファイルは無傷のままです)。")
        print(f"  3. 新スキーマ側全体を元に戻したい場合は、{NEW_REGISTRY_DIR} を削除するか、")
        print(f"     {backup_dir / 'registry_before_apply'} の内容で置き換えてください")
        print("     (このバックアップは、今回の--apply実行前の状態です)。")
    print("=" * 70)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run", action="store_true", help="変換結果を表示するだけで、何も書き込まない(既定)"
    )
    mode_group.add_argument(
        "--apply", action="store_true", help="実際に新スキーマファイルを書き込む(事前に必ずbackupを取る)"
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help=f"backup先ディレクトリ(既定: {DEFAULT_BACKUP_ROOT}/{{timestamp}}/)",
    )
    args = parser.parse_args(argv)

    plan = build_migration_plan()

    if not args.apply:
        print_report(plan, mode="dry-run")
        return 0

    backup_dir = args.backup_dir or (DEFAULT_BACKUP_ROOT / time.strftime("%Y%m%dT%H%M%S"))
    result = apply_migration(plan, backup_dir)
    print_report(plan, mode="apply", backup_dir=backup_dir, apply_result=result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
