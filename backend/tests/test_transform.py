"""
backend/domain/transform.py の動作確認テスト。

実行方法(リポジトリルートから):
    python backend/tests/test_transform.py

規約(固定、backend/domain/transform.py のdocstring参照):
    T_A_to_B.apply(p_A) == p_B
    compose(outer, inner).apply(p) == outer.apply(inner.apply(p))
    すなわち T_A_to_C = compose(T_B_to_C, T_A_to_B)
"""
from __future__ import annotations

import math
import sys
from dataclasses import fields
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.transform import RigidTransform2D, compose  # noqa: E402

_TOL = 1e-9


def _assert_point_close(actual, expected, msg=""):
    for a, e in zip(actual, expected):
        assert math.isclose(a, e, abs_tol=_TOL), f"{msg}: {actual} != {expected}"


def _assert_transform_close(actual: RigidTransform2D, expected: RigidTransform2D, msg=""):
    assert math.isclose(actual.yaw_rad % (2 * math.pi), expected.yaw_rad % (2 * math.pi), abs_tol=1e-6) or (
        math.isclose(math.sin(actual.yaw_rad), math.sin(expected.yaw_rad), abs_tol=_TOL)
        and math.isclose(math.cos(actual.yaw_rad), math.cos(expected.yaw_rad), abs_tol=_TOL)
    ), f"{msg} yaw: {actual.yaw_rad} != {expected.yaw_rad}"
    _assert_point_close(actual.translation, expected.translation, msg + " translation")


def test_identity():
    t = RigidTransform2D.identity()
    p = (1.5, -2.5, 3.0)
    _assert_point_close(t.apply(p), p, "identity")
    assert t.yaw_rad == 0.0
    assert t.translation == (0.0, 0.0, 0.0)
    print("test_identity: OK")


def test_translation_only():
    t = RigidTransform2D(yaw_rad=0.0, translation=(10.0, -5.0, 2.0))
    _assert_point_close(t.apply((1.0, 2.0, 3.0)), (11.0, -3.0, 5.0), "translation_only")
    print("test_translation_only: OK")


def test_plus_90_degree_yaw():
    t = RigidTransform2D(yaw_rad=math.pi / 2, translation=(0.0, 0.0, 0.0))
    # 反時計回り正: (1,0,0) -> (0,1,0)
    _assert_point_close(t.apply((1.0, 0.0, 0.0)), (0.0, 1.0, 0.0), "+90deg on (1,0,0)")
    _assert_point_close(t.apply((0.0, 1.0, 0.0)), (-1.0, 0.0, 0.0), "+90deg on (0,1,0)")
    print("test_plus_90_degree_yaw: OK")


def test_minus_90_degree_yaw():
    t = RigidTransform2D(yaw_rad=-math.pi / 2, translation=(0.0, 0.0, 0.0))
    _assert_point_close(t.apply((1.0, 0.0, 0.0)), (0.0, -1.0, 0.0), "-90deg on (1,0,0)")
    _assert_point_close(t.apply((0.0, 1.0, 0.0)), (1.0, 0.0, 0.0), "-90deg on (0,1,0)")
    print("test_minus_90_degree_yaw: OK")


def test_yaw_plus_translation():
    t = RigidTransform2D(yaw_rad=math.pi / 2, translation=(5.0, 7.0, 0.0))
    # R(90deg) @ (1,0) = (0,1) -> + (5,7) = (5,8)
    _assert_point_close(t.apply((1.0, 0.0, 0.0)), (5.0, 8.0, 0.0), "yaw+translation")
    print("test_yaw_plus_translation: OK")


def test_inverse_round_trip():
    t = RigidTransform2D(yaw_rad=0.7, translation=(3.0, -4.0, 1.5))
    p = (2.0, -1.0, 0.5)
    q = t.apply(p)
    _assert_point_close(t.inverse().apply(q), p, "inverse round-trip (forward then back)")
    _assert_point_close(t.apply(t.inverse().apply(q)), q, "inverse round-trip (back then forward)")
    print("test_inverse_round_trip: OK")


