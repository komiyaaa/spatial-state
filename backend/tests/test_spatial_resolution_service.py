"""
backend/services/spatial_resolution_service.py の動作確認テスト
(ロードマップPhase 3.6: Nodal Informationの永続化/APIとSpatial Resolution
service群の実行入口の接続)。

テスト用のCoordinateDefinitionは、test_transform_estimation_service.py /
test_global_resolution_service.pyと同じ「非常に細かいvoxel_size(1e-9m)」の
fine-voxel-grid trickを使い、任意の実数座標をlocal_spatial_id文字列として
表現する。GLOBAL側は本物のStandardSpatialIdResolver(z=16、非極地)を使う
(test_global_resolution_service.pyのtest_real_global_resolver_reaches_resolved_with_valid_anchor
と同じ手法)。

実行方法(リポジトリルートから):
    python backend/tests/test_spatial_resolution_service.py
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.global_resolution import GlobalResolutionStatus  # noqa: E402
from domain.component_placement import ComponentStatus  # noqa: E402
from domain.nodal_connection import (  # noqa: E402
    ConnectionEndpointRef,
    ConnectionEndpointType,
    NodalConnection,
    SolutionStatus,
)
from domain.nodal_endpoint import NodalEndpoint, NodalEndpointType  # noqa: E402
from domain.transform import RigidTransform2D  # noqa: E402
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402
from repositories.nodal_connection_repository import NodalConnectionRepository  # noqa: E402
from repositories.nodal_endpoint_repository import NodalEndpointRepository  # noqa: E402
from services.spatial_resolution_service import estimate_connection_solution, resolve_building  # noqa: E402
from spatial_id.geographic_projection import DEFAULT_TARGET_EPSG, to_projected  # noqa: E402
from spatial_id.global_spatial_id import StandardSpatialIdResolver  # noqa: E402
from spatial_id.local_spatial_id import LocalSpatialIdResolver  # noqa: E402

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


class _Env:
    """1つの一時ディレクトリ上に、Nodal Information一式の実repositoryを
    構築するテストヘルパー(server.pyが本番で使うのと同じrepositoryクラス)。"""

    def __init__(self, tmp):
        self.registry_dir = Path(tmp) / "registry"
        self.space_def_dir = Path(tmp) / "space_definitions"
        self.space_def_dir.mkdir()
        self.local_space_repo = LocalSpaceRepository(self.registry_dir, self.space_def_dir)
        self.endpoint_repo = NodalEndpointRepository(self.registry_dir / "nodal_endpoints.json")
        self.connection_repo = NodalConnectionRepository(self.registry_dir / "nodal_connections.json")
        self.local_resolver = LocalSpatialIdResolver(self.local_space_repo)
        self.global_resolver = StandardSpatialIdResolver()

    def add_space(self, building_id, tokutei_code) -> str:
        space_def = _make_coordinate_definition()
        (self.space_def_dir / f"{tokutei_code}.json").write_text(json.dumps(space_def), encoding="utf-8")
        self.local_space_repo.create(building_id=building_id, tokutei_code=tokutei_code, floor=1, zoom_level=0)
        return f"{building_id}-{tokutei_code}"

    def add_local_endpoint(self, space_id, point) -> str:
        endpoint = NodalEndpoint(
            endpoint_id=str(uuid.uuid4()), type=NodalEndpointType.LOCAL,
            space_id=space_id, local_spatial_id=_id_for_point("0", point),
        )
        self.endpoint_repo.create(endpoint)
        return endpoint.endpoint_id

    def add_global_endpoint(self, global_spatial_id) -> str:
        endpoint = NodalEndpoint(
            endpoint_id=str(uuid.uuid4()), type=NodalEndpointType.GLOBAL, global_spatial_id=global_spatial_id,
        )
        self.endpoint_repo.create(endpoint)
        return endpoint.endpoint_id

    def add_connection(self, building_id, ref_a, ref_b) -> NodalConnection:
        connection = NodalConnection(
            connection_id=str(uuid.uuid4()), building_id=building_id,
            endpoint_space_a=ref_a, endpoint_space_b=ref_b, correspondences=[],
        )
        self.connection_repo.create(connection)
        return connection

    def add_correspondence(self, connection_id, node_a_id, node_b_id) -> None:
        from domain.nodal_connection import Correspondence

        connection = self.connection_repo.get(connection_id)
        connection.correspondences.append(
            Correspondence(pair_id=str(uuid.uuid4()), node_a_id=node_a_id, node_b_id=node_b_id)
        )
        self.connection_repo.update(connection)


def test_estimate_connection_solution_local_local_two_points_solved():
    with tempfile.TemporaryDirectory() as tmp:
        env = _Env(tmp)
        space_a = env.add_space("b1", "A")
        space_b = env.add_space("b1", "B")

        known = RigidTransform2D(yaw_rad=math.pi / 2, translation=(3.0, 4.0, 1.0))
        points_a = [(0.1, 0.2, 0.0), (2.5, 0.7, 0.0)]
        points_b = [known.apply(p) for p in points_a]

        ref_a = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_a)
        ref_b = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_b)
        connection = env.add_connection("b1", ref_a, ref_b)
        for pa, pb in zip(points_a, points_b):
            node_a = env.add_local_endpoint(space_a, pa)
            node_b = env.add_local_endpoint(space_b, pb)
            env.add_correspondence(connection.connection_id, node_a, node_b)

        connection = env.connection_repo.get(connection.connection_id)
        updated = estimate_connection_solution(connection, env.endpoint_repo, env.local_resolver, env.global_resolver)

        assert updated.solution.status == SolutionStatus.SOLVED
        assert math.isclose(updated.solution.yaw_rad, known.yaw_rad, abs_tol=1e-4)
    print("test_estimate_connection_solution_local_local_two_points_solved: OK")


def test_estimate_connection_solution_zero_correspondences_is_unsolvable():
    with tempfile.TemporaryDirectory() as tmp:
        env = _Env(tmp)
        space_a = env.add_space("b1", "A")
        space_b = env.add_space("b1", "B")
        ref_a = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_a)
        ref_b = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_b)
        connection = env.add_connection("b1", ref_a, ref_b)

        updated = estimate_connection_solution(connection, env.endpoint_repo, env.local_resolver, env.global_resolver)
        assert updated.solution.status == SolutionStatus.UNSOLVABLE
        assert updated.solution.n_correspondences == 0
    print("test_estimate_connection_solution_zero_correspondences_is_unsolvable: OK")


def test_resolve_building_downgrades_stale_solved_edge_when_correspondence_removed():
    """ユーザー指示(2026-09-01): Nodal Informationをsource of truthとするため、
    resolve_building()は毎回全connectionを現在のcorrespondencesから
    再評価しなければならない。過去に2点以上でSOLVEDだったconnectionが、
    その後correspondence削除で1点以下になった場合、次のresolve_building()で
    solutionが能動的にUNSOLVABLE等へ更新され、古いSOLVEDのままspatial_graph
    のedgeとして残らないことを確認する(2点以上→resolve→1点以下→resolve、
    という順序で検証)。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = _Env(tmp)
        space_a = env.add_space("b1", "A")
        space_b = env.add_space("b1", "B")

        known = RigidTransform2D(yaw_rad=0.4, translation=(2.0, -1.0, 0.0))
        points_a = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        points_b = [known.apply(p) for p in points_a]

        ref_a = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_a)
        ref_b = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_b)
        connection = env.add_connection("b1", ref_a, ref_b)
        pair_ids = []
        for pa, pb in zip(points_a, points_b):
            node_a = env.add_local_endpoint(space_a, pa)
            node_b = env.add_local_endpoint(space_b, pb)
            env.add_correspondence(connection.connection_id, node_a, node_b)

        # 1回目のresolve: 2点あるのでSOLVED、component RESOLVEDでa/bが繋がる。
        results = resolve_building("b1", env.connection_repo, env.endpoint_repo, env.local_space_repo)
        connection_after_first = env.connection_repo.get(connection.connection_id)
        assert connection_after_first.solution.status == SolutionStatus.SOLVED
        member_sets = [set(r.local_placement.member_space_ids) for r in results]
        assert {space_a, space_b} in member_sets, "2点SOLVED後、A/Bが同じcomponentに属していない"

        # correspondenceを1点だけ残して削除(2点→1点)。
        connection_after_first.correspondences = connection_after_first.correspondences[:1]
        env.connection_repo.update(connection_after_first)

        # 2回目のresolve: source of truth(correspondences)が1点になったので、
        # solutionが能動的にUNSOLVABLE等へ更新され、A/Bは別componentになるはず。
        results2 = resolve_building("b1", env.connection_repo, env.endpoint_repo, env.local_space_repo)
        connection_after_second = env.connection_repo.get(connection.connection_id)
        assert connection_after_second.solution.status != SolutionStatus.SOLVED, (
            "correspondenceが1点に減ったのに、古いSOLVED solutionが残っている"
            "(resolve_building()が全connectionを再評価していない可能性)"
        )
        member_sets2 = [set(r.local_placement.member_space_ids) for r in results2]
        assert {space_a, space_b} not in member_sets2, (
            "古いSOLVED edgeがspatial_graphに残り、A/Bが依然として同じcomponentのまま"
        )
        # services.spatial_graph.build_components()は「1本も伝播可能なedgeを
        # 持たないspace_id」をそもそもcomponentとして返さない(唯一の接続が
        # UNSOLVABLEになったA/Bは、どのcomponentにも一切現れなくなる)。
        assert results2 == [], (
            "唯一の接続がUNSOLVABLEになったのに、componentが1件以上返っている"
        )
    print("test_resolve_building_downgrades_stale_solved_edge_when_correspondence_removed: OK")


