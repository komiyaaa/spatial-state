"""
backend/domain/

Nodal Information / Spatial Network / Integrated View 機能で使うドメインモデル群
(ClaudeCode_指示書_NodalInformation_SpatialNetwork_IntegratedView_V2.md 対応)。

【重要】registry.py / state_store.py(Spatial State・Base Map・VGICP等の
既存実行系)からは引き続き一切importされていない(既存の実行系に触れず、
regressionリスクをゼロに保つ、という方針を維持)。ただしserver.pyからは、
ロードマップPhase 3.6でNodal Information CRUD・Spatial Resolution実行入口
(/api/nodal-endpoints, /api/nodal-connections, /api/spatial-resolution/*)の
追加に伴い、domain.nodal_endpoint / domain.nodal_connection等をimportする
ようになった(既存の実行系とは完全に独立したAPI群としての追加であり、
Base Map登録・VGICP・Spatial State更新等の既存フローには一切変更を加えていない)。
"""
from .building import Building
from .local_space import CoordinateDefinition, LocalSpace
from .nodal_connection import (
    ConnectionEndpointRef,
    ConnectionEndpointType,
    ConnectionSolution,
    ConnectionType,
    Correspondence,
    NodalConnection,
    SolutionStatus,
)
from .nodal_endpoint import NodalEndpoint, NodalEndpointType

__all__ = [
    "Building",
    "CoordinateDefinition",
    "LocalSpace",
    "NodalEndpoint",
    "NodalEndpointType",
    "ConnectionEndpointRef",
    "ConnectionEndpointType",
    "ConnectionSolution",
    "ConnectionType",
    "Correspondence",
    "NodalConnection",
    "SolutionStatus",
]
