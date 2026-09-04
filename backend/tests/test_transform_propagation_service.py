"""
backend/services/transform_propagation_service.py の動作確認テスト
(ロードマップPhase 3.3: connected component内の相対配置伝播)。

NodalConnection.solutionは、Phase 3.2(transform_estimation_service)が
既に計算済みという前提で、直接ConnectionSolutionを組み立てて与える
(NodalEndpoint/Correspondenceの解決自体はこのテストの対象外)。

実行方法(リポジトリルートから):
    python backend/tests/test_transform_propagation_service.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.component_placement import ComponentStatus  # noqa: E402
from domain.nodal_connection import (  # noqa: E402
    ConnectionEndpointRef,
    ConnectionEndpointType,
    ConnectionSolution,
    NodalConnection,
    SolutionStatus,
)
from domain.transform import RigidTransform2D, compose  # noqa: E402
from services.transform_propagation_service import resolve_component_placements  # noqa: E402

_TOL = 1e-6


def _local_ref(space_id: str) -> ConnectionEndpointRef:
    return ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_id)


def _solved_connection(connection_id, space_a, space_b, transform: RigidTransform2D) -> NodalConnection:
    """space_a -> space_b のRigidTransform2Dを持つ、SOLVED状態のNodalConnection。"""
    return NodalConnection(
        connection_id=connection_id,
        building_id="b1",
        endpoint_space_a=_local_ref(space_a),
        endpoint_space_b=_local_ref(space_b),
        correspondences=[],
        solution=ConnectionSolution(
            status=SolutionStatus.SOLVED,
            n_correspondences=2,
            yaw_rad=transform.yaw_rad,
            translation=list(transform.translation),
            rmse_m=0.001,
            max_residual_m=0.002,
            residuals=[0.001, 0.001],
        ),
    )


def _unsolvable_connection(connection_id, space_a, space_b) -> NodalConnection:
    return NodalConnection(
        connection_id=connection_id,
        building_id="b1",
        endpoint_space_a=_local_ref(space_a),
        endpoint_space_b=_local_ref(space_b),
        correspondences=[],
        solution=ConnectionSolution(status=SolutionStatus.UNSOLVABLE, n_correspondences=1),
    )


def _find_component(results, space_id):
    for r in results:
        if space_id in r.member_space_ids:
            return r
    return None


def test_two_spaces_directly_connected():
    space_a, space_b = "b1-A", "b1-B"
    known = RigidTransform2D(yaw_rad=math.pi / 4, translation=(1.0, 2.0, 0.0))
    connections = [_solved_connection("c1", space_a, space_b, known)]

    results = resolve_component_placements(connections)
    assert len(results) == 1
    result = results[0]
    assert set(result.member_space_ids) == {space_a, space_b}
    assert result.root_space_id == space_a  # 辞書順最小
    assert result.status == ComponentStatus.RESOLVED
    assert result.conflicts == []

    assert result.transforms[space_a].yaw_rad == 0.0
    assert result.transforms[space_a].translation == (0.0, 0.0, 0.0)

    # known は T_A_to_B。root=Aなので、transforms[B] は T_B_to_A(= known.inverse())になるはず。
    expected_b = known.inverse()
    assert math.isclose(result.transforms[space_b].yaw_rad, expected_b.yaw_rad, abs_tol=_TOL)
    for a, b in zip(result.transforms[space_b].translation, expected_b.translation):
        assert math.isclose(a, b, abs_tol=_TOL)
    print("test_two_spaces_directly_connected: OK")


def test_chain_a_b_c_physical_point_round_trip():
    """A-B-C-chain(A→B→Cの向きに直列)。物理的に同一の点(p_A)が、各空間の
    相対配置を経由してroot(=A)フレームに正しく戻ることを、実際の点の
    往復で確認する(compose()の内部式を直接再現せず、意味的な正しさで検証)。
    """
    space_a, space_b, space_c = "b1-A", "b1-B", "b1-C"
    t_a_to_b = RigidTransform2D(yaw_rad=0.3, translation=(2.0, -1.0, 0.5))
    t_b_to_c = RigidTransform2D(yaw_rad=-0.8, translation=(-3.0, 4.0, -0.2))
    connections = [
        _solved_connection("c_ab", space_a, space_b, t_a_to_b),
        _solved_connection("c_bc", space_b, space_c, t_b_to_c),
    ]

    results = resolve_component_placements(connections)
    assert len(results) == 1
    result = results[0]
    assert set(result.member_space_ids) == {space_a, space_b, space_c}
    assert result.root_space_id == space_a
    assert result.status == ComponentStatus.RESOLVED

    p_a = (1.0, 1.0, 1.0)  # space A自身のintrinsic local座標での適当な点
    p_b = t_a_to_b.apply(p_a)  # 同じ物理点をspace Bの座標で表したもの
    p_c = t_b_to_c.apply(p_b)  # 同じ物理点をspace Cの座標で表したもの

    p_a_via_b = result.transforms[space_b].apply(p_b)
    p_a_via_c = result.transforms[space_c].apply(p_c)
    for x, y in zip(p_a_via_b, p_a):
        assert math.isclose(x, y, abs_tol=_TOL)
    for x, y in zip(p_a_via_c, p_a):
        assert math.isclose(x, y, abs_tol=_TOL)
    print("test_chain_a_b_c_physical_point_round_trip: OK")


def test_reversed_edge_direction_still_round_trips():
    """B-C辺の宣言を逆向き(endpoint_a=C, endpoint_b=B、solution=T_C_to_B)に
    しても、正しくinverse()が使われ、同じ物理的な正しさが得られることを確認
    する(「edge方向が逆の場合はinverseを使う」の直接確認)。"""
    space_a, space_b, space_c = "b1-A", "b1-B", "b1-C"
    t_a_to_b = RigidTransform2D(yaw_rad=0.3, translation=(2.0, -1.0, 0.5))
    t_b_to_c = RigidTransform2D(yaw_rad=-0.8, translation=(-3.0, 4.0, -0.2))
    t_c_to_b = t_b_to_c.inverse()  # 意図的に逆向きの宣言にする
    connections = [
        _solved_connection("c_ab", space_a, space_b, t_a_to_b),
        _solved_connection("c_cb_reversed", space_c, space_b, t_c_to_b),  # a=C, b=B
    ]

    results = resolve_component_placements(connections)
    result = results[0]
    assert result.status == ComponentStatus.RESOLVED

    p_a = (1.0, 1.0, 1.0)
    p_b = t_a_to_b.apply(p_a)
    p_c = t_c_to_b.inverse().apply(p_b)  # = t_b_to_c.apply(p_b)、同じ物理点のC表現

    p_a_via_c = result.transforms[space_c].apply(p_c)
    for x, y in zip(p_a_via_c, p_a):
        assert math.isclose(x, y, abs_tol=_TOL)
    print("test_reversed_edge_direction_still_round_trips: OK")


def test_multiple_disconnected_components_are_independent():
    known1 = RigidTransform2D(yaw_rad=0.1, translation=(1.0, 0.0, 0.0))
    known2 = RigidTransform2D(yaw_rad=1.2, translation=(-2.0, 5.0, 0.0))
    connections = [
        _solved_connection("c1", "b1-A", "b1-B", known1),
        _solved_connection("c2", "b1-X", "b1-Y", known2),
    ]

    results = resolve_component_placements(connections)
    assert len(results) == 2

    comp_ab = _find_component(results, "b1-A")
    comp_xy = _find_component(results, "b1-X")
    assert comp_ab is not comp_xy
    assert set(comp_ab.member_space_ids) == {"b1-A", "b1-B"}
    assert set(comp_xy.member_space_ids) == {"b1-X", "b1-Y"}
    assert comp_ab.status == ComponentStatus.RESOLVED
    assert comp_xy.status == ComponentStatus.RESOLVED
    print("test_multiple_disconnected_components_are_independent: OK")


def test_anchorless_component_resolves_successfully():
    """Global anchor(Local↔GLOBALの接続)が一切無い、Local↔Localのみの
    component でも、正常にRESOLVEDとして相対配置できることを確認する。"""
    known = RigidTransform2D(yaw_rad=0.5, translation=(3.0, 3.0, 0.0))
    connections = [_solved_connection("c1", "b1-A", "b1-B", known)]
    results = resolve_component_placements(connections)
    assert len(results) == 1
    assert results[0].status == ComponentStatus.RESOLVED
    assert results[0].root_space_id == "b1-A"
    print("test_anchorless_component_resolves_successfully: OK")


def _make_consistent_triangle():
    """A/B/Cそれぞれの「自分自身の座標系 -> 共通の仮想参照フレーム」への
    transform(G_A=identity, G_B, G_C)を先に決め、そこから3辺すべてを
    逆算する。この作り方自体が、3辺が互いに矛盾しないことを保証する。"""
    g_a = RigidTransform2D.identity()
    g_b = RigidTransform2D(yaw_rad=0.3, translation=(2.0, 1.0, 0.0))
    g_c = RigidTransform2D(yaw_rad=-0.5, translation=(-1.0, 3.0, 0.0))

    t_a_to_b = compose(g_b.inverse(), g_a)
    t_b_to_c = compose(g_c.inverse(), g_b)
    t_c_to_a = compose(g_a.inverse(), g_c)
    return g_a, g_b, g_c, t_a_to_b, t_b_to_c, t_c_to_a


def test_consistent_cycle_resolves_without_conflict():
    space_a, space_b, space_c = "b1-A", "b1-B", "b1-C"
    _g_a, g_b, g_c, t_a_to_b, t_b_to_c, t_c_to_a = _make_consistent_triangle()

    connections = [
        _solved_connection("c_ab", space_a, space_b, t_a_to_b),
        _solved_connection("c_bc", space_b, space_c, t_b_to_c),
        _solved_connection("c_ca", space_c, space_a, t_c_to_a),  # 非tree辺(cycle)になる想定
    ]

    results = resolve_component_placements(connections)
    assert len(results) == 1
    result = results[0]
    assert result.status == ComponentStatus.RESOLVED
    assert result.conflicts == []

    # rootはA。transforms[B]はT_B_to_A(=g_b)、transforms[C]はT_C_to_A(=g_c)と一致するはず。
    assert math.isclose(result.transforms[space_b].yaw_rad, g_b.yaw_rad, abs_tol=_TOL)
    for a, b in zip(result.transforms[space_b].translation, g_b.translation):
        assert math.isclose(a, b, abs_tol=_TOL)
    assert math.isclose(result.transforms[space_c].yaw_rad, g_c.yaw_rad, abs_tol=_TOL)
    for a, b in zip(result.transforms[space_c].translation, g_c.translation):
        assert math.isclose(a, b, abs_tol=_TOL)
    print("test_consistent_cycle_resolves_without_conflict: OK")


def test_inconsistent_cycle_causes_conflict_without_silently_picking_one():
    space_a, space_b, space_c = "b1-A", "b1-B", "b1-C"
    _g_a, g_b, g_c, t_a_to_b, t_b_to_c, t_c_to_a = _make_consistent_triangle()

    # B-C辺だけ意図的に大きくずらし、cycleを不整合にする。
    broken_t_b_to_c = RigidTransform2D(
        yaw_rad=t_b_to_c.yaw_rad + 0.3,
        translation=(t_b_to_c.translation[0] + 1.0, t_b_to_c.translation[1], t_b_to_c.translation[2]),
    )

    connections = [
        _solved_connection("c_ab", space_a, space_b, t_a_to_b),
        _solved_connection("c_bc_broken", space_b, space_c, broken_t_b_to_c),
        _solved_connection("c_ca", space_c, space_a, t_c_to_a),
    ]

    results = resolve_component_placements(connections)
    assert len(results) == 1
    result = results[0]
    assert result.status == ComponentStatus.CONFLICT
    assert len(result.conflicts) >= 1

    conflict = result.conflicts[0]
    assert conflict.connection_id == "c_bc_broken"
    assert conflict.space_id == space_c
    assert conflict.yaw_diff_rad > 0.01 or conflict.translation_diff_m > 0.05

    # 矛盾があっても、どちらかを勝手に採用せず、spanning tree由来の値
    # (T_C_to_A = g_c)を保持したままであることを確認する。
    assert math.isclose(result.transforms[space_c].yaw_rad, g_c.yaw_rad, abs_tol=_TOL)
    print("test_inconsistent_cycle_causes_conflict_without_silently_picking_one: OK")


def test_unsolvable_edge_is_excluded_from_propagation():
    """A-BがUNSOLVABLE、B-CがSOLVEDの場合、Aはどのcomponentにも含まれず、
    {B, C}だけのcomponentが1つ得られることを確認する。"""
    known = RigidTransform2D(yaw_rad=0.2, translation=(1.0, 1.0, 0.0))
    connections = [
        _unsolvable_connection("c_ab_bad", "b1-A", "b1-B"),
        _solved_connection("c_bc", "b1-B", "b1-C", known),
    ]

    results = resolve_component_placements(connections)
    assert len(results) == 1
    result = results[0]
    assert set(result.member_space_ids) == {"b1-B", "b1-C"}
    assert "b1-A" not in result.member_space_ids
    assert _find_component(results, "b1-A") is None
    print("test_unsolvable_edge_is_excluded_from_propagation: OK")


if __name__ == "__main__":
    test_two_spaces_directly_connected()
    test_chain_a_b_c_physical_point_round_trip()
    test_reversed_edge_direction_still_round_trips()
    test_multiple_disconnected_components_are_independent()
    test_anchorless_component_resolves_successfully()
    test_consistent_cycle_resolves_without_conflict()
    test_inconsistent_cycle_causes_conflict_without_silently_picking_one()
    test_unsolvable_edge_is_excluded_from_propagation()
    print()
    print("全テスト成功。")
