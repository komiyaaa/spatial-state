#!/usr/bin/env python
"""
scripts/migrate_registration_results_archive.py

既存のVGICP精密位置合わせ結果(backend/data/rough_registered/・
precise_registered/・scan_json/・vgicp_logs/、いずれもrun_id概念導入前に
生成されたもの)を、backend/data/registration_results/{space_id}/
{run_id}_{source_stem}/ へ非破壊的にコピー移行する(ER図反映と同じ
--dry-run既定・--apply・事前backupの規約)。

【正直に「記録されていなかった」ことを保持する(ユーザー指示: 2026-09-02)】
当時のvgicp_logsにはrotation/translation(変換行列)が一切保存されておらず、
元のSourceファイル名(合成filenameより前の、ユーザーが選択した実際のファイル名)
も記録されていない。これらを事後推定・復元することはしない。
registration_result.jsonでは rotation: null, translation: null,
source_filename: null とし、実際にログへ記録されていた値
(fitness_score・voxel_size・タイムスタンプ)のみを移行する。
"migrated_from_legacy": true を付け、新規実行分(run_vgicp()がbest_matrixを
その場で保存する)と区別できるようにする。

対象の判定: rough_registered/{space_id}/{filename} と
precise_registered/{space_id}/{filename} が両方存在するペアを1件として扱う
(scan_json・vgicp_logsは存在すれば付加情報として使うが、無くても移行対象と
する)。

使い方:
    python scripts/migrate_registration_results_archive.py            # --dry-run と同じ(既定)
    python scripts/migrate_registration_results_archive.py --dry-run  # 計画を表示するだけ、書き込みなし
    python scripts/migrate_registration_results_archive.py --apply    # 実際にコピーする(事前に必ずbackupを取る)
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
DATA_DIR = BACKEND_DIR / "data"

ROUGH_DIR = DATA_DIR / "rough_registered"
PRECISE_DIR = DATA_DIR / "precise_registered"
SCAN_JSON_DIR = DATA_DIR / "scan_json"
VGICP_LOG_DIR = DATA_DIR / "vgicp_logs"
REGISTRATION_RESULTS_DIR = DATA_DIR / "registration_results"

DEFAULT_BACKUP_ROOT = DATA_DIR / "_migration_backup"


@dataclass
class LegacyRunPlan:
    space_id: str
    filename: str
    rough_path: Path
    precise_path: Path
    scan_json_path: Optional[Path]
    vgicp_log_path: Optional[Path]
    run_id: str
    source_stem: str
    fitness_score: Optional[float]
    voxel_size: Optional[float]
    generated_at: Optional[str]
    run_dir: Path
    notes: List[str] = field(default_factory=list)


def _stem(filename: str) -> str:
    return Path(filename).stem


def _load_vgicp_log(space_id: str, stem: str) -> Optional[dict]:
    path = VGICP_LOG_DIR / f"{space_id}_{stem}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def build_migration_plan() -> List[LegacyRunPlan]:
    """既存データを読み込み、計画を組み立てる(書き込みは行わない)。"""
    plans: List[LegacyRunPlan] = []
    if not ROUGH_DIR.exists():
        return plans

    for space_dir in sorted(ROUGH_DIR.iterdir()):
        if not space_dir.is_dir():
            continue
        space_id = space_dir.name

        for rough_path in sorted(space_dir.iterdir()):
            if not rough_path.is_file():
                continue
            filename = rough_path.name
            stem = _stem(filename)

            precise_path = PRECISE_DIR / space_id / filename
            if not precise_path.exists():
                # precise側が無い(VGICP失敗等)ものは、Spatial Stateへ投入した
                # precise点群そのものが存在しないため移行対象外とする。
                continue

            scan_json_path = SCAN_JSON_DIR / space_id / f"{stem}.json"
            scan_json_path = scan_json_path if scan_json_path.exists() else None

            vgicp_log_path = VGICP_LOG_DIR / f"{space_id}_{stem}.json"
            vgicp_log_path = vgicp_log_path if vgicp_log_path.exists() else None
            vgicp_log = _load_vgicp_log(space_id, stem)

            fitness_score = vgicp_log.get("best_fitness_score") if vgicp_log else None
            voxel_size = vgicp_log.get("best_vsize") if vgicp_log else None
            generated_at = vgicp_log.get("generated_at") if vgicp_log else None

            # run_idは、現在時刻ではなく当時の記録(vgicp_logのgenerated_at)を
            # 元に決定論的に作る(再実行しても同じrun_idになり、idempotentにする)。
            # generated_atが無い場合はファイルのstem(タイムスタンプ由来の合成名)を使う。
            if generated_at:
                run_id_base = generated_at.replace("-", "").replace(":", "").replace("T", "T")
            else:
                run_id_base = stem
            run_id = f"{run_id_base}_migrated"

            source_stem = stem  # 元のSourceファイル名は記録されていないため、合成名のstemをそのまま使う
            run_dir = REGISTRATION_RESULTS_DIR / space_id / f"{run_id}_{source_stem}"

            notes = []
            if scan_json_path is None:
                notes.append("scan.jsonは見つからなかったため含めない")
            if vgicp_log is None:
                notes.append("vgicp_logsが見つからないためfitness_score/voxel_sizeはnull")

            plans.append(LegacyRunPlan(
                space_id=space_id, filename=filename, rough_path=rough_path,
                precise_path=precise_path, scan_json_path=scan_json_path,
                vgicp_log_path=vgicp_log_path, run_id=run_id, source_stem=source_stem,
                fitness_score=fitness_score, voxel_size=voxel_size, generated_at=generated_at,
                run_dir=run_dir, notes=notes,
            ))

    return plans


def _backup_existing_data(backup_dir: Path, plans: List[LegacyRunPlan]) -> None:
    """--apply の直前に、読み込み元(旧データ)をまるごとbackupする。"""
    space_ids = sorted({p.space_id for p in plans})
    for space_id in space_ids:
        for src_root, name in ((ROUGH_DIR, "rough_registered"), (PRECISE_DIR, "precise_registered"),
                                (SCAN_JSON_DIR, "scan_json")):
            src = src_root / space_id
            if src.exists():
                shutil.copytree(src, backup_dir / name / space_id, dirs_exist_ok=True)
    if VGICP_LOG_DIR.exists():
        (backup_dir / "vgicp_logs").mkdir(parents=True, exist_ok=True)
        for space_id in space_ids:
            for f in VGICP_LOG_DIR.glob(f"{space_id}_*"):
                shutil.copy2(f, backup_dir / "vgicp_logs" / f.name)


def apply_migration(plans: List[LegacyRunPlan], backup_dir: Path) -> dict:
    """実際にコピーする。既存ファイル(rough_registered/precise_registered/
    scan_json/vgicp_logs)は一切削除・変更しない。registration_results/側で
    既にrun_dirが存在する場合はスキップする(idempotent)。"""
    _backup_existing_data(backup_dir, plans)

    created, skipped = [], []
    for plan in plans:
        if plan.run_dir.exists():
            skipped.append(plan.run_dir)
            continue

        plan.run_dir.mkdir(parents=True, exist_ok=True)
        rough_dest = plan.run_dir / "rough_registered.ply"
        precise_dest = plan.run_dir / "precise_registered.ply"
        shutil.copy2(plan.rough_path, rough_dest)
        shutil.copy2(plan.precise_path, precise_dest)

        scan_dest = None
        if plan.scan_json_path is not None:
            scan_dest = plan.run_dir / "scan.json"
            shutil.copy2(plan.scan_json_path, scan_dest)

        result_json_path = plan.run_dir / "registration_result.json"
        result_json_path.write_text(json.dumps({
            "run_id": plan.run_id,
            "space_id": plan.space_id,
            # 【正直に「記録されていなかった」ことを保持する】当時は元Sourceファイル名を
            # 記録していなかったため、事後推定はせずnullのままにする。
            "source_filename": None,
            "uploaded_filename": plan.filename,
            "rough_registered_path": str(rough_dest),
            "precise_registered_path": str(precise_dest),
            "scan_json_path": str(scan_dest) if scan_dest else None,
            "fitness_score": plan.fitness_score,
            "voxel_size": plan.voxel_size,
            # 当時rotation/translationはログに残されていないため、事後推定せずnull。
            "rotation": None,
            "translation": None,
            "generated_at": plan.generated_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "migrated_from_legacy": True,
            "origin": {
                "rough_registered_path": str(plan.rough_path),
                "precise_registered_path": str(plan.precise_path),
                "scan_json_path": str(plan.scan_json_path) if plan.scan_json_path else None,
                "vgicp_log_path": str(plan.vgicp_log_path) if plan.vgicp_log_path else None,
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        created.append(plan.run_dir)

    return {"created": created, "skipped": skipped}


def print_report(plans: List[LegacyRunPlan], mode: str, backup_dir: Optional[Path] = None,
                  apply_result: Optional[dict] = None) -> None:
    print("=" * 70)
    print(f"migrate_registration_results_archive.py -- mode: {mode}")
    print("=" * 70)
    print(f"\n対象件数: {len(plans)}")
    by_space: dict = {}
    for p in plans:
        by_space.setdefault(p.space_id, []).append(p)
    for space_id, items in by_space.items():
        print(f"\n[{space_id}] {len(items)}件")
        for p in items:
            print(f"  - {p.run_dir.name} (fitness={p.fitness_score}, voxel_size={p.voxel_size})")
            for note in p.notes:
                print(f"      ({note})")

    if mode == "dry-run":
        print(f"\n--dry-run のため未実行。--apply 時は既定で {DEFAULT_BACKUP_ROOT}/{{timestamp}}_registration_results_archive/ にbackupを作成します。")
    else:
        print(f"\nbackup先: {backup_dir}")
        print(f"作成: {len(apply_result['created'])}件")
        print(f"スキップ(既に存在): {len(apply_result['skipped'])}件")
        print("\n元ファイル(rough_registered/precise_registered/scan_json/vgicp_logs)は一切削除・変更していません。")
    print("=" * 70)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="計画を表示するだけで、何も書き込まない(既定)")
    mode_group.add_argument("--apply", action="store_true", help="実際にコピーする(事前に必ずbackupを取る)")
    parser.add_argument("--backup-dir", type=Path, default=None,
                         help=f"backup先ディレクトリ(既定: {DEFAULT_BACKUP_ROOT}/{{timestamp}}_registration_results_archive/)")
    args = parser.parse_args(argv)

    plans = build_migration_plan()

    if not args.apply:
        print_report(plans, mode="dry-run")
        return 0

    backup_dir = args.backup_dir or (DEFAULT_BACKUP_ROOT / f"{time.strftime('%Y%m%dT%H%M%S')}_registration_results_archive")
    result = apply_migration(plans, backup_dir)
    print_report(plans, mode="apply", backup_dir=backup_dir, apply_result=result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
