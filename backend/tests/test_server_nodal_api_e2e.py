"""
backend/server.py に追加したNodal Information CRUD / Spatial Resolution実行入口
のAPI E2Eテスト(ロードマップPhase 3.6)。

server.appのFlask test_client()を使い、実際のHTTPライクなリクエスト/レスポンス
経由で、保存→明示的resolve→結果取得までを確認する。実データ
(backend/data/registry/nodal_endpoints.json 等)を汚さないよう、テスト冒頭で
server.py側のrepositoryモジュール変数を一時ディレクトリ版へ差し替える。

実行方法(リポジトリルートから):
    python backend/tests/test_server_nodal_api_e2e.py
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import server  # noqa: E402
from domain.nodal_connection import SolutionStatus  # noqa: E402
from domain.transform import RigidTransform2D  # noqa: E402
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402
from repositories.nodal_connection_repository import NodalConnectionRepository  # noqa: E402
from repositories.nodal_endpoint_repository import NodalEndpointRepository  # noqa: E402
from repositories.spatial_resolution_result_repository import SpatialResolutionResultRepository  # noqa: E402
from spatial_id.geographic_projection import DEFAULT_TARGET_EPSG, to_projected  # noqa: E402

_VOXEL_SIZE = 1e-9


def _make_coordinate_definition(zoom: str = "0", voxel_size: float = _VOXEL_SIZE) -> dict:
    return {
        "id": "test", "degree": 0.0, "rad": 0.0, "height": 1.0,
        "origin": [0.0, 0.0, 0.0],
        "unit-size": {zoom: voxel_size},
        "bounds": [[0.0, 0.0, 0.0] for _ in range(8)],
    }


def _id_for_point(zoom: str, point, voxel_size: float = _VOXEL_SIZE) -> str:
    x, y, z = point
    x_idx = round(x / voxel_size - 0.5)
    y_idx = round(y / voxel_size - 0.5)
    f_idx = round(z / voxel_size - 0.5)
    return f"{zoom}/{f_idx}/{x_idx}/{y_idx}"


def _patch_server_repos(tmp):
    """server.pyのモジュール属性(repository群)を、一時ディレクトリ版へ
    差し替える。実データ(backend/data/registry/)には一切触れない。"""
    registry_dir = Path(tmp) / "registry"
    space_def_dir = Path(tmp) / "space_definitions"
    results_dir = Path(tmp) / "spatial_resolution_results"
    space_def_dir.mkdir()

    server.local_space_repo = LocalSpaceRepository(registry_dir, space_def_dir)
    server.nodal_endpoint_repo = NodalEndpointRepository(registry_dir / "nodal_endpoints.json")
    server.nodal_connection_repo = NodalConnectionRepository(registry_dir / "nodal_connections.json")
    server.spatial_resolution_result_repo = SpatialResolutionResultRepository(results_dir)
    return space_def_dir


def _add_space(space_def_dir, building_id, tokutei_code) -> str:
    space_def = _make_coordinate_definition()
    (space_def_dir / f"{tokutei_code}.json").write_text(json.dumps(space_def), encoding="utf-8")
    server.local_space_repo.create(building_id=building_id, tokutei_code=tokutei_code, floor=1, zoom_level=0)
    return f"{building_id}-{tokutei_code}"


def _post(client, path, body):
    resp = client.post(path, data=json.dumps(body), content_type="application/json")
    return resp.get_json(), resp.status_code


def test_nodal_endpoint_crud():
    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = _patch_server_repos(tmp)
        space_a = _add_space(space_def_dir, "b1", "A")
        client = server.app.test_client()

        body, status = _post(client, "/api/nodal-endpoints", {
            "type": "LOCAL", "space_id": space_a, "local_spatial_id": "0/0/0/0",
        })
        assert status == 200, body
        endpoint_id = body["endpoint"]["endpoint_id"]

        resp = client.get(f"/api/nodal-endpoints/{endpoint_id}")
        assert resp.status_code == 200
        assert resp.get_json()["endpoint"]["space_id"] == space_a

        resp = client.get(f"/api/nodal-endpoints?space_id={space_a}")
        assert len(resp.get_json()["endpoints"]) == 1

        put_resp = client.put(
            f"/api/nodal-endpoints/{endpoint_id}",
            data=json.dumps({"type": "LOCAL", "space_id": space_a, "local_spatial_id": "0/1/1/1"}),
            content_type="application/json",
        )
        assert put_resp.status_code == 200
        assert put_resp.get_json()["endpoint"]["local_spatial_id"] == "0/1/1/1"

        del_resp = client.delete(f"/api/nodal-endpoints/{endpoint_id}")
        assert del_resp.status_code == 200
        assert client.get(f"/api/nodal-endpoints/{endpoint_id}").status_code == 404
    print("test_nodal_endpoint_crud: OK")


def test_nodal_connection_crud_and_correspondences():
    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = _patch_server_repos(tmp)
        space_a = _add_space(space_def_dir, "b1", "A")
        space_b = _add_space(space_def_dir, "b1", "B")
        client = server.app.test_client()

        body, status = _post(client, "/api/nodal-connections", {
            "building_id": "b1",
            "endpoint_space_a": {"type": "LOCAL", "space_id": space_a},
            "endpoint_space_b": {"type": "LOCAL", "space_id": space_b},
        })
        assert status == 200, body
        connection_id = body["connection"]["connection_id"]
        assert body["connection"]["correspondences"] == []

        resp = client.get(f"/api/nodal-connections?building_id=b1")
        assert len(resp.get_json()["connections"]) == 1

        node_a, _ = _post(client, "/api/nodal-endpoints", {"type": "LOCAL", "space_id": space_a, "local_spatial_id": "0/0/0/0"})
        node_b, _ = _post(client, "/api/nodal-endpoints", {"type": "LOCAL", "space_id": space_b, "local_spatial_id": "0/0/0/0"})

        corr_body, corr_status = _post(client, f"/api/nodal-connections/{connection_id}/correspondences", {
            "node_a_id": node_a["endpoint"]["endpoint_id"], "node_b_id": node_b["endpoint"]["endpoint_id"],
        })
        assert corr_status == 200, corr_body
        assert len(corr_body["connection"]["correspondences"]) == 1

        del_resp = client.delete(f"/api/nodal-connections/{connection_id}")
        assert del_resp.status_code == 200
        assert client.get(f"/api/nodal-connections/{connection_id}").status_code == 404
    print("test_nodal_connection_crud_and_correspondences: OK")


def test_full_e2e_local_local_and_global_anchor_resolve():
    """ユーザー指定の最低シナリオ:
    Local A↔Bのconnection作成 → correspondence保存 → transform推定 →
    component resolution → Global anchor追加 → EPSG:6677でGLOBAL_RESOLVED
    まで、API(test_client())だけを使って一気通貫で確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = _patch_server_repos(tmp)
        space_a = _add_space(space_def_dir, "b1", "A")
        space_b = _add_space(space_def_dir, "b1", "B")
        client = server.app.test_client()

        # --- 1. Local A↔B connection作成 ---
        conn_body, status = _post(client, "/api/nodal-connections", {
            "building_id": "b1",
            "endpoint_space_a": {"type": "LOCAL", "space_id": space_a},
            "endpoint_space_b": {"type": "LOCAL", "space_id": space_b},
        })
        assert status == 200, conn_body
        conn_ab_id = conn_body["connection"]["connection_id"]

        # --- 2. correspondence保存(2点、既知transformで作った対応点) ---
        local_to_local = RigidTransform2D(yaw_rad=0.3, translation=(2.0, 1.0, 0.0))
        points_a = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        points_b = [local_to_local.apply(p) for p in points_a]
        for pa, pb in zip(points_a, points_b):
            node_a, _ = _post(client, "/api/nodal-endpoints", {
                "type": "LOCAL", "space_id": space_a, "local_spatial_id": _id_for_point("0", pa),
            })
            node_b, _ = _post(client, "/api/nodal-endpoints", {
                "type": "LOCAL", "space_id": space_b, "local_spatial_id": _id_for_point("0", pb),
            })
            _post(client, f"/api/nodal-connections/{conn_ab_id}/correspondences", {
                "node_a_id": node_a["endpoint"]["endpoint_id"], "node_b_id": node_b["endpoint"]["endpoint_id"],
            })

        # --- 3. transform推定 ---
        est_body, est_status = _post(client, f"/api/nodal-connections/{conn_ab_id}/estimate", {})
        assert est_status == 200, est_body
        assert est_body["connection"]["solution"]["status"] == SolutionStatus.SOLVED.value

        # --- 4. Global anchor(Local A↔GLOBAL)追加 ---
        global_resolver = server.StandardSpatialIdResolver()
        global_ids = ["16/0/58000/25000", "16/0/58001/25000", "16/0/58000/25001"]
        geographic_centers = [global_resolver.get_center(gid) for gid in global_ids]
        projected_centers = [to_projected(p, DEFAULT_TARGET_EPSG) for p in geographic_centers]
        known_a_to_global = RigidTransform2D(yaw_rad=0.1, translation=(5.0, -2.0, 1.0))

        anchor_body, status = _post(client, "/api/nodal-connections", {
            "building_id": "b1",
            "endpoint_space_a": {"type": "LOCAL", "space_id": space_a},
            "endpoint_space_b": {"type": "GLOBAL"},
        })
        assert status == 200, anchor_body
        conn_anchor_id = anchor_body["connection"]["connection_id"]

        for gid, pp in zip(global_ids, projected_centers):
            local_metric_point = known_a_to_global.inverse().apply((pp.x, pp.y, pp.alt))
            local_node, _ = _post(client, "/api/nodal-endpoints", {
                "type": "LOCAL", "space_id": space_a, "local_spatial_id": _id_for_point("0", local_metric_point),
            })
            global_node, _ = _post(client, "/api/nodal-endpoints", {"type": "GLOBAL", "global_spatial_id": gid})
            _post(client, f"/api/nodal-connections/{conn_anchor_id}/correspondences", {
                "node_a_id": local_node["endpoint"]["endpoint_id"], "node_b_id": global_node["endpoint"]["endpoint_id"],
            })

        anchor_est_body, anchor_est_status = _post(client, f"/api/nodal-connections/{conn_anchor_id}/estimate", {})
        assert anchor_est_status == 200, anchor_est_body

        # --- 5. resolve(EPSG:6677) ---
        resolve_body, resolve_status = _post(client, "/api/spatial-resolution/resolve", {
            "building_id": "b1", "target_epsg": DEFAULT_TARGET_EPSG,
        })
        assert resolve_status == 200, resolve_body
        components = resolve_body["result"]["components"]
        matching = [c for c in components if space_a in c["local_placement"]["member_space_ids"]]
        assert len(matching) == 1, resolve_body
        component = matching[0]
        assert component["local_placement"]["status"] == "RESOLVED"
        assert component["global_resolution"]["status"] == "RESOLVED", component["global_resolution"]
        assert component["global_resolution"]["target_epsg"] == DEFAULT_TARGET_EPSG
        assert space_b in component["global_resolution"]["member_transforms_to_global"]

        # --- 6. 結果取得(GET) ---
        get_resp = client.get("/api/spatial-resolution/results/b1")
        assert get_resp.status_code == 200
        fetched = get_resp.get_json()["result"]
        assert fetched["target_epsg"] == DEFAULT_TARGET_EPSG
        fetched_matching = [c for c in fetched["components"] if space_a in c["local_placement"]["member_space_ids"]]
        assert fetched_matching[0]["global_resolution"]["status"] == "RESOLVED"
    print("test_full_e2e_local_local_and_global_anchor_resolve: OK")


def test_resolve_unknown_building_returns_empty_result():
    with tempfile.TemporaryDirectory() as tmp:
        _patch_server_repos(tmp)
        client = server.app.test_client()
        body, status = _post(client, "/api/spatial-resolution/resolve", {"building_id": "no-such-building"})
        assert status == 200, body
        assert body["result"]["components"] == []

        assert client.get("/api/spatial-resolution/results/no-such-building").status_code == 200
        assert client.get("/api/spatial-resolution/results/never-resolved").status_code == 404
    print("test_resolve_unknown_building_returns_empty_result: OK")


if __name__ == "__main__":
    test_nodal_endpoint_crud()
    test_nodal_connection_crud_and_correspondences()
    test_full_e2e_local_local_and_global_anchor_resolve()
    test_resolve_unknown_building_returns_empty_result()
    print()
    print("全テスト成功。")
