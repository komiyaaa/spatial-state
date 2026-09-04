"""
backend/services/spatial_graph.py

NodalConnectionの集合から、Local↔Local接続だけをedgeとしたグラフを構築し、
connected componentに分割する(ロードマップPhase 3.3)。

【設計方針(ユーザー指示: 2026-09-02、v7設計レビューを踏襲)】
- グラフのnode = Local SpaceのSpace_id、edge = Local↔LocalのNodalConnection
  のうち、伝播に使える(statusが UNSOLVED/UNSOLVABLE ではない)ものだけ。
- Local↔GLOBALのNodalConnectionはこのグラフには一切含めない(GLOBAL側は
  中継nodeではない。Global anchor propagationは今回のスコープ外)。
- unresolved(UNSOLVED)・UNSOLVABLEなsolutionを持つedgeは、グラフの辺として
  一切採用しない(「伝播に使わない」を、グラフ構築の時点で徹底する)。
  そのため、2つのLocal Spaceが「UNSOLVABLEな1本のedgeだけ」で繋がっている
  場合、この2つは別々のcomponentになる(繋がっているとはみなさない)。
- 同じspace_idペアに複数のNodalConnectionが存在してもよい(多重辺として
  扱う。connected component抽出には影響しないが、cycle検出の対象になる)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from domain.nodal_connection import ConnectionEndpointType, NodalConnection, SolutionStatus

_USABLE_STATUSES = {SolutionStatus.SOLVED, SolutionStatus.WARNING_HIGH_RESIDUAL}


def _is_usable_local_to_local(connection: NodalConnection) -> bool:
    a, b = connection.endpoint_space_a, connection.endpoint_space_b
    if a.type != ConnectionEndpointType.LOCAL or b.type != ConnectionEndpointType.LOCAL:
        return False
    return connection.solution.status in _USABLE_STATUSES and connection.solution.yaw_rad is not None


@dataclass
class ComponentGraph:
    """1つのconnected component。member_space_idsとedges(このcomponent内で
    伝播に使えるNodalConnectionの一覧)を持つ。"""

    member_space_ids: Set[str]
    edges: List[NodalConnection] = field(default_factory=list)

    @property
    def component_id(self) -> str:
        """決定的なcomponent識別子(member中で辞書順最小のspace_id)。"""
        return min(self.member_space_ids)


def build_components(connections: List[NodalConnection]) -> List[ComponentGraph]:
    """Local↔Local・伝播可能なNodalConnectionだけをedgeとして、
    connected componentへ分割する。

    Local↔GLOBALの接続、およびstatusがUNSOLVED/UNSOLVABLEの接続は、
    グラフのedgeとして一切使わない(モジュールdocstring参照)。
    """
    usable = [c for c in connections if _is_usable_local_to_local(c)]

    adjacency: Dict[str, List[NodalConnection]] = {}
    for connection in usable:
        space_a = connection.endpoint_space_a.space_id
        space_b = connection.endpoint_space_b.space_id
        adjacency.setdefault(space_a, []).append(connection)
        adjacency.setdefault(space_b, []).append(connection)

    visited: Set[str] = set()
    components: List[ComponentGraph] = []

    for start in sorted(adjacency.keys()):
        if start in visited:
            continue
        member_space_ids: Set[str] = set()
        component_edges: List[NodalConnection] = []
        component_edge_ids: Set[int] = set()
        queue = [start]
        visited.add(start)
        member_space_ids.add(start)

        while queue:
            current = queue.pop(0)
            for connection in adjacency.get(current, []):
                if id(connection) not in component_edge_ids:
                    component_edges.append(connection)
                    component_edge_ids.add(id(connection))
                other = (
                    connection.endpoint_space_b.space_id
                    if connection.endpoint_space_a.space_id == current
                    else connection.endpoint_space_a.space_id
                )
                if other not in visited:
                    visited.add(other)
                    member_space_ids.add(other)
                    queue.append(other)

        components.append(ComponentGraph(member_space_ids=member_space_ids, edges=component_edges))

    return components