def test_compose_two_transforms():
    # T_A_to_B: 90度回転, T_B_to_C: 並進のみ
    t_a_to_b = RigidTransform2D(yaw_rad=math.pi / 2, translation=(0.0, 0.0, 0.0))
    t_b_to_c = RigidTransform2D(yaw_rad=0.0, translation=(10.0, 0.0, 0.0))
    t_a_to_c = compose(t_b_to_c, t_a_to_b)

    p_a = (1.0, 0.0, 0.0)
    expected = t_b_to_c.apply(t_a_to_b.apply(p_a))
    _assert_point_close(t_a_to_c.apply(p_a), expected, "compose 2 transforms")
    print("test_compose_two_transforms: OK")


def test_compose_three_transforms():
    t1 = RigidTransform2D(yaw_rad=0.3, translation=(1.0, 2.0, 0.5))
    t2 = RigidTransform2D(yaw_rad=-1.1, translation=(-3.0, 4.0, -0.5))
    t3 = RigidTransform2D(yaw_rad=2.0, translation=(0.5, -0.5, 1.0))

    p = (1.3, -2.7, 0.9)

    # 結合性: compose(compose(t3,t2), t1) == compose(t3, compose(t2,t1))
    left = compose(compose(t3, t2), t1)
    right = compose(t3, compose(t2, t1))
    _assert_point_close(left.apply(p), right.apply(p), "compose associativity")

    # 直接適用した場合と一致すること
    direct = t3.apply(t2.apply(t1.apply(p)))
    _assert_point_close(left.apply(p), direct, "compose 3 transforms vs direct application")
    print("test_compose_three_transforms: OK")


def test_compose_with_inverse_is_identity():
    t = RigidTransform2D(yaw_rad=1.234, translation=(5.0, -6.0, 7.0))
    identity_like_1 = compose(t, t.inverse())
    identity_like_2 = compose(t.inverse(), t)
    p = (3.0, -1.0, 2.0)
    _assert_point_close(identity_like_1.apply(p), p, "compose(T, T.inverse())")
    _assert_point_close(identity_like_2.apply(p), p, "compose(T.inverse(), T)")
    print("test_compose_with_inverse_is_identity: OK")


def test_z_translation_only():
    t = RigidTransform2D(yaw_rad=math.pi / 3, translation=(0.0, 0.0, 9.0))
    x, y, z = t.apply((1.0, 1.0, 1.0))
    assert math.isclose(z, 10.0, abs_tol=_TOL), f"z translation failed: {z}"
    print("test_z_translation_only: OK")


def test_apply_does_not_mutate_input():
    p = (1.0, 2.0, 3.0)
    original = tuple(p)
    t = RigidTransform2D(yaw_rad=0.5, translation=(1.0, 1.0, 1.0))
    t.apply(p)
    assert p == original, "apply()が入力pointを変更してしまった"
    print("test_apply_does_not_mutate_input: OK")


def test_yaw_beyond_two_pi():
    base_yaw = 0.9
    t1 = RigidTransform2D(yaw_rad=base_yaw, translation=(2.0, -1.0, 0.0))
    t2 = RigidTransform2D(yaw_rad=base_yaw + 4 * math.pi, translation=(2.0, -1.0, 0.0))
    p = (3.0, 4.0, 5.0)
    _assert_point_close(t1.apply(p), t2.apply(p), "yaw beyond 2pi should behave identically")
    print("test_yaw_beyond_two_pi: OK")


def test_no_scale_or_reflection_api():
    field_names = {f.name for f in fields(RigidTransform2D)}
    assert field_names == {"yaw_rad", "translation"}, f"想定外のフィールドが存在する: {field_names}"

    try:
        RigidTransform2D(yaw_rad=0.0, translation=(0.0, 0.0, 0.0), scale=2.0)  # type: ignore[call-arg]
        raise AssertionError("scale引数が受理されてしまった")
    except TypeError:
        pass

    try:
        RigidTransform2D(yaw_rad=0.0, translation=(0.0, 0.0, 0.0), reflect=True)  # type: ignore[call-arg]
        raise AssertionError("reflect引数が受理されてしまった")
    except TypeError:
        pass

    print("test_no_scale_or_reflection_api: OK")


if __name__ == "__main__":
    test_identity()
    test_translation_only()
    test_plus_90_degree_yaw()
    test_minus_90_degree_yaw()
    test_yaw_plus_translation()
    test_inverse_round_trip()
    test_compose_two_transforms()
    test_compose_three_transforms()
    test_compose_with_inverse_is_identity()
    test_z_translation_only()
    test_apply_does_not_mutate_input()
    test_yaw_beyond_two_pi()
    test_no_scale_or_reflection_api()
    print()
    print("全テスト成功。")
