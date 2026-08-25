"""
段階1本体の、状態を持たない純粋関数群。

参照: stage1_formalization_v3.md §2.1
"""
import math
from typing import Sequence, Optional


def w_cnt(c: float, c_sat: float) -> float:
    """点数信頼度(柱1)。§2.1

    s1(v,t) = min(1, ln(1+c) / ln(1+c_sat))
    """
    if c <= 0:
        return 0.0
    return min(1.0, math.log(1 + c) / math.log(1 + c_sat))


def is_peak(c_eff_self: float, c_eff_neighbors: Sequence[float]) -> bool:
    """ピーク判定(非極大抑制)。§2.1.4・2.1.5・2.1.6

    近傍の中で、自分の実効生点数が最大(または同点)かどうかを判定する。
    近傍が無い場合は、常にTrue(自分がピーク)とみなす。

    重要(§2.1.6): 比較には、w_cntで頭打ちさせる前の「実効生点数」を使う。
    頭打ち後の値(h_W)を使うと、本物どうしの僅差の比べ合いができなくなる。
    """
    if not c_eff_neighbors:
        return True
    return c_eff_self >= max(c_eff_neighbors)


def h_max(W: int, n_neighbors: int) -> float:
    """s3正規化のための理論上の最大項数。§2.1

    H_max = W * (1 + 近傍数)
    """
    return W * (1 + n_neighbors)


def fitness_weight(patch_fitness: float, median_fitness: Optional[float]) -> float:
    """パッチ品質の重み w_fit。§2.7

    相場(median_fitness)がまだ育っていない(None)場合は、割り引かない。
    相場より明らかに悪ければ w_fit < 1 になる。
    """
    if median_fitness is None:
        return 1.0
    if patch_fitness <= 0:
        return 1.0
    return min(1.0, median_fitness / patch_fitness)


def kappa_theoretical_ceiling(gamma: float, n0: float) -> float:
    """継続的に証拠が入り続けた場合の、kappaの理論上の天井。§5.3.1

    kappa_max = 1 / (1 + n0*(1-gamma))
    """
    if gamma >= 1:
        return 1.0
    return 1.0 / (1.0 + n0 * (1.0 - gamma))
