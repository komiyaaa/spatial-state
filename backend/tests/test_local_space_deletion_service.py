"""
backend/tests/test_local_space_deletion_service.py

Local Space削除機能(local_space_deletion_service.py)のテスト。
実行方法(backendディレクトリから): python -m pytest tests/test_local_space_deletion_service.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from domain import (  # noqa: E402
    ConnectionEndpointRef,
    ConnectionEndpointType,
    NodalConnection,
    NodalEndpoint,
    NodalEndpointType,
)
from local_space_deletion_service import (  # noqa: E402
    DeletionArchiveError,
    DeletionContext,
    build_deletion_plan,
    execute_deletion,
)
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402
from repositories.nodal_connection_repository import NodalConnectionRepository  # noqa: E402
from repositories.nodal_endpoint_repository import NodalEndpointRepository  # noqa: E402
from repositories.spatial_resolution_result_repository import SpatialResolutionResultRepository  # noqa: E402
from repositories.spatial_voxel_cache_repository import SpatialVoxelCacheRepository  # noqa: E402
from repositories.voxel_color_cache_repository import VoxelColorCacheRepository  # noqa: E402


def _make_ctx(tmp_path: Path) -> DeletionContext:
    registry_dir = tmp_path / "registry"
    space_def_dir = tmp_path / "space_definitions"
    return DeletionContext(
        local_space_repo=LocalSpaceRepository(registry_dir, space_def_dir),
        nodal_endpoint_repo=NodalEndpointRepository(registry_dir / "nodal_endpoints.json"),
        nodal_connection_repo=NodalConnectionRepository(registry_dir / "nodal_connections.json"),
        spatial_resolution_result_repo=SpatialResolutionResultRepository(tmp_path / "spatial_resolution_results"),
        spatial_voxel_cache_repo=SpatialVoxelCacheRepository(tmp_path / "spatial_voxel_cache"),
        voxel_color_cache_repo=VoxelColorCacheRepository(tmp_path / "voxel_color_cache"),
        space_def_dir=space_def_dir,
        base_maps_dir=tmp_path / "base_maps",
        planes_dir=tmp_path / "planes",
        voxel_labels_dir=tmp_path / "voxel_labels",
        structural_label_history_dir=tmp_path / "structural_label_fitness_history",
        tracker_state_dir=tmp_path / "tracker_state",
        registration_results_dir=tmp_path / "registration_results",
        rough_dir=tmp_path / "rough_registered",
        precise_dir=tmp_path / "precise_registered",
        scan_json_dir=tmp_path / "scan_json",
        vgicp_log_dir=tmp_path / "vgicp_logs",
        archive_root=tmp_path / "_archived_local_spaces",
    )


def _seed_space_data(ctx: DeletionContext, space_id: str, building_id: str, tokutei_code: str,
                      floor: int = 1, zoom_level: int = 11) -> None:
    """1つのLocal Spaceについて、削除対象になりうる全カテゴリへダミーデータを置く。"""
    ctx.local_space_repo.create(building_id=building_id, tokutei_code=tokutei_code, floor=floor,
                                 zoom_level=zoom_level)

    ctx.space_def_dir.mkdir(parents=True, exist_ok=True)
    dummy_coordinate_definition = {
        "id": space_id, "degree": 0.0, "rad": 0.0, "height": 3.0,
        "origin": [0.0, 0.0, 0.0], "unit-size": {"11": 0.03}, "bounds": [[0, 0], [1, 1]],
    }
    (ctx.space_def_dir / f"{space_id}.json").write_text(
        json.dumps(dummy_coordinate_definition), encoding="utf-8",
    )

    ctx.base_maps_dir.mkdir(parents=True, exist_ok=True)
    (ctx.base_maps_dir / f"{space_id}.las").write_bytes(b"dummy-las-bytes")
    manifest = []
    manifest_path = ctx.base_maps_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.append({"id": space_id, "label": space_id, "file": f"{space_id}.las"})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx.planes_dir.mkdir(parents=True, exist_ok=True)
    (ctx.planes_dir / f"{space_id}.json").write_text("[]", encoding="utf-8")

    ctx.voxel_labels_dir.mkdir(parents=True, exist_ok=True)
    (ctx.voxel_labels_dir / f"{space_id}.json").write_text("{}", encoding="utf-8")

    ctx.structural_label_history_dir.mkdir(parents=True, exist_ok=True)
    (ctx.structural_label_history_dir / f"{space_id}.json").write_text("[]", encoding="utf-8")

    ctx.tracker_state_dir.mkdir(parents=True, exist_ok=True)
    (ctx.tracker_state_dir / f"{space_id}.json").write_text(json.dumps({"space_id": space_id}), encoding="utf-8")

    reg_dir = ctx.registration_results_dir / space_id / "run_001"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "precise_registered.ply").write_bytes(b"ply-bytes")

    (ctx.rough_dir / space_id).mkdir(parents=True, exist_ok=True)
    (ctx.rough_dir / space_id / "a.ply").write_bytes(b"rough")
    (ctx.precise_dir / space_id).mkdir(parents=True, exist_ok=True)
    (ctx.precise_dir / space_id / "a.ply").write_bytes(b"precise")
    (ctx.scan_json_dir / space_id).mkdir(parents=True, exist_ok=True)
    (ctx.scan_json_dir / space_id / "a.json").write_text("{}", encoding="utf-8")

    ctx.vgicp_log_dir.mkdir(parents=True, exist_ok=True)
    (ctx.vgicp_log_dir / f"{space_id}_a.json").write_text("{}", encoding="utf-8")


def test_build_deletion_plan_identifies_all_existing_categories(tmp_path):
    ctx = _make_ctx(tmp_path)
    _seed_space_data(ctx, "b1-G001", "b1", "G001")

    plan = build_deletion_plan(ctx, "b1-G001")

    categories = {i.category for i in plan.archive_items if i.exists}
    expected = {
        "coordinate_definition", "base_map", "plane", "voxel_label",
        "structural_label_fitness_history", "spatial_state",
        "registration_results", "rough_registered", "precise_registered",
        "scan_json", "vgicp_logs",
    }
    assert expected.issubset(categories), f"想定カテゴリが揃っていない: {categories}"
    assert plan.tokutei_code == "G001"
    assert plan.building_id == "b1"
    print("test_build_deletion_plan_identifies_all_existing_categories: OK")


def test_build_deletion_plan_skips_legacy_when_tokutei_code_used_elsewhere(tmp_path):
    ctx = _make_ctx(tmp_path)
    _seed_space_data(ctx, "b1-G001", "b1", "G001")
    # 別Buildingで同じtokutei_code "G001" を使う別Local Space(衝突ケース)
    ctx.local_space_repo.create(building_id="b2", tokutei_code="G001", floor=1, zoom_level=11)
    # legacy tokutei_code-keyedのBase Mapファイル(2つのspaceで共有されうる形)
    manifest_path = ctx.base_maps_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.append({"id": "G001", "label": "G001", "file": "legacy_G001.las"})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (ctx.base_maps_dir / "legacy_G001.las").write_bytes(b"legacy-bytes")

    plan = build_deletion_plan(ctx, "b1-G001")

    assert not any(i.category == "base_map_legacy" and i.exists for i in plan.archive_items), \
        "他spaceが使用中のtokutei_codeなのに、legacyファイルがアーカイブ対象に入っている"
    assert any(s.category == "base_map_legacy" for s in plan.legacy_keys_skipped), \
        "衝突がlegacy_keys_skippedに記録されていない"
    print("test_build_deletion_plan_skips_legacy_when_tokutei_code_used_elsewhere: OK")


def test_execute_deletion_archives_and_removes_from_active(tmp_path):
    ctx = _make_ctx(tmp_path)
    _seed_space_data(ctx, "b1-G001", "b1", "G001")
    plan = build_deletion_plan(ctx, "b1-G001")

    result = execute_deletion(ctx, plan)

    # active側から消えていること
    assert not (ctx.space_def_dir / "b1-G001.json").exists()
    assert not (ctx.base_maps_dir / "b1-G001.las").exists()
    assert not (ctx.planes_dir / "b1-G001.json").exists()
    assert not (ctx.tracker_state_dir / "b1-G001.json").exists()
    assert not (ctx.registration_results_dir / "b1-G001").exists()
    assert not list(ctx.vgicp_log_dir.glob("b1-G001_*"))
    assert ctx.local_space_repo.get("b1-G001") is None

    # archive側に存在すること
    assert (result.archive_dir / "coordinate_definition" / "b1-G001.json").exists()
    assert (result.archive_dir / "base_map" / "b1-G001.las").exists()
    assert (result.archive_dir / "registration_results" / "run_001" / "precise_registered.ply").exists()
    manifest = json.loads((result.archive_dir / "deletion_manifest.json").read_text(encoding="utf-8"))
    assert manifest["space_id"] == "b1-G001"
    assert manifest["tokutei_code"] == "G001"

    # manifest.jsonからentryが除去されていること
    base_manifest = json.loads((ctx.base_maps_dir / "manifest.json").read_text(encoding="utf-8"))
    assert not any(e["id"] == "b1-G001" for e in base_manifest)
    print("test_execute_deletion_archives_and_removes_from_active: OK")


def test_execute_deletion_is_fail_closed_on_archive_failure(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path)
    _seed_space_data(ctx, "b1-G001", "b1", "G001")
    plan = build_deletion_plan(ctx, "b1-G001")

    import local_space_deletion_service as svc

    def _boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    # registration_results(ディレクトリ)のコピーで失敗させる(他の複数ファイル項目は
    # それより前に処理されているはずだが、fail-closedならactive側は無傷のまま)。
    monkeypatch.setattr(svc.shutil, "copytree", _boom)

    with pytest.raises(DeletionArchiveError):
        execute_deletion(ctx, plan)

    # active側のデータが一切消えていないこと(fail-closed)
    assert (ctx.space_def_dir / "b1-G001.json").exists()
    assert (ctx.base_maps_dir / "b1-G001.las").exists()
    assert (ctx.planes_dir / "b1-G001.json").exists()
    assert (ctx.tracker_state_dir / "b1-G001.json").exists()
    assert (ctx.registration_results_dir / "b1-G001").exists()
    assert ctx.local_space_repo.get("b1-G001") is not None, "失敗時にLocalSpace登録行が削除されてしまっている"
    print("test_execute_deletion_is_fail_closed_on_archive_failure: OK")


def test_execute_deletion_nodal_cascade_and_other_space_untouched(tmp_path):
    ctx = _make_ctx(tmp_path)
    _seed_space_data(ctx, "b1-G001", "b1", "G001")
    _seed_space_data(ctx, "b1-G002", "b1", "G002")

    own_endpoint = ctx.nodal_endpoint_repo.create(NodalEndpoint(
        endpoint_id="ep-own", type=NodalEndpointType.LOCAL, space_id="b1-G001", local_spatial_id="11/0/0/0",
    ))
    other_endpoint = ctx.nodal_endpoint_repo.create(NodalEndpoint(
        endpoint_id="ep-other", type=NodalEndpointType.LOCAL, space_id="b1-G002", local_spatial_id="11/0/0/0",
    ))
    unrelated_endpoint = ctx.nodal_endpoint_repo.create(NodalEndpoint(
        endpoint_id="ep-unrelated", type=NodalEndpointType.LOCAL, space_id="b1-G002", local_spatial_id="11/1/1/1",
    ))
    ctx.nodal_connection_repo.create(NodalConnection(
        connection_id="conn-1", building_id="b1",
        endpoint_space_a=ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id="b1-G001"),
        endpoint_space_b=ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id="b1-G002"),
        correspondences=[],
    ))

    plan = build_deletion_plan(ctx, "b1-G001")
    execute_deletion(ctx, plan)

    assert ctx.nodal_endpoint_repo.get("ep-own") is None, "削除対象spaceのEndpointが残っている"
    assert ctx.nodal_connection_repo.get("conn-1") is None, "ダングリングになるはずのConnectionが残っている"
    assert ctx.nodal_endpoint_repo.get("ep-other") is not None, "無関係な他spaceのEndpointが巻き込まれて消えた"
    assert ctx.nodal_endpoint_repo.get("ep-unrelated") is not None, "無関係な他spaceのEndpointが巻き込まれて消えた"

    # 他Local Space自体のデータも無傷であること
    assert ctx.local_space_repo.get("b1-G002") is not None
    assert (ctx.space_def_dir / "b1-G002.json").exists()
    assert (ctx.tracker_state_dir / "b1-G002.json").exists()
    print("test_execute_deletion_nodal_cascade_and_other_space_untouched: OK")


def test_execute_deletion_invalidates_caches(tmp_path):
    ctx = _make_ctx(tmp_path)
    _seed_space_data(ctx, "b1-G001", "b1", "G001")
    ctx.spatial_voxel_cache_repo.base_dir.mkdir(parents=True, exist_ok=True)
    (ctx.spatial_voxel_cache_repo.base_dir / "b1-G001__z11.meta.json").write_text("{}", encoding="utf-8")
    ctx.voxel_color_cache_repo.base_dir.mkdir(parents=True, exist_ok=True)
    (ctx.voxel_color_cache_repo.base_dir / "b1-G001__z11__DEFAULT.meta.json").write_text("{}", encoding="utf-8")
    ctx.spatial_resolution_result_repo.save("b1", [], target_epsg=6677)

    plan = build_deletion_plan(ctx, "b1-G001")
    assert any(c.category == "spatial_voxel_cache" and c.matched_file_count == 1 for c in plan.cache_invalidations)
    assert plan.spatial_resolution_result_building_exists is True

    result = execute_deletion(ctx, plan)

    assert not list(ctx.spatial_voxel_cache_repo.base_dir.glob("b1-G001__z*"))
    assert not list(ctx.voxel_color_cache_repo.base_dir.glob("b1-G001__z*"))
    assert ctx.spatial_resolution_result_repo.load("b1") is None
    assert result.spatial_resolution_result_invalidated is True
    print("test_execute_deletion_invalidates_caches: OK")


if __name__ == "__main__":
    import tempfile

    for fn in [
        test_build_deletion_plan_identifies_all_existing_categories,
        test_build_deletion_plan_skips_legacy_when_tokutei_code_used_elsewhere,
        test_execute_deletion_archives_and_removes_from_active,
        test_execute_deletion_nodal_cascade_and_other_space_untouched,
        test_execute_deletion_invalidates_caches,
    ]:
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
    print()
    print("全テスト成功(fail-closedテストはpytest経由で実行してください、monkeypatchが必要)。")
