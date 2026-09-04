"""
backend/services/spatial_resolution_service.py

1つのconnectionのcorrespondenceからsolutionを推定・永続化する処理と、
building単位でcomponent解決→Global解決までを行うentry pointをまとめる
(ロードマップPhase 3.6)。既存の計算ロジック(services.transform_estimation_service /
services.spatial_graph / services.transform_propagation_service /
services.global_resolution_service)を呼び出すだけのオーケストレーション層であり、
新しい数式・判定ロジックはここでは持たない。

【Nodal Informationをsource of truthとする(ユーザー指示: 2026-09-01)】
resolve_building()は、対象buildingの**全connection**について、現在の
correspondencesから毎回solutionを再推定する。correspondencesが0点・1点の
connectionも対象から除外しない(「対応点が足りないので推定を試みずスキップ」
という最適化はしない)。理由: 過去にcorrespondencesが2点以上でSOLVED/
WARNING_HIGH_RESIDUALだったconnectionが、その後correspondence削除等で
1点以下になった場合、estimate_connection_solution()を呼ばなければ古い
SOLVEDなsolutionがそのまま残ってしまう。services.spatial_graph.build_components()
はsolution.statusがSOLVED/WARNING_HIGH_RESIDUALのedgeをそのままグラフの
辺として採用するため、これを放置するとNodal Information(現在の
correspondences)と矛盾した古いedgeがconnected component解決に混入する
(サイレントな不整合)。estimate_local_to_local_transform()/estimate_anchor()は
いずれも対応点2点未満で明示的にUNSOLVABLE(またはANCHOR_INSUFFICIENT)を
返す設計になっているため、常に呼び出すだけで「解けなくなったconnectionの
solutionを能動的に無効化する」という状態遷移が自然に実現される。
"""
from __future__ import annotations

import time
from typing import List

from domain.nodal_connection import ConnectionEndpointType, ConnectionSolution, NodalConnection
from domain.nodal_endpoint import NodalEndpointType
from domain.spatial_resolution_result import ComponentResolutionResult
from repositories.local_space_repository import LocalSpaceRepository
from repositories.nodal_connection_repository import NodalConnectionRepository
from repositories.nodal_endpoint_repository import NodalEndpointRepository
from services.global_resolution_service import (
    GlobalAnchorCandidate,
    estimate_anchor,
    resolve_component_global_placement,
)
from services.transform_estimation_service import (
    DEFAULT_DEGENERACY_EPS_M2,
    DEFAULT_WARNING_RMSE_THRESHOLD_M,
    LocalCorrespondencePoint,
    estimate_local_to_local_transform,
)
from services.transform_propagation_service import resolve_component_placements
from spatial_id.geographic_projection import DEFAULT_TARGET_EPSG
from spatial_id.global_spatial_id import GlobalSpatialIdResolver, StandardSpatialIdResolver
from spatial_id.local_spatial_id import LocalSpatialIdResolver


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _resolve_endpoint(endpoint_id: str, endpoint_repo: NodalEndpointRepository):
    endpoint = endpoint_repo.get(endpoint_id)
    if endpoint is None:
        raise ValueError(f"endpoint_id '{endpoint_id}' が見つかりません(NodalEndpointに存在しません)。")
    return endpoint


