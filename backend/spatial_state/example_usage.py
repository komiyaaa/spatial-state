"""
SpatialStateTracker の使用例。

実行方法(/home/claude ディレクトリから):
    python3 -m spatial_state.example_usage

実際のパイプラインに組み込む際は、以下の3点を、実データから供給する
処理に置き換える:
  1. c_self, neighbor_counts: ボクセルグリッド構築処理から得られる、
     このスキャンでの点数
  2. covered: 近傍のヒット密度から判定する、カバー範囲判定処理の結果
  3. patch_fitness: VGICPの位置合わせ結果から得られる fitness_score
"""
from .params import Params
from .tracker import SpatialStateTracker


def main():
    params = Params(
        c_sat=10.0,
        W=6,
        r_pool=1,
        n0_mu=3.0,
        M_min=2,
        gamma=0.8,
        n0=3.0,
        kappa_th=0.5,
        N_fit_min=15,
    )
    tracker = SpatialStateTracker(params)

    print("=== シナリオ: 壁面ボクセルが、10回の観測の後、撤去される ===\n")

    for t in range(10):
        state, flag = tracker.update_voxel(
            voxel_id="v_wall_001",
            c_self=25,
            neighbor_ids=["v_wall_000", "v_wall_002"],
            neighbor_counts=[22, 20],
            covered=True,
            patch_fitness=0.028,
            structural_label="wall",
        )
        print(
            f"t={t+1:2d}: state={state.value:14s} flag={flag.value:9s} "
            f"P_occ={tracker.get_p_occ('v_wall_001'):.3f} "
            f"kappa={tracker.get_kappa('v_wall_001'):.3f}"
        )

    print()
    print("--- ここで、壁が撤去されたと仮定する ---")
    print()

    for t in range(8):
        state, flag = tracker.update_voxel(
            voxel_id="v_wall_001",
            c_self=0,
            neighbor_ids=["v_wall_000", "v_wall_002"],
            neighbor_counts=[0, 0],
            covered=True,
            patch_fitness=0.028,
            structural_label="wall",
        )
        print(
            f"t={t+11:2d}: state={state.value:14s} flag={flag.value:9s} "
            f"P_occ={tracker.get_p_occ('v_wall_001'):.3f} "
            f"kappa={tracker.get_kappa('v_wall_001'):.3f}"
        )

    print()
    print("=== 全ボクセルのサマリー ===")
    for vid, info in tracker.summary().items():
        print(f"  {vid}: {info}")


if __name__ == "__main__":
    main()
