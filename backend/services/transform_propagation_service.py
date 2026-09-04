"""
backend/services/transform_propagation_service.py

connected component内で、Local↔LocalのConnectionSolution(RigidTransform2D)を
composeして伝播し、componentの中で任意に選んだ1つのLocal Space(root)を
基準にした相対配置(component-local frame)を求める(ロードマップPhase 3.3)。

【目的(ユーザー指示: 2026-09-02)】
Global anchorが無いLocal Space群でも、1つのcomponent-local frame上に
相対配置できるようにする。Global座標(緯度経度等)はまだ求めない
(Global Spatial ID resolver・Global anchor propagationは対象外、
将来の別Phase)。

【前提】
- Nodal Information(NodalConnection.solution)をsource of truthとする。
  ここでは何も永続化しない(呼び出し側の責務、今回のスコープ外)。
- グラフ構築はspatial_graph.build_components()に委譲する。Local↔Local・
  伝播可能な(status ∈ {SOLVED, WARNING_HIGH_RESIDUAL})接続だけがedgeに
  なるため、unresolved(UNSOLVED)・UNSOLVABLEなedgeは、ここに来る前に
  既に除外されている。
- rootの選び方: 各componentのmember_space_idsのうち、辞書順最小のもの
  (決定的な選び方であればよく、意味的な優先度は無い)。
- edgeの向き: NodalConnection.solutionは常にendpoint_space_a→endpoint_space_b
  のRigidTransform2D(T_a_to_b)。伝播方向が逆(known側がb、対象がa)の場合は、
  domain.transform.RigidTransform2D.inverse()で向きを反転してから使う。
  compose()の数式・引数順序(compose(outer, inner))は一切変更しない。
- scale/reflectionは導入しない(RigidTransform2D自体がこれらを表現できない
  ため、composeやinverseを何回適用してもscale/reflectionは発生し得ない)。

【cycle(非tree辺)の扱い】
spanning treeで一度確定した各Local Spaceのroot基準transformに対し、
非tree辺(同じLocal Spaceへの別経路)から計算した値を突き合わせる。
許容誤差(yaw_tolerance_rad・translation_tolerance_m)を超えて食い違えば、
そのcomponent全体をCONFLICT状態にする(どちらの値が正しいかを自動選択
しない。両方の値と差分をComponentConflictとして記録するだけに留める)。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from domain.component_placement import ComponentConflict, ComponentPlacementResult, ComponentStatus
from domain.nodal_connection import NodalConnection
from domain.transform import RigidTransform2D, compose
from services.spatial_graph import ComponentGraph, build_components

# 較正前の初期値(実データでの調整が別途必要)。
DEFAULT_YAW_TOLERANCE_RAD = 0.01  # 約0.57度
DEFAULT_TRANSLATION_TOLERANCE_M = 0.05  # 5cm


def _connection_transform(connection: NodalConnection) -> RigidTransform2D:
    solution = connection.solution
    return RigidTransform2D(yaw_rad=solution.yaw_rad, translation=tuple(solution.translation))


def _propagate_across(
    known_space_id: str, known_transform: RigidTransform2D, connection: NodalConnection
) -> RigidTransform2D:
    """connection(T_a_to_b)と、known_space_id側の既知のT_known_to_rootから、
    もう片方のspaceのT_to_rootを計算する。known_space_idがconnectionの
    b側の場合は、そのままcomposeできるが、a側の場合はinverse()で向きを
    反転してから使う(モジュールdocstring参照)。
    """
    a_id = connection.endpoint_space_a.space_id
    b_id = connection.endpoint_space_b.space_id
    edge_transform = _connection_transform(connection)  # T_a_to_b

    if known_space_id == a_id:
        # 対象はb側。T_b_to_root = compose(T_a_to_root, T_b_to_a)
        return compose(known_transform, edge_transform.inverse())
    if known_space_id == b_id:
        # 対象はa側。T_a_to_root = compose(T_b_to_root, T_a_to_b)
        return compose(known_transform, edge_transform)
    raise ValueError(
        f"connection '{connection.connection_id}' はspace_id '{known_space_id}' を含みません "
        f"(a={a_id}, b={b_id})。"
    )


def _other_space_id(connection: NodalConnection, current: str) -> str:
    a_id = connection.endpoint_space_a.space_id
    b_id = connection.endpoint_space_b.space_id
    return b_id if current == a_id else a_id


def _yaw_diff_rad(yaw_a: float, yaw_b: float) -> float:
    diff = (yaw_a - yaw_b + math.pi) % (2 * math.pi) - math.pi
    return abs(diff)


def _translation_diff_m(t_a, t_b) -> float:
    return math.dist(t_a, t_b)


def _propagate_component(
    component: ComponentGraph, yaw_tolerance_rad: float, translation_tolerance_m: float
) -> ComponentPlacementResult:
    root = min(component.member_space_ids)
    transforms: Dict[str, RigidTransform2D] = {root: RigidTransform2D.identity()}
    parent_connection_id: Dict[str, Optional[str]] = {root: None}

    adjacency: Dict[str, List[NodalConnection]] = {}
    for connection in component.edges:
        adjacency.setdefault(connection.endpoint_space_a.space_id, []).append(connection)
        adjacency.setdefault(connection.endpoint_space_b.space_id, []).append(connection)
    # 決定的な走査順(隣接space_id、次点でconnection_id)にしておく。
    for space_id, edges in adjacency.items():
        edges.sort(key=lambda c: (_other_space_id(c, space_id), c.connection_id))

    visited = {root}
    queue = [root]
    conflicts: List[ComponentConflict] = []

    while queue:
        current = queue.pop(0)
        current_transform = transforms[current]
        for connection in adjacency.get(current, []):
            other = _other_space_id(connection, current)
            computed = _propagate_across(current, current_transform, connection)

            if other not in visited:
                visited.add(other)
                transforms[other] = computed
                parent_connection_id[other] = connection.connection_id
                queue.append(other)
                continue

            # このconnectionが、まさに現在地(current)へ到達するのに使った
            # tree辺そのものなら、それは同じ辺を逆向きに辿っただけであり
            # cycleではない(スキップする)。
            if connection.connection_id == parent_connection_id.get(current):
                continue

            existing = transforms[other]
            yaw_diff = _yaw_diff_rad(existing.yaw_rad, computed.yaw_rad)
            translation_diff = _translation_diff_m(existing.translation, computed.translation)
            if yaw_diff > yaw_tolerance_rad or translation_diff > translation_tolerance_m:
                conflicts.append(
                    ComponentConflict(
                        connection_id=connection.connection_id,
                        space_id=other,
                        expected=existing,
                        via_edge=computed,
                        yaw_diff_rad=yaw_diff,
                        translation_diff_m=translation_diff,
                    )
                )

    status = ComponentStatus.CONFLICT if conflicts else ComponentStatus.RESOLVED
    return ComponentPlacementResult(
        component_id=component.component_id,
        member_space_ids=sorted(component.member_space_ids),
        root_space_id=root,
        transforms=transforms,
        status=status,
        conflicts=conflicts,
    )


def resolve_component_placements(
    connections: List[NodalConnection],
    yaw_tolerance_rad: float = DEFAULT_YAW_TOLERANCE_RAD,
    translation_tolerance_m: float = DEFAULT_TRANSLATION_TOLERANCE_M,
) -> List[ComponentPlacementResult]:
    """NodalConnectionの集合から、Local↔Localで繋がるconnected componentごとに
    component-local相対配置を計算する。disconnectedなcomponent同士は完全に
    独立に計算される(1つのcomponentの矛盾が他のcomponentに影響しない)。
    """
    components = build_components(connections)
    return [
        _propagate_component(component, yaw_tolerance_rad, translation_tolerance_m)
        for component in components
    ]
