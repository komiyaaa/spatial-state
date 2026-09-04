"""
backend/domain/spatial_resolution_result.py

1つのconnected componentについて、Local↔Local相対配置(Phase 3.3)と
Global Resolution(Phase 3.4/3.5b)の結果を束ねた、Spatial Resolution実行
1回分の出力単位(ロードマップPhase 3.6)。

【位置づけ】これ自体もderived data(source of truthはNodal Information、
すなわちNodalEndpoint/NodalConnection)であり、
repositories.spatial_resolution_result_repositoryが保存するのはあくまで
「直近の実行結果のスナップショット」。Nodal Informationが変わったら、
POST /api/spatial-resolution/resolve を再実行して上書きする前提であり、
このスナップショット自体を書き換えて整合性を取る、という運用はしない。
"""
from __future__ import annotations

from dataclasses import dataclass

from domain.component_placement import ComponentPlacementResult
from domain.global_resolution import ComponentGlobalResolution


@dataclass
class ComponentResolutionResult:
    component_id: str
    local_placement: ComponentPlacementResult
    global_resolution: ComponentGlobalResolution

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "local_placement": self.local_placement.to_dict(),
            "global_resolution": self.global_resolution.to_dict(),
        }
