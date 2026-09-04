"""
backend/repositories/

永続化層(V2指示書§14のディレクトリ構成に対応)。

【重要】このパッケージは、既存の server.py からはまだ一切importされていない、
並行実装である(Phase 2の方針: 既存の実行系に触れず、regressionリスクを
ゼロに保つ)。切り替えはPhase 5(API層)で行う。
"""
from .building_repository import BuildingRepository
from .local_space_repository import LocalSpaceRepository
from .nodal_connection_repository import NodalConnectionRepository
from .nodal_endpoint_repository import NodalEndpointRepository
from .spatial_state_repository import SpatialStateRepository

__all__ = [
    "BuildingRepository",
    "LocalSpaceRepository",
    "NodalEndpointRepository",
    "NodalConnectionRepository",
    "SpatialStateRepository",
]
