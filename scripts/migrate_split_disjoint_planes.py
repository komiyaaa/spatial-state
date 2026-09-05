#!/usr/bin/env python
"""
scripts/migrate_split_disjoint_planes.py

既存の backend/data/planes/{space_id}.json (RANSACのsegment_plane自体が
空間連続性を条件にしないため、空間的に非連続なcoplanar clusterが1つの
Planeにまとまっている可能性があるもの)を、plane_segmentation.pyの
split_plane_by_connectivity()で再分割し、非破壊的に上書きする
(ER図反映・registration_results archiveと同じ --dry-run既定・--apply・
事前backupの規約)。

【重要: Base Map点群自体は一切変更しない】
このスクリプトが変更するのは backend/data/planes/{space_id}.json のみ。
backend/base_maps/{space_id}.las は読み込む(point_indicesの座標復元のため)
だけで、一切書き込まない。

【重要: 分割後の子Planeのconfirmed_labelは親から継承する】
G002/G003は既に人間によるconfirmed_labelレビューが入っている可能性がある。
分割のたびに全て未レビュー状態(suggested_label)へ戻すと、それまでの
レビュー作業が失われるため、split_plane_by_connectivity()へ
inherited_confirmed_label=親のconfirmed_label を渡す(検出時の
「各子が自分のsuggested_labelを使う」という挙動とは意図的に使い分ける)。

【今回の変更範囲】
backend/data/planes/{space_id}.json の書き換えのみ。
backend/data/voxel_labels/・backend/data/structural_label_fitness_history/
(Structural Labelのボクセル反映)は、このスクリプトでは再生成しない
(source_plane_idsが分割前のplane_idを参照したまま不整合になるが、
再生成の実行タイミングは別途ユーザーに確認してから行う)。
Spatial State(backend/data/tracker_state/等)には一切触れない。

使い方:
    python scripts/migrate_split_disjoint_planes.py            # --dry-run と同じ(既定)
    python scripts/migrate_split_disjoint_planes.py --dry-run  # 計画を表示するだけ、書き込みなし
    python scripts/migrate_split_disjoint_planes.py --apply    # 実際に上書きする(事前に必ずbackupを取る)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"

PLANES_DIR = DATA_DIR / "planes"
BASE_MAPS_DIR = REPO_ROOT / "base_maps"

DEFAULT_BACKUP_ROOT = DATA_DIR / "_migration_backup"

_ALREADY_MIGRATED_PATTERN = re.compile(r"^P\d{3}[a-z]+$")

sys.path.insert(0, str(BACKEND_DIR))


@dataclass
class SpaceSplitPlan:
    space_id: str
    already_migrated: bool
    point_count: Optional[int] = None
    original_planes: List = field(default_factory=list)  # list[Plane]
    new_planes: List = field(default_factory=list)  # list[Plane]
    split_summary: List[dict] = field(default_factory=list)  # 分割が起きたPlaneのみ
    notes: List[str] = field(default_factory=list)


def _load_base_map_points(space_id: str):
    """segment_planes()・build_voxel_labels()と同じ読み込み経路
    (server.py _import_pcd 相当)でBase Map点群を再現する。"""
    import numpy as np
    import open3d as o3d

    las_path = BASE_MAPS_DIR / f"{space_id}.las"
    if not las_path.exists():
        return None, f"base_maps/{space_id}.las が見つかりません"

    import laspy as lp
    point_cloud = lp.read(str(las_path))
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(
        np.vstack((point_cloud.x, point_cloud.y, point_cloud.z)).transpose()
    )
    points = np.asarray(pcd.points)
    return points, None


def build_migration_plan() -> List[SpaceSplitPlan]:
    """既存のplanes.jsonを読み込み、分割計画を組み立てる(書き込みは行わない)。"""
    from plane_segmentation import PlaneSegmentationConfig, split_plane_by_connectivity
    from repositories.plane_repository import PlaneRepository

    plane_repo = PlaneRepository(PLANES_DIR)
    config = PlaneSegmentationConfig()

    plans: List[SpaceSplitPlan] = []
    if not PLANES_DIR.exists():
        return plans

    for path in sorted(PLANES_DIR.glob("*.json")):
        space_id = path.stem
        original_planes = plane_repo.load_planes(space_id)
        if not original_planes:
            continue

        if any(_ALREADY_MIGRATED_PATTERN.match(p.plane_id) for p in original_planes):
            plans.append(SpaceSplitPlan(space_id=space_id, already_migrated=True,
                                         notes=["既にサフィックス付きplane_idが存在するためスキップ(移行済み)"]))
            continue

        points, err = _load_base_map_points(space_id)
        if points is None:
            plans.append(SpaceSplitPlan(space_id=space_id, already_migrated=False,
                                         original_planes=original_planes, notes=[err]))
            continue

        plan = SpaceSplitPlan(space_id=space_id, already_migrated=False,
                               point_count=len(points), original_planes=original_planes)
        new_planes = []
        for p in original_planes:
            children = split_plane_by_connectivity(p, points, config, inherited_confirmed_label=p.confirmed_label)
            new_planes.extend(children)
            if len(children) > 1:
                plan.split_summary.append({
                    "parent_plane_id": p.plane_id,
                    "parent_point_count": p.point_count,
                    "parent_confirmed_label": p.confirmed_label.value,
                    "children": [
                        {"plane_id": c.plane_id, "point_count": c.point_count,
                         "suggested_label": c.suggested_label.value, "confirmed_label": c.confirmed_label.value}
                        for c in children
                    ],
                })
        plan.new_planes = new_planes
        plans.append(plan)

    return plans


def _backup_existing_data(backup_dir: Path, plans: List[SpaceSplitPlan]) -> None:
    """--apply の直前に、書き換え対象のplanes.jsonをまるごとbackupする。"""
    (backup_dir / "planes").mkdir(parents=True, exist_ok=True)
    for plan in plans:
        if plan.already_migrated:
            continue
        src = PLANES_DIR / f"{plan.space_id}.json"
        if src.exists():
            shutil.copy2(src, backup_dir / "planes" / src.name)


def apply_migration(plans: List[SpaceSplitPlan], backup_dir: Path) -> dict:
    """実際にplanes.jsonを上書きする。Base Map(.las)・voxel_labels・
    Spatial Stateには一切触れない。"""
    from repositories.plane_repository import PlaneRepository

    target_plans = [p for p in plans if not p.already_migrated and p.point_count is not None]
    _backup_existing_data(backup_dir, target_plans)

    plane_repo = PlaneRepository(PLANES_DIR)
    updated = []
    for plan in target_plans:
        if not plan.split_summary:
            # 分割対象のPlaneが1件も無かった場合は書き込み不要(無変更のまま)
            continue
        plane_repo.save_planes(plan.space_id, plan.new_planes)
        updated.append(plan.space_id)

    return {"updated": updated}


def print_report(plans: List[SpaceSplitPlan], mode: str, backup_dir: Optional[Path] = None,
                  apply_result: Optional[dict] = None) -> None:
    print("=" * 70)
    print(f"migrate_split_disjoint_planes.py -- mode: {mode}")
    print("=" * 70)

    for plan in plans:
        print(f"\n[{plan.space_id}]")
        if plan.already_migrated:
            print("  スキップ(移行済み)")
            continue
        if plan.point_count is None:
            print(f"  スキップ({'; '.join(plan.notes)})")
            continue
        print(f"  Base Map点数: {plan.point_count}")
        print(f"  分割前Plane数: {len(plan.original_planes)} / 分割後Plane数: {len(plan.new_planes)}")
        if not plan.split_summary:
            print("  分割が必要なPlaneは無し(全Plane、空間的に連続)")
            continue
        for entry in plan.split_summary:
            print(f"  - {entry['parent_plane_id']} ({entry['parent_point_count']}点, "
                  f"confirmed={entry['parent_confirmed_label']}) -> {len(entry['children'])}件に分割:")
            for c in entry["children"]:
                print(f"      {c['plane_id']}: {c['point_count']}点, "
                      f"suggested={c['suggested_label']}, confirmed(継承)={c['confirmed_label']}")

    if mode == "dry-run":
        print(f"\n--dry-run のため未実行。--apply 時は既定で "
              f"{DEFAULT_BACKUP_ROOT}/{{timestamp}}_split_disjoint_planes/ にbackupを作成します。")
    else:
        print(f"\nbackup先: {backup_dir}")
        print(f"更新したspace_id: {apply_result['updated']}")
        print("\nBase Map(.las)・voxel_labels・structural_label_fitness_history・Spatial Stateは"
              "一切変更していません(必要な場合は別途、既存のbuild_voxel_labels()再実行をご確認ください)。")
    print("=" * 70)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="計画を表示するだけで、何も書き込まない(既定)")
    mode_group.add_argument("--apply", action="store_true", help="実際に上書きする(事前に必ずbackupを取る)")
    parser.add_argument("--backup-dir", type=Path, default=None,
                         help=f"backup先ディレクトリ(既定: {DEFAULT_BACKUP_ROOT}/{{timestamp}}_split_disjoint_planes/)")
    args = parser.parse_args(argv)

    plans = build_migration_plan()

    if not args.apply:
        print_report(plans, mode="dry-run")
        return 0

    backup_dir = args.backup_dir or (DEFAULT_BACKUP_ROOT / f"{time.strftime('%Y%m%dT%H%M%S')}_split_disjoint_planes")
    result = apply_migration(plans, backup_dir)
    print_report(plans, mode="apply", backup_dir=backup_dir, apply_result=result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
