"""
Local Spaceのsource of truth整合確認テスト(ロードマップPhase 3.7)。

【調査で確認した問題(修正前)】
POST /api/local-spaces は registry.create_local_space()(backend/local_spaces.json)
にのみ書き込んでおり、Phase 2/3で新設した repositories.local_space_repository.
LocalSpaceRepository(backend/data/registry/local_spaces.json)には一切反映
されていなかった。migrate_gui_v1_to_v2.py による一回限りの移行はあるが、
それ以降に作成されたLocal Spaceは再度移行しない限りLocalSpaceRepository
(=Nodal Information側、LocalSpatialIdResolver等)から見えない、という
書き込み先の分裂があった(実データで、ichigaya_tamachi-G002のzoom_level/
registered_atが2つのファイルで食い違っていたことで発覚)。

【修正内容】
server.py の create_local_space()・list_building_local_spaces()・
_load_space_def_for_space_id() を、registry.py(legacy)ではなく
local_space_repo(Phase 2 Domain/Repository)経由に統一した
(registry.pyの建物(Building)管理は今回のスコープ外、無変更)。

本ファイルは以下を確認する:
1. LocalSpaceRepository.create()自体の重複space_id時の挙動(partial file無し)
2. POST /api/local-spaces で新規作成したLocal Spaceが、直後に
   LocalSpaceRepository.list_all()/get()から参照できること
3. その新規Local Spaceに対して、LocalSpatialIdResolver.resolve_local_center()
   が実際に解決できること
4. create_local_space()・list_building_local_spaces()・
   _load_space_def_for_space_id() のソースが、もうregistry.create_local_space()/
   registry.list_local_spaces()を呼んでいないこと(回帰ガード)
5. 同一tokutei_codeでの重複作成が、local_spaces.json/space_definitionファイルを
   壊れた状態(不正JSON・重複行)にしないこと

実データ(backend/data/registry/, backend/local_spaces.json, backend/buildings.json,
backend/space_definitions/, base_maps/)には一切触れない
(server.pyのモジュール変数を一時ディレクトリ版へ差し替えて実行する)。

実行方法(リポジトリルートから):
    python backend/tests/test_local_space_source_of_truth.py
"""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import server  # noqa: E402
from registry import Registry  # noqa: E402
from repositories.building_repository import BuildingRepository  # noqa: E402
from repositories.label_fitness_history_repository import LabelFitnessHistoryRepository  # noqa: E402
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402
from repositories.plane_repository import PlaneRepository  # noqa: E402
from repositories.spatial_voxel_cache_repository import SpatialVoxelCacheRepository  # noqa: E402
from repositories.spatial_voxel_label_repository import SpatialVoxelLabelRepository  # noqa: E402
from repositories.voxel_color_cache_repository import VoxelColorCacheRepository  # noqa: E402
from space_definition_generator import finest_zoom_level  # noqa: E402
from spatial_id.local_spatial_id import LocalSpatialIdResolver  # noqa: E402


def _make_room_points(sx=6.0, sy=4.0, sz=3.0, nx=8, ny=8, nz=5) -> np.ndarray:
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    return np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)


def _write_ply(points: np.ndarray) -> bytes:
    import open3d as o3d

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "room.ply"
        assert o3d.io.write_point_cloud(str(path), pcd)
        return path.read_bytes()


def _patch_server_globals(tmp: Path):
    """server.pyのモジュール変数(repository・パス定数)を一時ディレクトリ版へ
    差し替える。実データには一切触れない。"""
    registry_dir = tmp / "data" / "registry"
    space_def_dir = tmp / "space_definitions"
    base_maps_dir = tmp / "base_maps"
    planes_dir = tmp / "data" / "planes"
    voxel_labels_dir = tmp / "data" / "voxel_labels"
    label_history_dir = tmp / "data" / "structural_label_fitness_history"
    spatial_voxel_cache_dir = tmp / "data" / "spatial_voxel_cache"
    voxel_color_cache_dir = tmp / "data" / "voxel_color_cache"
    space_def_dir.mkdir(parents=True)

    server.registry = Registry(tmp)  # legacy(backend/buildings.json・local_spaces.json)側。呼び出し元は無いが念のため隔離する
    server.building_repo = BuildingRepository(tmp / "buildings.json")
    server.local_space_repo = LocalSpaceRepository(registry_dir, space_def_dir)
    server.SPACE_DEF_DIR = space_def_dir
    server.BASE_MAPS_DIR = base_maps_dir
    server.plane_repo = PlaneRepository(planes_dir)
    server.voxel_label_repo = SpatialVoxelLabelRepository(voxel_labels_dir)
    server.label_fitness_history_repo = LabelFitnessHistoryRepository(label_history_dir)
    server.spatial_voxel_cache_repo = SpatialVoxelCacheRepository(spatial_voxel_cache_dir)
    server.voxel_color_cache_repo = VoxelColorCacheRepository(voxel_color_cache_dir)
    return registry_dir, space_def_dir


