"""
段階1本体のパラメータ定義。

参照: stage1_formalization_v3.md 全体
"""
from dataclasses import dataclass
import warnings


@dataclass
class Params:
    """段階1本体の全パラメータ。

    各パラメータの意味・較正方針は、stage1_formalization_v3.md の
    対応する節を参照。
    """

    # --- 柱1: 点数信頼度(§2.1) ---
    c_sat: float = 10.0  # 信頼度が頭打ちになる点数

    # --- 柱3: 時間窓+近傍プーリング(§2.1) ---
    W: int = 6  # 時間窓の幅(直近何回分を見るか)
    r_pool: int = 1  # 近傍プーリングの半径(ボクセル単位)

    # --- カバー範囲判定(§2.3) ---
    h_cov: int = 1  # 「カバーされている」とみなす近傍ヒット数の閾値

    # --- 柱2: 動的度合い(§2.2、§2.2.1で投票箱方式に刷新) ---
    gamma_mu: float = 0.9  # mu専用の忘却係数(§2.2.1)
    n0_mu: float = 3.0  # kappa_muが0.5に達する仮想観測回数(旧N_minを刷新、§2.2.1)

    # --- 連続ミスのゲート(§2.3.1) ---
    M_min: int = 2  # 連続ミスがこの回数に達するまで、負の証拠を発生させない

    # --- ベータ分布の更新(§2.4) ---
    gamma: float = 0.8  # 忘却係数(0 < gamma <= 1)

    # --- 確信度(§2.4) ---
    n0: float = 0.5  # 確信度が0.5に達する仮想観測回数
    # 【重要】3次元の近傍プーリング(6近傍想定)による希釈を考慮した値。
    # 単純な1次元検算(近傍0個)ではn0=3.0でも問題なかったが、実際の3次元
    # パイプラインでは、孤立した物体のkappaの天井が下がるため、この値が
    # 必要になった(validate()のdocstring参照)。
    kappa_th: float = 0.5  # 状態確定に必要な確信度の閾値

    # --- 占有判定の閾値(§3) ---
    p_th: float = 0.5  # P_occがこれ以上ならOCCUPIED候補

    # --- パッチ品質(w_fit、§2.7) ---
    N_fit_min: int = 15  # fitness_scoreの相場が信頼できる最低蓄積パッチ数

    # --- 検証用: 実際の3次元近傍数(理論制約チェックに使う) ---
    # 3次元でr_pool=1の面隣接なら6、全26方向なら26。孤立した物体では
    # 近傍が永久に空のままになりうるため、この数を考慮しないと、
    # n0の理論制約(§5.3.1)が甘すぎる値を許してしまう(詳細はvalidate()参照)。
    expected_n_neighbors_for_validation: int = 6

    def __post_init__(self):
        self.validate()

    def validate(self):
        """パラメータ間の理論制約を検証する。

        参照: stage1_formalization_v4.md §5.3.1(および、実データパイプライン
        構築時に見つかった、近傍プーリングによる希釈の考慮を追加)。

        継続的にヒットし続けた場合でも、kappaには理論上の天井がある。
        §5.3.1の元の式は、s3が最大1.0まで到達できる(=近傍プーリングに
        よる希釈が無い)ことを前提にしていたが、**実際には、孤立した
        物体は近傍が永久に空のままになりうるため、s3の天井は
        1/(1+n_neighbors)に留まる。** これを踏まえた、より厳しい制約は:

            s3_cap = 1/(1+n_neighbors)
            kappa_max = s3_cap / (s3_cap + n0*(1-gamma))
            → n0 < (1-kappa_th) / ((1+n_neighbors)*kappa_th*(1-gamma))
        """
        if not (0 < self.gamma <= 1):
            raise ValueError(f"gamma は (0, 1] の範囲である必要がある: {self.gamma}")
        if not (0 < self.kappa_th < 1):
            raise ValueError(f"kappa_th は (0, 1) の範囲である必要がある: {self.kappa_th}")
        if self.n0 <= 0:
            raise ValueError(f"n0 は正の値である必要がある: {self.n0}")

        n_neighbors = self.expected_n_neighbors_for_validation
        s3_cap = 1.0 / (1 + n_neighbors)
        kappa_max = s3_cap / (s3_cap + self.n0 * (1.0 - self.gamma)) if self.gamma < 1 else 1.0
        n0_upper_bound = (
            (1 - self.kappa_th) / ((1 + n_neighbors) * self.kappa_th * (1 - self.gamma))
            if self.gamma < 1 else float("inf")
        )

        if kappa_max < self.kappa_th:
            raise ValueError(
                f"【致命的なパラメータ不整合】孤立した物体(近傍{n_neighbors}個が"
                f"永久に空のケース)を想定すると、理論上のkappaの天井({kappa_max:.4f})が"
                f"kappa_th({self.kappa_th})を下回っている。この設定では、孤立した物体は"
                f"どれだけ観測を重ねても、永久にCONFIRMEDへ到達できない\n"
                f"  現在: gamma={self.gamma}, n0={self.n0}, kappa_th={self.kappa_th}, "
                f"想定近傍数={n_neighbors}\n"
                f"  n0 は {n0_upper_bound:.4f} 未満にする必要がある(この設定のもとで)。"
            )

        margin = kappa_max - self.kappa_th
        if margin < 0.05:
            warnings.warn(
                f"kappa の理論上の天井({kappa_max:.4f}、近傍{n_neighbors}個の孤立物体を想定)が"
                f"kappa_th({self.kappa_th})に近い(余裕={margin:.4f})。"
                f"n0 を {n0_upper_bound*0.7:.4f} 程度まで下げることを推奨する。"
            )

    def n0_upper_bound(self, n_neighbors: int = None) -> float:
        """現在の gamma, kappa_th のもとで、n0が満たすべき上限を返す。

        n_neighbors: 省略時は expected_n_neighbors_for_validation を使う。
        """
        if self.gamma >= 1:
            return float("inf")
        n = n_neighbors if n_neighbors is not None else self.expected_n_neighbors_for_validation
        return (1 - self.kappa_th) / ((1 + n) * self.kappa_th * (1 - self.gamma))
