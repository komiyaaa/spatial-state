"""
1ボクセル分の状態を管理する VoxelState。

参照: stage1_formalization_v3.md §2〜§3(処理フローは§4)
"""
from dataclasses import dataclass, field
from typing import Optional, Sequence
from enum import Enum

from .core import w_cnt, is_peak, h_max, fitness_weight
from .params import Params


class State(str, Enum):
    NEVER_OBSERVED = "NEVER_OBSERVED"
    SCAN_COVERED = "SCAN_COVERED"
    OCCUPIED = "OCCUPIED"
    DECAYED = "DECAYED"


class ConfidenceFlag(str, Enum):
    CONFIRMED = "CONFIRMED"
    PENDING = "PENDING"


@dataclass
class VoxelState:
    """1つのボクセル(空間ID)が持つ、全ての内部状態。

    このクラスは、外部から `update()` を呼ばれることでのみ状態が変わる。
    直接フィールドを書き換えないこと。
    """

    voxel_id: str

    # ベータ分布パラメータ(§2.4)。初期値は構造ラベルの事前分布(§5.4)から
    # 与えることもできる(alpha0, beta0 引数)。
    alpha: float = 0.05
    beta: float = 0.05

    # 直近W回分のヒット履歴(w_cnt適用後の値)。h_W計算に使う(§2.1)。
    hit_history: list = field(default_factory=list)
    # 直近W回分の「実効生点数」履歴。ピーク判定に使う(§2.1.6)。
    raw_history: list = field(default_factory=list)

    # 柱2: 動的度合い関連(§2.2)
    n_obs: int = 0
    # 柱2: mu専用の投票箱(§2.2.1)。旧transition_countは廃止。
    alpha_mu: float = 0.1
    beta_mu: float = 0.1

    # 連続ミスのゲート(§2.3.1)
    miss_streak: int = 0

    # ever_evidenced(§2.1.4): このボクセルが一度でも証拠を持ったか
    ever_evidenced: bool = False
    # P_occがp_thを一度でも超えたことがあるか(確定できたかどうかとは独立に記録)
    was_ever_occupied_candidate: bool = False

    # 最終的な状態
    state: State = State.NEVER_OBSERVED
    confidence_flag: ConfidenceFlag = ConfidenceFlag.PENDING

    def mu(self, n0_mu: float) -> float:
        """柱2: 動的度合い。§2.2.1(投票箱方式)

        mu_raw = alpha_mu / (alpha_mu + beta_mu)
        kappa_mu = n_mu / (n_mu + n0_mu), n_mu = alpha_mu + beta_mu
        mu = kappa_mu * mu_raw + (1 - kappa_mu) * 0.5

        旧版(transition_count/n_obsの単純比率)と異なり、gamma_muによる
        忘却を持つため、過去の誤判定の影響が時間とともに薄れる(§2.2.1で検証)。
        """
        n_mu = self.alpha_mu + self.beta_mu
        mu_raw = self.alpha_mu / n_mu if n_mu > 0 else 0.5
        kappa_mu = n_mu / (n_mu + n0_mu)
        return kappa_mu * mu_raw + (1 - kappa_mu) * 0.5

    def p_occ(self) -> float:
        """存在確率。§2.4

        P_occ = alpha / (alpha + beta)
        """
        total = self.alpha + self.beta
        if total <= 0:
            return 0.5
        return self.alpha / total

    def kappa(self, n0: float) -> float:
        """確信度。§2.4

        kappa = n / (n + n0), n = alpha + beta
        """
        n = self.alpha + self.beta
        return n / (n + n0)

    def update(
        self,
        params: Params,
        c_self: float,
        c_neighbors: Sequence[float],
        covered: bool,
        patch_fitness: Optional[float] = None,
        median_fitness: Optional[float] = None,
    ) -> None:
        """1回分の観測を、このボクセルに反映する。

        処理フローは stage1_formalization_v3.md §4 に対応する。

        Parameters
        ----------
        params : Params
            全パラメータ
        c_self : float
            今回、このボクセルに当たった生の点数
        c_neighbors : Sequence[float]
            近傍ボクセルの、今回の生の点数のリスト(ピーク判定・プーリングに使用)
        covered : bool
            今回、このボクセル周辺が実際にスキャン範囲内だったか
        patch_fitness : Optional[float]
            この観測を提供したパッチの fitness_score(VGICP由来)。
            分からない場合は None(w_fit=1として扱われる)。
        median_fitness : Optional[float]
            同じ構造ラベルの、これまでのパッチのfitness_scoreの中央値
            (SpatialStateTracker側で計算し、渡す)。
        """
        self.n_obs += 1

        # --- w_fitを最初に適用する(§2.7.1) ---
        w_fit = fitness_weight(patch_fitness if patch_fitness is not None else 0.0, median_fitness)
        c_self_eff = w_fit * c_self
        c_neighbors_eff = [w_fit * c for c in c_neighbors]
        # (異なるパッチから来た近傍は、本来は個別のw_fitを掛けるべきだが、
        #  1回のスキャンでは同一パッチ由来であることが多いため、簡易化している)

        # --- s1, s3 を計算する(柱1・柱3、§2.1) ---
        self.raw_history.append(c_self_eff)
        if len(self.raw_history) > params.W:
            self.raw_history.pop(0)

        hit_this_round = w_cnt(c_self_eff, params.c_sat)
        self.hit_history.append(hit_this_round)
        if len(self.hit_history) > params.W:
            self.hit_history.pop(0)

        h_W_self = sum(self.hit_history)

        peak = is_peak(c_self_eff, c_neighbors_eff)
        if peak:
            neighbor_hW_sum = sum(w_cnt(c, params.c_sat) for c in c_neighbors_eff)
            H = h_W_self + neighbor_hW_sum
        else:
            H = h_W_self

        H_max_val = h_max(params.W, len(c_neighbors))
        s3 = min(1.0, H / H_max_val) if H_max_val > 0 else 0.0

        # --- mu(v) を計算する(柱2、§2.2) ---
        mu_val = self.mu(params.n0_mu)

        # --- miss_streak を更新する(§2.3.1) ---
        has_support = (c_self_eff > 0) or (peak and any(c > 0 for c in c_neighbors_eff))
        if has_support:
            self.miss_streak = 0
            self.ever_evidenced = True
        else:
            self.miss_streak += 1

        # --- e+, e- を計算する(§2.3) ---
        if has_support:
            e_plus = s3
            e_minus = 0.0
        else:
            e_plus = 0.0
            if covered and self.miss_streak >= params.M_min:
                e_minus = mu_val * max(0.0, 1.0 - s3)
            else:
                e_minus = 0.0

        # --- alpha, beta を更新する(§2.4) ---
        self.alpha = params.gamma * self.alpha + e_plus
        self.beta = params.gamma * self.beta + e_minus

        # --- 状態を確定する(§3) ---
        p = self.p_occ()
        k = self.kappa(params.n0)

        if self.ever_evidenced:
            if p >= params.p_th:
                candidate = State.OCCUPIED
                self.was_ever_occupied_candidate = True
            else:
                candidate = State.DECAYED if self.was_ever_occupied_candidate else State.SCAN_COVERED
        else:
            candidate = State.NEVER_OBSERVED

        if k >= params.kappa_th:
            new_state = candidate
            new_flag = ConfidenceFlag.CONFIRMED
        else:
            new_state = self.state
            new_flag = ConfidenceFlag.PENDING

        # --- mu専用の投票箱(alpha_mu, beta_mu)を更新する(§2.2.1、双方向、§5.6参照) ---
        transitioned = (self.state == State.OCCUPIED and new_state == State.DECAYED) or (
            self.state == State.DECAYED and new_state == State.OCCUPIED
        )
        e_plus_mu = 0.0 if transitioned else 1.0
        e_minus_mu = 1.0 if transitioned else 0.0
        self.alpha_mu = params.gamma_mu * self.alpha_mu + e_plus_mu
        self.beta_mu = params.gamma_mu * self.beta_mu + e_minus_mu

        self.state = new_state
        self.confidence_flag = new_flag