def _create_local_space_via_api(client, building_id, tokutei_code, ply_bytes, floor="1"):
    return client.post(
        "/api/local-spaces",
        data=ply_bytes,
        headers={
            "X-Building-Id": building_id,
            "X-Tokutei-Code": tokutei_code,
            "X-Filename": "room.ply",
            "X-Floor": floor,
            "X-Rotation-Degree": "0",
            "Content-Type": "application/octet-stream",
        },
    )


def test_local_space_repository_create_raises_cleanly_without_partial_row():
    """LocalSpaceRepository.create()自体の重複挙動: 重複時は例外を送出し、
    local_spaces.jsonに壊れた/重複した行を一切残さないことを確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        registry_dir = Path(tmp) / "registry"
        space_def_dir = Path(tmp) / "space_definitions"
        space_def_dir.mkdir()
        repo = LocalSpaceRepository(registry_dir, space_def_dir)

        repo.create(building_id="b1", tokutei_code="A", floor=1, zoom_level=9)
        try:
            repo.create(building_id="b1", tokutei_code="A", floor=2, zoom_level=9)
            raise AssertionError("重複tokutei_codeの作成がエラーにならなかった")
        except ValueError:
            pass

        rows = json.loads((registry_dir / "local_spaces.json").read_text(encoding="utf-8"))
        assert len(rows) == 1, f"重複作成の失敗後にpartial/重複行が残っている: {rows}"
        assert rows[0]["floor"] == 1, "1回目の正常な行が上書き・破損している"
        assert len(repo.list_all(building_id="b1")) == 1
    print("test_local_space_repository_create_raises_cleanly_without_partial_row: OK")


def test_create_local_space_endpoint_is_immediately_visible_via_repository_and_resolver():
    """POST /api/local-spaces で作成したLocal Spaceが、
    LocalSpaceRepository.list_all()/get()から即座に見え、
    LocalSpatialIdResolver.resolve_local_center()で解決できることを確認する。"""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        registry_dir, space_def_dir = _patch_server_globals(tmp)
        client = server.app.test_client()

        # building_id取得はbuilding_repo(create_local_space()が実際に参照するsource
        # of truth)を使う。legacy registryを使うと、たまたま実データのbuildings.json
        # に同名のbuilding_idが存在する場合にだけ偶然テストが通ってしまう
        # (2026-09-02発見・修正: Building移行後、この関数は当初registryのままだった)。
        building = server.building_repo.create(name="テスト建物")
        building_id = building.building_id

        ply_bytes = _write_ply(_make_room_points())
        resp = _create_local_space_via_api(client, building_id, "T1", ply_bytes)
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        space_id = body["local_space"]["space_id"]
        assert space_id == f"{building_id}-T1"

        # 1. repository参照(list_all/get)
        space_via_get = server.local_space_repo.get(space_id)
        assert space_via_get is not None, "作成直後にLocalSpaceRepository.get()で見つからない"
        space_via_list = server.local_space_repo.list_all(building_id=building_id)
        assert any(s.space_id == space_id for s in space_via_list), (
            "作成直後にLocalSpaceRepository.list_all()で見つからない"
        )

        # 2. GET /api/buildings/<id>/local-spaces(読み出しAPI側)にも反映されている
        list_resp = client.get(f"/api/buildings/{building_id}/local-spaces")
        assert list_resp.status_code == 200
        listed_ids = [s["space_id"] for s in list_resp.get_json()["local_spaces"]]
        assert space_id in listed_ids

        # 3. LocalSpatialIdResolverで実際に解決できる
        resolver = LocalSpatialIdResolver(server.local_space_repo)
        finest = finest_zoom_level(space_via_get.coordinate_definition.to_dict())
        center = resolver.resolve_local_center(space_id, f"{finest}/0/0/0")
        assert len(center) == 3 and all(isinstance(v, float) for v in center)

        # 4. 旧registry(legacy)側には、このLocal Spaceが一切書き込まれていない
        #    (dual-writeになっていないことの確認。legacyのlocal_spaces.jsonは
        #    _seed_if_missingの初期シードのみのはず)
        legacy_spaces = server.registry.list_local_spaces()
        assert not any(s["space_id"] == space_id for s in legacy_spaces), (
            "新規作成したLocal Spaceが、legacyのregistry.local_spaces.jsonにも"
            "書き込まれている(dual-writeが残っている)"
        )
    print("test_create_local_space_endpoint_is_immediately_visible_via_repository_and_resolver: OK")


def test_duplicate_local_space_creation_via_api_leaves_no_partial_state():
    """同一building_id・tokutei_codeでのAPI経由の重複作成が、
    local_spaces.json・space_definitionファイルを壊れた/重複した状態に
    しないことを確認する。"""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        registry_dir, space_def_dir = _patch_server_globals(tmp)
        client = server.app.test_client()

        building = server.building_repo.create(name="重複テスト建物")
        building_id = building.building_id

        ply_bytes = _write_ply(_make_room_points())
        resp1 = _create_local_space_via_api(client, building_id, "DUP", ply_bytes)
        assert resp1.status_code == 200, resp1.get_json()

        resp2 = _create_local_space_via_api(client, building_id, "DUP", ply_bytes)
        assert resp2.status_code == 400, "重複space_idの2回目作成がエラーにならなかった"

        rows = json.loads((registry_dir / "local_spaces.json").read_text(encoding="utf-8"))
        matching = [r for r in rows if r["tokutei_code"] == "DUP" and r["building_id"] == building_id]
        assert len(matching) == 1, f"重複作成の失敗後にpartial/重複行が残っている: {matching}"

        space_id = f"{building_id}-DUP"
        space = server.local_space_repo.get(space_id)
        assert space is not None and space.coordinate_definition is not None, (
            "重複作成の失敗後、space_definitionファイルが壊れて読めなくなっている"
        )
        resolver = LocalSpatialIdResolver(server.local_space_repo)
        finest = finest_zoom_level(space.coordinate_definition.to_dict())
        resolver.resolve_local_center(space_id, f"{finest}/0/0/0")  # 例外にならなければOK
    print("test_duplicate_local_space_creation_via_api_leaves_no_partial_state: OK")


def test_server_endpoints_no_longer_use_legacy_registry_for_local_spaces():
    """create_local_space()・list_building_local_spaces()・
    _load_space_def_for_space_id()が、もうregistry.create_local_space()/
    registry.list_local_spaces()を呼んでいないことのソースベース回帰ガード
    (test_cache_invalidation.pyと同じinspect.getsource()方式)。"""
    src_create = inspect.getsource(server.create_local_space)
    assert "registry.create_local_space(" not in src_create
    assert "local_space_repo.create(" in src_create

    src_list = inspect.getsource(server.list_building_local_spaces)
    assert "registry.list_local_spaces(" not in src_list
    assert "local_space_repo.list_all(" in src_list

    src_load = inspect.getsource(server._load_space_def_for_space_id)
    assert "registry.list_local_spaces(" not in src_load
    assert "local_space_repo.get(" in src_load
    print("test_server_endpoints_no_longer_use_legacy_registry_for_local_spaces: OK")


def test_server_endpoints_no_longer_use_legacy_registry_for_buildings():
    """list_buildings()・create_building()・create_local_space()のbuilding存在
    チェックが、もうregistry.list_buildings()/registry.create_building()を
    呼んでいないことのソースベース回帰ガード(ER図反映、2026-09-02)。"""
    assert "registry.list_buildings(" not in inspect.getsource(server.list_buildings)
    assert "building_repo.list_all(" in inspect.getsource(server.list_buildings)

    assert "registry.create_building(" not in inspect.getsource(server.create_building)
    assert "building_repo.create(" in inspect.getsource(server.create_building)

    assert "registry.list_buildings(" not in inspect.getsource(server.create_local_space)
    assert "building_repo.get(" in inspect.getsource(server.create_local_space)
    print("test_server_endpoints_no_longer_use_legacy_registry_for_buildings: OK")


if __name__ == "__main__":
    test_local_space_repository_create_raises_cleanly_without_partial_row()
    test_create_local_space_endpoint_is_immediately_visible_via_repository_and_resolver()
    test_duplicate_local_space_creation_via_api_leaves_no_partial_state()
    test_server_endpoints_no_longer_use_legacy_registry_for_local_spaces()
    test_server_endpoints_no_longer_use_legacy_registry_for_buildings()
    print()
    print("全テスト成功。")
