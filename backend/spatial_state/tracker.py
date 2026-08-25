"""
複数ボクセルと、構造ラベルごとの fitness_score 相場を統括する
SpatialStateTracker。

参照: stage1_formalization_v3.md 全体、特に §2.7(w_fit・N_fit_min)
"""
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

from .params import Params
from .voxel import VoxelState, State, ConfidenceFlag


class SpatialStateTracker:
    """ボクセル群の状態を、時間をまたいで管理するトラッカー。

    使い方の概要:
        tracker = SpatialStateTracker(Params(...))
        state, flag = tracker.update_voxel(
            voxel_id="v_12_5_3",
            c_self=25,
            neighbor_ids=["v_11_5_3", "v_13_5_3"],
            neighbor_counts=[3, 0],
            covered=True,
            patch_fitness=0.028,
            structural_label="wall",
        )
    """

    def __init__(self, params: Params):
        self.params = params
        self.voxels: Dict[str, VoxelState] = {}
        self.fitness_history: Dict[str, List[float]] = {}

    def _get_or_create_voxel(
        self, voxel_id: str, alpha0: float = 0.05, beta0: float = 0.05
    ) -> VoxelState:
        if voxel_id not in self.voxels:
            self.voxels[voxel_id] = VoxelState(voxel_id=voxel_id, alpha=alpha0, beta=beta0)
        return self.voxels[voxel_id]

    def _median_fitness(self, structural_label: Optional[str]) -> Optional[float]:
        """そのラベルの、これまでのfitness_scoreの中央値を返す。

        蓄積パッチ数が N_fit_min 未満なら None(まだ相場が信用できない、§2.7)。
        """
        if structural_label is None:
            return None
        history = self.fitness_history.get(structural_label, [])
        if len(history) < self.params.N_fit_min:
            return None
        return statistics.median(history)

    def set_prior(self, voxel_id: str, alpha0: float, beta0: float) -> None:
        """構造ラベルに基づく事前分布(alpha0, beta0)を設定する。§5.4

        ボクセルが初めて登場する前に呼ぶ必要がある。
        """
        if voxel_id in self.voxels:
            raise ValueError(f"ボクセル {voxel_id} は既に初期化済みです。set_prior は初回登場前に呼んでください。")
        self.voxels[voxel_id] = VoxelState(voxel_id=voxel_id, alpha=alpha0, beta=beta0)

    def update_voxel(
        self,
        voxel_id: str,
        c_self: float,
        neighbor_ids: Sequence[str],
        neighbor_counts: Sequence[float],
        covered: bool,
        patch_fitness: Optional[float] = None,
        structural_label: Optional[str] = None,
    ) -> Tuple[State, ConfidenceFlag]:
        """1回分の観測を、指定したボクセルに反映する。

        Returns
        -------
        (State, ConfidenceFlag)
            更新後の状態と確信度フラグ
        """
        voxel = self._get_or_create_voxel(voxel_id)
        median_fitness = self._median_fitness(structural_label)

        voxel.update(
            params=self.params,
            c_self=c_self,
            c_neighbors=neighbor_counts,
            covered=covered,
            patch_fitness=patch_fitness,
            median_fitness=median_fitness,
        )

        if structural_label is not None and patch_fitness is not None:
            self.fitness_history.setdefault(structural_label, []).append(patch_fitness)

        return voxel.state, voxel.confidence_flag

    def get_state(self, voxel_id: str) -> Tuple[State, ConfidenceFlag]:
        if voxel_id not in self.voxels:
            return State.NEVER_OBSERVED, ConfidenceFlag.PENDING
        v = self.voxels[voxel_id]
        return v.state, v.confidence_flag

    def get_p_occ(self, voxel_id: str) -> float:
        if voxel_id not in self.voxels:
            return 0.5
        return self.voxels[voxel_id].p_occ()

    def get_kappa(self, voxel_id: str) -> float:
        if voxel_id not in self.voxels:
            return 0.0
        return self.voxels[voxel_id].kappa(self.params.n0)

    def get_mu(self, voxel_id: str) -> float:
        if voxel_id not in self.voxels:
            return 0.5
        return self.voxels[voxel_id].mu(self.params.n0_mu)

    def summary(self) -> Dict[str, dict]:
        """全ボクセルの状態一覧を、辞書形式で返す(デバッグ・出力用)。"""
        result = {}
        for vid, v in self.voxels.items():
            result[vid] = {
                "state": v.state.value,
                "confidence_flag": v.confidence_flag.value,
                "p_occ": round(v.p_occ(), 4),
                "kappa": round(v.kappa(self.params.n0), 4),
                "mu": round(v.mu(self.params.n0_mu), 4),
                "n_obs": v.n_obs,
                "n_mu": round(v.alpha_mu + v.beta_mu, 4),
            }
        return result