def estimate_connection_solution(
    connection: NodalConnection,
    endpoint_repo: NodalEndpointRepository,
    local_resolver: LocalSpatialIdResolver,
    global_resolver: GlobalSpatialIdResolver,
    target_epsg: int = DEFAULT_TARGET_EPSG,
) -> NodalConnection:
    """1つのNodalConnectionについて、correspondences(NodalEndpoint参照)を
    実座標へ解決し、solutionを再計算する。connectionの型(LOCAL↔LOCAL /
    LOCAL↔GLOBAL)に応じて推定経路を振り分ける。correspondencesが0点・1点でも
    呼び出してよい(estimate_local_to_local_transform/estimate_anchorが
    UNSOLVABLE/ANCHOR_INSUFFICIENT相当を返す)。返り値は新しいsolutionを
    持つNodalConnection(呼び出し側がrepositoryへupdateする)。
    """
    a, b = connection.endpoint_space_a, connection.endpoint_space_b
    is_local_local = a.type == ConnectionEndpointType.LOCAL and b.type == ConnectionEndpointType.LOCAL

    if is_local_local:
        pairs = []
        for corr in connection.correspondences:
            node_a = _resolve_endpoint(corr.node_a_id, endpoint_repo)
            node_b = _resolve_endpoint(corr.node_b_id, endpoint_repo)
            pairs.append((
                LocalCorrespondencePoint(space_id=node_a.space_id, local_spatial_id=node_a.local_spatial_id),
                LocalCorrespondencePoint(space_id=node_b.space_id, local_spatial_id=node_b.local_spatial_id),
            ))
        connection.solution = estimate_local_to_local_transform(local_resolver, pairs)
        connection.updated_at = _now()
        return connection

    is_local_global = {a.type, b.type} == {ConnectionEndpointType.LOCAL, ConnectionEndpointType.GLOBAL}
    if not is_local_global:
        raise ValueError(
            f"connection '{connection.connection_id}' はLOCAL↔LOCALでもLOCAL↔GLOBALでもありません "
            f"(a.type={a.type}, b.type={b.type})。"
        )

    local_ref = a if a.type == ConnectionEndpointType.LOCAL else b
    local_points = []
    for corr in connection.correspondences:
        node_a = _resolve_endpoint(corr.node_a_id, endpoint_repo)
        node_b = _resolve_endpoint(corr.node_b_id, endpoint_repo)
        local_node, global_node = (node_a, node_b) if node_a.type == NodalEndpointType.LOCAL else (node_b, node_a)
        local_points.append((
            LocalCorrespondencePoint(space_id=local_node.space_id, local_spatial_id=local_node.local_spatial_id),
            global_node.global_spatial_id,
        ))

    anchor = GlobalAnchorCandidate(connection_id=connection.connection_id, correspondences=local_points)
    estimate = estimate_anchor(
        anchor, local_ref.space_id, local_resolver, global_resolver,
        DEFAULT_WARNING_RMSE_THRESHOLD_M, DEFAULT_DEGENERACY_EPS_M2, target_epsg,
    )
    connection.solution = ConnectionSolution(
        status=estimate.fit_status,
        n_correspondences=estimate.n_correspondences,
        yaw_rad=estimate.transform_local_to_global.yaw_rad if estimate.transform_local_to_global else None,
        translation=list(estimate.transform_local_to_global.translation) if estimate.transform_local_to_global else None,
        rmse_m=estimate.rmse_m,
        max_residual_m=estimate.max_residual_m,
        updated_at=_now(),
    )
    connection.updated_at = _now()
    return connection


def _collect_global_anchor_candidates(
    connections: List[NodalConnection], endpoint_repo: NodalEndpointRepository
) -> List[GlobalAnchorCandidate]:
    """LOCAL↔GLOBALのNodalConnectionから、GlobalAnchorCandidate一覧を作る
    (グラフのnode/edgeには一切含めない。services.spatial_graphの除外方針と対になる処理)。
    """
    candidates = []
    for connection in connections:
        a, b = connection.endpoint_space_a, connection.endpoint_space_b
        if {a.type, b.type} != {ConnectionEndpointType.LOCAL, ConnectionEndpointType.GLOBAL}:
            continue
        points = []
        for corr in connection.correspondences:
            node_a = _resolve_endpoint(corr.node_a_id, endpoint_repo)
            node_b = _resolve_endpoint(corr.node_b_id, endpoint_repo)
            local_node, global_node = (node_a, node_b) if node_a.type == NodalEndpointType.LOCAL else (node_b, node_a)
            points.append((
                LocalCorrespondencePoint(space_id=local_node.space_id, local_spatial_id=local_node.local_spatial_id),
                global_node.global_spatial_id,
            ))
        candidates.append(GlobalAnchorCandidate(connection_id=connection.connection_id, correspondences=points))
    return candidates


def resolve_building(
    building_id: str,
    connection_repo: NodalConnectionRepository,
    endpoint_repo: NodalEndpointRepository,
    local_space_repo: LocalSpaceRepository,
    target_epsg: int = DEFAULT_TARGET_EPSG,
) -> List[ComponentResolutionResult]:
    """building配下の**全**connectionのsolutionを現在のcorrespondencesから
    再推定・永続化してから、connected component解決→Global解決までを行う
    (Phase 3.3+3.4/3.5bのオーケストレーション、explicit entry point)。

    correspondencesが0点・1点のconnectionもスキップしない(モジュール
    docstring参照。過去にSOLVEDだったsolutionを能動的にUNSOLVABLE等へ
    更新するため)。
    """
    local_resolver = LocalSpatialIdResolver(local_space_repo)
    global_resolver = StandardSpatialIdResolver()

    connections = connection_repo.list_all(building_id=building_id)
    for connection in connections:
        updated = estimate_connection_solution(connection, endpoint_repo, local_resolver, global_resolver, target_epsg)
        connection_repo.update(updated)

    connections = connection_repo.list_all(building_id=building_id)
    components = resolve_component_placements(connections)
    anchors = _collect_global_anchor_candidates(connections, endpoint_repo)

    results = []
    for component in components:
        relevant_anchors = [
            anchor for anchor in anchors
            if {point.space_id for point, _ in anchor.correspondences} & set(component.member_space_ids)
        ]
        global_resolution = resolve_component_global_placement(
            component, relevant_anchors, local_resolver, global_resolver, target_epsg=target_epsg
        )
        results.append(ComponentResolutionResult(
            component_id=component.component_id,
            local_placement=component,
            global_resolution=global_resolution,
        ))
    return results
