"""
段階1本体: 3本柱統合による、屋内空間の占有状態推定モデル

参照: stage1_formalization_v3.md

使い方:
    from spatial_state import Params, SpatialStateTracker

    params = Params(gamma=0.8, n0=3.0, kappa_th=0.5)  # 生成時に理論制約が自動検証される
    tracker = SpatialStateTracker(params)

    state, flag = tracker.update_voxel(
        voxel_id="v_12_5_3",
        c_self=25,
        neighbor_ids=["v_11_5_3"],
        neighbor_counts=[3],
        covered=True,
        patch_fitness=0.028,
        structural_label="wall",
    )
"""
from .params import Params
from .voxel import VoxelState, State, ConfidenceFlag
from .tracker import SpatialStateTracker
from .core import w_cnt, is_peak, h_max, fitness_weight, kappa_theoretical_ceiling

__all__ = [
    "Params",
    "VoxelState",
    "State",
    "ConfidenceFlag",
    "SpatialStateTracker",
    "w_cnt",
    "is_peak",
    "h_max",
    "fitness_weight",
    "kappa_theoretical_ceiling",
]