def test_resolve_building_local_local_and_global_anchor_end_to_end():
    """ユーザー指定の最低シナリオ: Local A↔Bのconnection作成→correspondence
    保存→transform推定→component resolution→Global anchor追加→
    EPSG:6677でGLOBAL_RESOLVEDまでを、resolve_building()経由で確認する
    (server.pyのAPI E2Eテストと対になる、service層だけでのend-to-end確認)。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = _Env(tmp)
        space_a = env.add_space("b1", "A")
        space_b = env.add_space("b1", "B")

        # --- Local A ↔ Local B ---
        local_to_local = RigidTransform2D(yaw_rad=0.3, translation=(2.0, 1.0, 0.0))
        points_a = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        points_b = [local_to_local.apply(p) for p in points_a]
        ref_a = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_a)
        ref_b = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_b)
        conn_ab = env.add_connection("b1", ref_a, ref_b)
        for pa, pb in zip(points_a, points_b):
            node_a = env.add_local_endpoint(space_a, pa)
            node_b = env.add_local_endpoint(space_b, pb)
            env.add_correspondence(conn_ab.connection_id, node_a, node_b)

        # --- Local A ↔ GLOBAL(実際のStandardSpatialIdResolverを使う) ---
        global_ids = ["16/0/58000/25000", "16/0/58001/25000", "16/0/58000/25001"]
        geographic_centers = [env.global_resolver.get_center(gid) for gid in global_ids]
        projected_centers = [to_projected(p, DEFAULT_TARGET_EPSG) for p in geographic_centers]

        known_a_to_global = RigidTransform2D(yaw_rad=0.1, translation=(5.0, -2.0, 1.0))
        ref_local = ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_a)
        ref_global = ConnectionEndpointRef(type=ConnectionEndpointType.GLOBAL)
        conn_anchor = env.add_connection("b1", ref_local, ref_global)
        for gid, pp in zip(global_ids, projected_centers):
            local_metric_point = known_a_to_global.inverse().apply((pp.x, pp.y, pp.alt))
            local_node = env.add_local_endpoint(space_a, local_metric_point)
            global_node = env.add_global_endpoint(gid)
            env.add_correspondence(conn_anchor.connection_id, local_node, global_node)

        results = resolve_building("b1", env.connection_repo, env.endpoint_repo, env.local_space_repo, target_epsg=DEFAULT_TARGET_EPSG)

        conn_ab_after = env.connection_repo.get(conn_ab.connection_id)
        assert conn_ab_after.solution.status == SolutionStatus.SOLVED

        matching = [r for r in results if space_a in r.local_placement.member_space_ids]
        assert len(matching) == 1
        result = matching[0]
        assert result.local_placement.status == ComponentStatus.RESOLVED
        assert result.global_resolution.status == GlobalResolutionStatus.RESOLVED
        assert result.global_resolution.target_epsg == DEFAULT_TARGET_EPSG
        assert space_a in result.global_resolution.member_transforms_to_global
        assert space_b in result.global_resolution.member_transforms_to_global
    print("test_resolve_building_local_local_and_global_anchor_end_to_end: OK")


if __name__ == "__main__":
    test_estimate_connection_solution_local_local_two_points_solved()
    test_estimate_connection_solution_zero_correspondences_is_unsolvable()
    test_resolve_building_downgrades_stale_solved_edge_when_correspondence_removed()
    test_resolve_building_local_local_and_global_anchor_end_to_end()
    print()
    print("全テスト成功。")
