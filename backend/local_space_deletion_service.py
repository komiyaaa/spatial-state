"""
backend/local_space_deletion_service.py

既存のLocal Spaceを、space_id・tokutei_codeを固定したまま「active systemから
除外する」ための削除機能のオーケストレーション層(2026-09-03追加)。

【設計方針】
- 単純な破棄ではなく、実験記録性のあるデータ(CoordinateDefinition・Base Map・
  Plane・Structural Label・Spatial State・Registration Result等)は
  `backend/data/_archived_local_spaces/{timestamp}_{space_id}/` へ
  「コピー → 完全性確認 → active側から削除」の順で退避する
  (fail-closed: アーカイブの完全性が確認できるまで、元データは一切消さない)。
- 表示専用の再生成可能キャッシュ(spatial_voxel_cache・voxel_color_cache・
  spatial_resolution_results)はアーカイブせず、既存のinvalidateで破棄する。
- Nodal Endpoint/Connectionはソフト参照(外部キー制約なし)のため、削除対象
  space_idのLOCAL Endpointと、それを参照するConnectionを、アーカイブ
  manifestへスナップショットしてから削除する(相手側space_idのEndpoint自体は
  触らない)。
- LocalSpace registry行(space_idの実体)は、他の全工程が完了した後に
  最後に削除する(途中で失敗しても「中途半端に消えたspace」を作らないため)。
- `build_deletion_plan()`はdry-run・実削除どちらからも呼ばれる唯一の
  「何が対象か」の計算ロジック(preview/executionの乖離を防ぐ)。

Spatial State・Structural Label・VGICP・Registration Resultの算式・保存形式
自体には一切触れない(このモジュールはファイルの移動・削除のみを行う)。
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class DeletionArchiveError(RuntimeError):
    """アーカイブのコピー・完全性確認に失敗した(fail-closed: この時点では
    active側のデータは一切変更されていない)。"""


@dataclass
class DeletionContext:
    """削除処理に必要な全repository・ディレクトリを束ねたもの(DI用、
    server.pyの既存グローバルからそのまま組み立てる想定)。"""

    local_space_repo: object
    nodal_endpoint_repo: object
    nodal_connection_repo: object
    spatial_resolution_result_repo: object
    spatial_voxel_cache_repo: object
    voxel_color_cache_repo: object

    space_def_dir: Path
    base_maps_dir: Path
    planes_dir: Path
    voxel_labels_dir: Path
    structural_label_history_dir: Path
    tracker_state_dir: Path
    registration_results_dir: Path
    rough_dir: Path
    precise_dir: Path
    scan_json_dir: Path
    vgicp_log_dir: Path
    archive_root: Path


@dataclass
class ArchiveItem:
    category: str  # "coordinate_definition" / "base_map" / "plane" / ... (legacyは末尾に"_legacy")
    kind: str  # "file" | "dir" | "glob"
    source_paths: List[Path] = field(default_factory=list)
    exists: bool = False
    file_count: int = 0
    total_bytes: int = 0
    legacy: bool = False

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "kind": self.kind,
            "source_paths": [str(p) for p in self.source_paths],
            "exists": self.exists,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "legacy": self.legacy,
        }


@dataclass
class CacheInvalidationItem:
    category: str
    description: str
    matched_file_count: int

    def to_dict(self) -> dict:
        return {"category": self.category, "description": self.description,
                "matched_file_count": self.matched_file_count}


@dataclass
class SkippedLegacyItem:
    category: str
    tokutei_code: str
    reason: str

    def to_dict(self) -> dict:
        return {"category": self.category, "tokutei_code": self.tokutei_code, "reason": self.reason}


@dataclass
class DeletionPlan:
    space_id: str
    tokutei_code: str
    building_id: str
    local_space_row: dict
    archive_items: List[ArchiveItem]
    cache_invalidations: List[CacheInvalidationItem]
    nodal_endpoints: List[dict]
    nodal_connections: List[dict]
    legacy_keys_skipped: List[SkippedLegacyItem]
    spatial_resolution_result_building_exists: bool

    def to_dict(self) -> dict:
        return {
            "space_id": self.space_id,
            "tokutei_code": self.tokutei_code,
            "building_id": self.building_id,
            "local_space_row": self.local_space_row,
            "archive_items": [i.to_dict() for i in self.archive_items if i.exists],
            "cache_invalidations": [i.to_dict() for i in self.cache_invalidations],
            "nodal_endpoints_affected": self.nodal_endpoints,
            "nodal_connections_affected": self.nodal_connections,
            "legacy_keys_skipped": [i.to_dict() for i in self.legacy_keys_skipped],
            "spatial_resolution_result_building_cache_exists": self.spatial_resolution_result_building_exists,
        }


@dataclass
class DeletionResult:
    space_id: str
    archive_dir: Path
    archived_categories: List[str]
    removed_from_active: List[str]
    cache_invalidated: List[str]
    nodal_endpoints_deleted: List[str]
    nodal_connections_deleted: List[str]
    spatial_resolution_result_invalidated: bool
    local_space_deleted: bool

    def to_dict(self) -> dict:
        return {
            "space_id": self.space_id,
            "archive_dir": str(self.archive_dir),
            "archived_categories": self.archived_categories,
            "removed_from_active": self.removed_from_active,
            "cache_invalidated": self.cache_invalidated,
            "nodal_endpoints_deleted": self.nodal_endpoints_deleted,
            "nodal_connections_deleted": self.nodal_connections_deleted,
            "spatial_resolution_result_invalidated": self.spatial_resolution_result_invalidated,
            "local_space_deleted": self.local_space_deleted,
        }


def _load_base_map_manifest(base_maps_dir: Path) -> list:
    manifest_path = base_maps_dir / "manifest.json"
    if not manifest_path.exists():
        return []
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _manifest_entry(manifest: list, entry_id: str) -> Optional[dict]:
    for entry in manifest:
        if entry.get("id") == entry_id:
            return entry
    return None


def _tokutei_code_used_elsewhere(local_space_repo, tokutei_code: str, space_id: str) -> bool:
    """tokutei_codeはBuilding非依存で一意ではないため、削除対象以外の
    Local Spaceが同じtokutei_codeを使っていないか確認する(legacy
    tokutei_code-keyedファイルを安全に削除できるかの判定用)。"""
    return any(
        s.tokutei_code == tokutei_code and s.space_id != space_id
        for s in local_space_repo.list_all()
    )


def _file_archive_item(category: str, path: Path, legacy: bool = False) -> ArchiveItem:
    if not path.exists():
        return ArchiveItem(category=category, kind="file", source_paths=[path], exists=False, legacy=legacy)
    return ArchiveItem(
        category=category, kind="file", source_paths=[path], exists=True,
        file_count=1, total_bytes=path.stat().st_size, legacy=legacy,
    )


def _dir_archive_item(category: str, path: Path) -> ArchiveItem:
    if not path.exists() or not path.is_dir():
        return ArchiveItem(category=category, kind="dir", source_paths=[path], exists=False)
    files = [p for p in path.rglob("*") if p.is_file()]
    return ArchiveItem(
        category=category, kind="dir", source_paths=[path], exists=True,
        file_count=len(files), total_bytes=sum(p.stat().st_size for p in files),
    )


def _glob_archive_item(category: str, directory: Path, pattern: str) -> ArchiveItem:
    matches = sorted(directory.glob(pattern)) if directory.exists() else []
    matches = [p for p in matches if p.is_file()]
    return ArchiveItem(
        category=category, kind="glob", source_paths=matches, exists=len(matches) > 0,
        file_count=len(matches), total_bytes=sum(p.stat().st_size for p in matches),
    )


def build_deletion_plan(ctx: DeletionContext, space_id: str) -> DeletionPlan:
    """削除対象の全カテゴリを読み取り専用で計算する(dry-run・実削除共通の
    唯一のロジック)。この関数自体はファイルを一切変更しない。"""
    space = ctx.local_space_repo.get(space_id)
    if space is None:
        raise ValueError(f"space_id '{space_id}' が見つかりません。")

    tokutei_code = space.tokutei_code
    building_id = space.building_id
    legacy_eligible = not _tokutei_code_used_elsewhere(ctx.local_space_repo, tokutei_code, space_id)

    local_space_row = {
        "space_id": space.space_id, "building_id": space.building_id,
        "tokutei_code": space.tokutei_code, "floor": space.floor,
        "zoom_level": space.zoom_level, "registered_at": space.registered_at,
        "real_estate_id": space.real_estate_id,
    }

    archive_items: List[ArchiveItem] = []
    legacy_keys_skipped: List[SkippedLegacyItem] = []

    # --- CoordinateDefinition ---
    archive_items.append(_file_archive_item("coordinate_definition", ctx.space_def_dir / f"{space_id}.json"))
    legacy_space_def = ctx.space_def_dir / f"{tokutei_code}.json"
    if legacy_eligible:
        item = _file_archive_item("coordinate_definition_legacy", legacy_space_def, legacy=True)
        if item.exists:
            archive_items.append(item)
    elif legacy_space_def.exists():
        legacy_keys_skipped.append(SkippedLegacyItem(
            category="coordinate_definition_legacy", tokutei_code=tokutei_code,
            reason=f"他のLocal Spaceがtokutei_code '{tokutei_code}' を使用中のためスキップ",
        ))

    # --- Base Map + manifest ---
    manifest = _load_base_map_manifest(ctx.base_maps_dir)
    own_entry = _manifest_entry(manifest, space_id)
    if own_entry:
        archive_items.append(_file_archive_item("base_map", ctx.base_maps_dir / own_entry["file"]))
    legacy_entry = _manifest_entry(manifest, tokutei_code)
    if legacy_entry:
        if legacy_eligible:
            archive_items.append(_file_archive_item(
                "base_map_legacy", ctx.base_maps_dir / legacy_entry["file"], legacy=True,
            ))
        else:
            legacy_keys_skipped.append(SkippedLegacyItem(
                category="base_map_legacy", tokutei_code=tokutei_code,
                reason=f"他のLocal Spaceがtokutei_code '{tokutei_code}' を使用中のためスキップ",
            ))

    # --- Plane / Structural Label ---
    archive_items.append(_file_archive_item("plane", ctx.planes_dir / f"{space_id}.json"))
    archive_items.append(_file_archive_item("voxel_label", ctx.voxel_labels_dir / f"{space_id}.json"))
    archive_items.append(_file_archive_item(
        "structural_label_fitness_history", ctx.structural_label_history_dir / f"{space_id}.json",
    ))

    # --- Spatial State ---
    archive_items.append(_file_archive_item("spatial_state", ctx.tracker_state_dir / f"{space_id}.json"))

    # --- Registration Result / 実験記録一式 ---
    archive_items.append(_dir_archive_item("registration_results", ctx.registration_results_dir / space_id))
    archive_items.append(_dir_archive_item("rough_registered", ctx.rough_dir / space_id))
    archive_items.append(_dir_archive_item("precise_registered", ctx.precise_dir / space_id))
    archive_items.append(_dir_archive_item("scan_json", ctx.scan_json_dir / space_id))
    archive_items.append(_glob_archive_item("vgicp_logs", ctx.vgicp_log_dir, f"{space_id}_*"))

    # --- 表示用キャッシュ(アーカイブ不要、破棄のみ) ---
    spatial_voxel_cache_files = list(ctx.spatial_voxel_cache_repo.base_dir.glob(f"{space_id}__z*")) \
        if ctx.spatial_voxel_cache_repo.base_dir.exists() else []
    voxel_color_cache_files = list(ctx.voxel_color_cache_repo.base_dir.glob(f"{space_id}__z*")) \
        if ctx.voxel_color_cache_repo.base_dir.exists() else []
    cache_invalidations = [
        CacheInvalidationItem("spatial_voxel_cache", "3D Viewer用ボクセル座標キャッシュ(再生成可能)",
                               len(spatial_voxel_cache_files)),
        CacheInvalidationItem("voxel_color_cache", "3D Viewer用ボクセル色キャッシュ(再生成可能)",
                               len(voxel_color_cache_files)),
    ]

    # --- Spatial Resolution Result(building_id単位のderived cache) ---
    spatial_resolution_result_exists = ctx.spatial_resolution_result_repo.load(building_id) is not None

    # --- Nodal Endpoint / Connection ---
    endpoints = ctx.nodal_endpoint_repo.list_all(space_id=space_id)
    endpoint_ids = {e.endpoint_id for e in endpoints}
    connections = [
        c for c in ctx.nodal_connection_repo.list_all(building_id=building_id)
        if c.endpoint_space_a.space_id == space_id or c.endpoint_space_b.space_id == space_id
    ]

    return DeletionPlan(
        space_id=space_id, tokutei_code=tokutei_code, building_id=building_id,
        local_space_row=local_space_row, archive_items=archive_items,
        cache_invalidations=cache_invalidations,
        nodal_endpoints=[e.to_dict() for e in endpoints],
        nodal_connections=[c.to_dict() for c in connections],
        legacy_keys_skipped=legacy_keys_skipped,
        spatial_resolution_result_building_exists=spatial_resolution_result_exists,
    )


def _copy_and_verify_file(src: Path, dest: Path) -> None:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    except OSError as e:
        raise DeletionArchiveError(f"アーカイブへのコピーに失敗しました: {src} -> {dest} ({e})") from e
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        raise DeletionArchiveError(f"アーカイブ検証に失敗しました(サイズ不一致): {src} -> {dest}")


def _copy_and_verify_dir(src: Path, dest: Path) -> None:
    try:
        shutil.copytree(src, dest)
    except OSError as e:
        raise DeletionArchiveError(f"アーカイブへのコピーに失敗しました: {src} -> {dest} ({e})") from e
    src_files = sorted(p.relative_to(src) for p in src.rglob("*") if p.is_file())
    dest_files = sorted(p.relative_to(dest) for p in dest.rglob("*") if p.is_file())
    if src_files != dest_files:
        raise DeletionArchiveError(f"アーカイブ検証に失敗しました(ファイル一覧不一致): {src} -> {dest}")
    for rel in src_files:
        if (src / rel).stat().st_size != (dest / rel).stat().st_size:
            raise DeletionArchiveError(f"アーカイブ検証に失敗しました(サイズ不一致): {src / rel}")


def execute_deletion(ctx: DeletionContext, plan: DeletionPlan) -> DeletionResult:
    """plan(build_deletion_planの出力)に従って実削除を行う。

    Phase 1(fail-closed): 実験記録性のあるデータを
    `{archive_root}/{timestamp}_{space_id}/` へコピーし、完全性を確認する。
    1件でも検証に失敗したら例外を送出し、この時点ではactive側のデータは
    一切変更しない(コピー済みの一部ファイルがarchive側に残る可能性はあるが、
    それはただの余分なコピーであり実データの欠損にはならない)。

    Phase 2(Phase 1が完全に成功した場合のみ実行): アーカイブ済みファイルを
    active側から削除し、表示用キャッシュを破棄し、Nodal Endpoint/Connectionを
    削除し、最後にLocalSpace registry行を削除する。
    """
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    archive_dir = ctx.archive_root / f"{timestamp}_{plan.space_id}"

    # --- Phase 1: コピー + 完全性確認(fail-closed) ---
    archived_categories: List[str] = []
    for item in plan.archive_items:
        if not item.exists:
            continue
        if item.kind == "file":
            src = item.source_paths[0]
            dest = archive_dir / item.category / src.name
            _copy_and_verify_file(src, dest)
        elif item.kind == "dir":
            src = item.source_paths[0]
            dest = archive_dir / item.category
            _copy_and_verify_dir(src, dest)
        elif item.kind == "glob":
            dest_dir = archive_dir / item.category
            for src in item.source_paths:
                _copy_and_verify_file(src, dest_dir / src.name)
        archived_categories.append(item.category)

    manifest_path = archive_dir / "deletion_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "space_id": plan.space_id, "tokutei_code": plan.tokutei_code, "building_id": plan.building_id,
        "archived_at": timestamp, "local_space_row": plan.local_space_row,
        "archived_categories": archived_categories,
        "nodal_endpoints_snapshot": plan.nodal_endpoints,
        "nodal_connections_snapshot": plan.nodal_connections,
        "legacy_keys_skipped": [i.to_dict() for i in plan.legacy_keys_skipped],
    }
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 完全性確認: 書き込んだmanifestを読み戻して一致することを確かめる
    if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest_data:
        raise DeletionArchiveError(f"deletion_manifest.jsonの検証に失敗しました: {manifest_path}")

    # --- Phase 2: アーカイブ確定後にのみ、active側を変更する ---
    removed_from_active: List[str] = []
    for item in plan.archive_items:
        if not item.exists:
            continue
        if item.kind == "file":
            item.source_paths[0].unlink()
        elif item.kind == "dir":
            shutil.rmtree(item.source_paths[0])
        elif item.kind == "glob":
            for src in item.source_paths:
                src.unlink()
        removed_from_active.append(item.category)

    # Base Map manifestからentryを除去(bytes自体は既にarchive済み)
    manifest = _load_base_map_manifest(ctx.base_maps_dir)
    removed_ids = {plan.space_id}
    if any(i.category == "base_map_legacy" for i in plan.archive_items if i.exists):
        removed_ids.add(plan.tokutei_code)
    new_manifest = [e for e in manifest if e.get("id") not in removed_ids]
    if len(new_manifest) != len(manifest):
        (ctx.base_maps_dir / "manifest.json").write_text(
            json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    cache_invalidated: List[str] = []
    ctx.spatial_voxel_cache_repo.invalidate(plan.space_id)
    cache_invalidated.append("spatial_voxel_cache")
    ctx.voxel_color_cache_repo.invalidate(plan.space_id)
    cache_invalidated.append("voxel_color_cache")

    spatial_resolution_result_invalidated = ctx.spatial_resolution_result_repo.invalidate(plan.building_id)

    nodal_connections_deleted = []
    for row in plan.nodal_connections:
        ctx.nodal_connection_repo.delete(row["connection_id"])
        nodal_connections_deleted.append(row["connection_id"])
    nodal_endpoints_deleted = []
    for row in plan.nodal_endpoints:
        ctx.nodal_endpoint_repo.delete(row["endpoint_id"])
        nodal_endpoints_deleted.append(row["endpoint_id"])

    # 最後にLocalSpace registry行を削除する(これが「space_idが存在しなくなる」瞬間)
    ctx.local_space_repo.delete(plan.space_id)

    return DeletionResult(
        space_id=plan.space_id, archive_dir=archive_dir, archived_categories=archived_categories,
        removed_from_active=removed_from_active, cache_invalidated=cache_invalidated,
        nodal_endpoints_deleted=nodal_endpoints_deleted, nodal_connections_deleted=nodal_connections_deleted,
        spatial_resolution_result_invalidated=spatial_resolution_result_invalidated,
        local_space_deleted=True,
    )
