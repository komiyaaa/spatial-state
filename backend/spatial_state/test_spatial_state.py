"""
これまで stage1_formalization_v3.md で検算してきた、主要な性質が
実装でも再現されることを確認するテスト。

実行方法(/home/claude ディレクトリから):
    python3 -m spatial_state.test_spatial_state
"""
from .params import Params
from .tracker import SpatialStateTracker
from .voxel import State, ConfidenceFlag


def make_tracker(**overrides):
    defaults = dict(gamma=0.8, n0=3.0, kappa_th=0.5, n0_mu=3.0, M_min=2, W=6, r_pool=1,
                     expected_n_neighbors_for_validation=0)
    defaults.update(overrides)
    return SpatialStateTracker(Params(**defaults))


def test_stable_object_confirms_occupied():
    """§2.6: ずっと安定した物体は、OCCUPIEDとして確定するはず"""
    tracker = make_tracker()
    for _ in range(10):
        state, flag = tracker.update_voxel(
            "v1", c_self=25, neighbor_ids=[], neighbor_counts=[], covered=True
        )
    assert state == State.OCCUPIED
    assert flag == ConfidenceFlag.CONFIRMED
    assert tracker.get_p_occ("v1") > 0.9
    print("test_stable_object_confirms_occupied: OK")


def test_single_miss_does_not_cause_false_alarm():
    """§2.3.1: 1回だけの測定ミスは、負の証拠を発生させないはず(M_min=2ゲート)"""
    tracker = make_tracker()
    for _ in range(10):
        tracker.update_voxel("v1", c_self=25, neighbor_ids=[], neighbor_counts=[], covered=True)
    p_before = tracker.get_p_occ("v1")

    tracker.update_voxel("v1", c_self=0, neighbor_ids=[], neighbor_counts=[], covered=True)
    p_after = tracker.get_p_occ("v1")

    assert abs(p_before - p_after) < 0.01, f"1回のミスでP_occが動いた: {p_before} -> {p_after}"
    print(f"test_single_miss_does_not_cause_false_alarm: OK (P_occ {p_before:.4f} -> {p_after:.4f})")


def test_genuine_removal_is_detected():
    """§2.6: 本当に撤去された物体は、連続ミスを重ねると正しくDECAYEDになるはず"""
    tracker = make_tracker()
    for _ in range(10):
        tracker.update_voxel("v1", c_self=25, neighbor_ids=[], neighbor_counts=[], covered=True)
    assert tracker.get_state("v1")[0] == State.OCCUPIED

    for _ in range(10):
        state, flag = tracker.update_voxel(
            "v1", c_self=0, neighbor_ids=[], neighbor_counts=[], covered=True
        )

    assert state == State.DECAYED, f"撤去が検出されなかった: state={state}"
    print(f"test_genuine_removal_is_detected: OK (最終P_occ={tracker.get_p_occ('v1'):.4f})")


def test_never_observed_voxel_stays_neutral():
    """§2.1.4・2.1.5: 一度もヒットしないボクセルは、近傍が濃くても中立のまま"""
    tracker = make_tracker()
    for _ in range(15):
        state, flag = tracker.update_voxel(
            "v1", c_self=0, neighbor_ids=["v_neighbor"], neighbor_counts=[25], covered=True
        )
    p = tracker.get_p_occ("v1")
    assert p < 0.6, f"一度も証拠が無いのに、P_occが育ってしまった: {p}"
    print(f"test_never_observed_voxel_stays_neutral: OK (P_occ={p:.4f})")


def test_dynamic_object_mu_lower_than_static():
    """§2.2・§5.6: 頻繁に出たり消えたりする物体は、muが静的な物体より低くなるはず

    注意: n0=3.0(デフォルト)だと、5回ヒット/5回ミスの振動パターンでは、
    kappaが0.478付近で頭打ちになりkappa_thに届かず、状態が一度もCONFIRMEDに
    ならない(§9の理論制約と整合する、正しい挙動)。このテストでは、確定が
    実際に起きるよう、n0をやや小さく設定する。
    """
    tracker = make_tracker(n0_mu=1.5, n0=1.2, kappa_th=0.4)

    for _ in range(20):
        tracker.update_voxel("v_static", c_self=25, neighbor_ids=[], neighbor_counts=[], covered=True)

    pattern = ([25] * 5 + [0] * 5) * 2
    for c in pattern:
        tracker.update_voxel("v_dynamic", c_self=c, neighbor_ids=[], neighbor_counts=[], covered=True)

    mu_static = tracker.get_mu("v_static")
    mu_dynamic = tracker.get_mu("v_dynamic")

    assert mu_static > mu_dynamic, f"静的の方がmuが低い: static={mu_static}, dynamic={mu_dynamic}"
    print(f"test_dynamic_object_mu_lower_than_static: OK (static_mu={mu_static:.3f}, dynamic_mu={mu_dynamic:.3f})")


def test_bad_parameter_combination_raises_error():
    """§5.3.1: 理論制約を破るパラメータは、生成時にエラーになるはず"""
    try:
        Params(gamma=0.8, n0=8.0, kappa_th=0.5)
        assert False, "エラーが発生しなかった(検出漏れ)"
    except ValueError as e:
        assert "致命的なパラメータ不整合" in str(e)
        print("test_bad_parameter_combination_raises_error: OK")


def test_peak_detection_preserves_weaker_independent_object():
    """§実験12: 近接する、独立した弱い物体も、ピーク検出で潰されないはず"""
    tracker = make_tracker(r_pool=2)
    for _ in range(15):
        tracker.update_voxel(
            "v_weak", c_self=10, neighbor_ids=["v_strong"], neighbor_counts=[25], covered=True
        )
    p = tracker.get_p_occ("v_weak")
    assert p > 0.8, f"弱い方の独立した物体が、不当に抑制された: P_occ={p}"
    print(f"test_peak_detection_preserves_weaker_independent_object: OK (P_occ={p:.4f})")


if __name__ == "__main__":
    test_stable_object_confirms_occupied()
    test_single_miss_does_not_cause_false_alarm()
    test_genuine_removal_is_detected()
    test_never_observed_voxel_stays_neutral()
    test_dynamic_object_mu_lower_than_static()
    test_bad_parameter_combination_raises_error()
    test_peak_detection_preserves_weaker_independent_object()
    print()
    print("全テスト成功。")
