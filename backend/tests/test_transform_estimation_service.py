"""
backend/services/transform_estimation_service.py の動作確認テスト
(ロードマップPhase 3.2: Local↔Local Nodal correspondenceからの
RigidTransform2D推定)。

テスト用のCoordinateDefinitionは、非常に細かいvoxel_size(1e-9m)を使うことで、
任意の実数座標を(idx+0.5)*voxel_sizeの格子点として(浮動小数点誤差程度の
精度で)表現できるようにしている。これにより、resolve_local_center()を
実際に経由しながら、既知のRigidTransform2Dで作った理想的な対応点集合を
テストに使える(座標の丸め誤差は5e-10m未満で、テストの許容誤差より
十分小さい)。

実行方法(リポジトリルートから):
    python backend/tests/test_transform_estimation_service.py
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.nodal_connection import SolutionStatus  # noqa: E402
from domain.transform import RigidTransform2D  # noqa: E402
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402
from services.transform_estimation_service import (  # noqa: E402
    LocalCorrespondencePoint,
    estimate_local_to_local_transform,
)
from spatial_id.local_spatial_id import LocalSpatialIdResolver  # noqa: E402

_TOL = 1e-6
_VOXEL_SIZE = 1e-9


def _make_coordinate_definition(zoom: str = "0", voxel_size: float = _VOXEL_SIZE) -> dict:
    """resolve_local_centerはunit-sizeしか見ないため、origin/rad等は
    プレースホルダで構わない(意図的にorigin/radを無視できることの前提)。"""
    return {
        "id": "test",
        "degree": 0.0,
        "rad": 0.0,
        "height": 1.0,
        "origin": [0.0, 0.0, 0.0],
        "unit-size": {zoom: voxel_size},
        "bounds": [[0.0, 0.0, 0.0] for _ in range(8)],
    }


def _id_for_point(zoom: str, point, voxel_size: float = _VOXEL_SIZE) -> str:
    x, y, z = point
    x_idx = round(x / voxel_size - 0.5)
    y_idx = round(y / voxel_size - 0.5)
    f_idx = round(z / voxel_size - 0.5)
    return f"{zoom}/{f_idx}/{x_idx}/{y_idx}"


def _add_space(repo, space_def_dir, building_id, tokutei_code, zoom="0", voxel_size=_VOXEL_SIZE) -> str:
    space_def = _make_coordinate_definition(zoom=zoom, voxel_size=voxel_size)
    (space_def_dir / f"{tokutei_code}.json").write_text(json.dumps(space_def), encoding="utf-8")
    repo.create(building_id=building_id, tokutei_code=tokutei_code, floor=1, zoom_level=0)
    return f"{building_id}-{tokutei_code}"


def _make_repo_and_resolver(tmp):
    registry_dir = Path(tmp) / "registry"
    space_def_dir = Path(tmp) / "space_definitions"
    space_def_dir.mkdir()
    repo = LocalSpaceRepository(registry_dir, space_def_dir)
    return repo, space_def_dir, LocalSpatialIdResolver(repo)


def _correspondences_from_points(space_a, points_a, zoom_a, vsize_a, space_b, points_b, zoom_b, vsize_b):
    ids_a = [_id_for_point(zoom_a, p, vsize_a) for p in points_a]
    ids_b = [_id_for_point(zoom_b, p, vsize_b) for p in points_b]
    return [
        (LocalCorrespondencePoint(space_a, ida), LocalCorrespondencePoint(space_b, idb))
        for ida, idb in zip(ids_a, ids_b)
    ]


def test_two_points_recovers_known_yaw_and_translation():
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        space_b = _add_space(repo, space_def_dir, "b1", "B")

        known = RigidTransform2D(yaw_rad=math.pi / 2, translation=(3.0, 4.0, 1.0))
        points_a = [(0.1, 0.2, 0.0), (2.5, 0.7, 0.0)]
        points_b = [known.apply(p) for p in points_a]

        correspondences = _correspondences_from_points(
            space_a, points_a, "0", _VOXEL_SIZE, space_b, points_b, "0", _VOXEL_SIZE
        )
        solution = estimate_local_to_local_transform(resolver, correspondences)

        assert solution.status == SolutionStatus.SOLVED
        assert math.isclose(solution.yaw_rad, known.yaw_rad, abs_tol=1e-4)
        for a, b in zip(solution.translation, known.translation):
            assert math.isclose(a, b, abs_tol=1e-4)
        assert solution.rmse_m < 1e-4
    print("test_two_points_recovers_known_yaw_and_translation: OK")


def test_three_or_more_points_best_fit_exact():
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        space_b = _add_space(repo, space_def_dir, "b1", "B")

        known = RigidTransform2D(yaw_rad=0.6109, translation=(-2.0, 5.5, -0.3))  # 35度程度、綺麗な角度でない
        points_a = [(0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.0, 2.0, 0.0), (1.5, 1.5, 0.5)]
        points_b = [known.apply(p) for p in points_a]

        correspondences = _correspondences_from_points(
            space_a, points_a, "0", _VOXEL_SIZE, space_b, points_b, "0", _VOXEL_SIZE
        )
        solution = estimate_local_to_local_transform(resolver, correspondences)

        assert solution.status == SolutionStatus.SOLVED
        assert solution.n_correspondences == 4
        assert math.isclose(solution.yaw_rad, known.yaw_rad, abs_tol=1e-4)
        for a, b in zip(solution.translation, known.translation):
            assert math.isclose(a, b, abs_tol=1e-4)
        assert solution.rmse_m < 1e-4
        assert len(solution.residuals) == 4
    print("test_three_or_more_points_best_fit_exact: OK")


def test_noisy_correspondences_best_fit_close_to_true_transform():
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        space_b = _add_space(repo, space_def_dir, "b1", "B")

        rng = np.random.default_rng(7)
        known = RigidTransform2D(yaw_rad=0.4, translation=(1.0, -1.0, 0.2))
        points_a = [(x, y, 0.0) for x, y in rng.uniform(-5, 5, size=(8, 2))]
        noise = rng.normal(scale=0.01, size=(8, 3))  # 1cm程度のノイズ
        points_b = [
            tuple(np.array(known.apply(p)) + n) for p, n in zip(points_a, noise)
        ]

        correspondences = _correspondences_from_points(
            space_a, points_a, "0", _VOXEL_SIZE, space_b, points_b, "0", _VOXEL_SIZE
        )
        solution = estimate_local_to_local_transform(resolver, correspondences)

        assert solution.status == SolutionStatus.SOLVED
        # ノイズがあるためRMSEは非ゼロだが、ノイズ水準(1cm程度)と同程度に収まるはず
        assert 0.0 < solution.rmse_m < 0.03
        assert math.isclose(solution.yaw_rad, known.yaw_rad, abs_tol=0.02)
        assert math.isclose(solution.translation[0], known.translation[0], abs_tol=0.02)
        assert math.isclose(solution.translation[1], known.translation[1], abs_tol=0.02)
    print("test_noisy_correspondences_best_fit_close_to_true_transform: OK")


def test_different_zoom_levels_between_endpoints():
    """異なるzoom level同士のcorrespondenceを許可する既存設計の確認:
    space A側はzoom "0"(粗いvoxel_size)、space B側はzoom "5"(細かいvoxel_size)
    のIDでも、正しく推定できる。"""
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A", zoom="0", voxel_size=_VOXEL_SIZE)
        space_b = _add_space(repo, space_def_dir, "b1", "B", zoom="5", voxel_size=_VOXEL_SIZE * 2)

        known = RigidTransform2D(yaw_rad=-0.9, translation=(10.0, -3.0, 0.0))
        points_a = [(0.5, 0.5, 0.0), (4.0, 1.0, 0.0), (2.0, 3.5, 0.0)]
        points_b = [known.apply(p) for p in points_a]

        correspondences = _correspondences_from_points(
            space_a, points_a, "0", _VOXEL_SIZE, space_b, points_b, "5", _VOXEL_SIZE * 2
        )
        solution = estimate_local_to_local_transform(resolver, correspondences)

        assert solution.status == SolutionStatus.SOLVED
        assert math.isclose(solution.yaw_rad, known.yaw_rad, abs_tol=1e-3)
        for a, b in zip(solution.translation, known.translation):
            assert math.isclose(a, b, abs_tol=1e-3)
    print("test_different_zoom_levels_between_endpoints: OK")


def test_same_id_string_different_space_uses_resolved_coordinates():
    """local_spatial_id文字列をspace間で直接比較しない設計の確認:
    space A・Bで(意図的に)全く同じID文字列を使った対応点ペアを1つ混ぜても、
    それぞれ自分自身のCoordinateDefinition(voxel_sizeが異なる)で解決した
    物理座標が使われ、文字列としての一致には一切影響されないことを確認する。
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, resolver = _make_repo_and_resolver(tmp)
        vsize_a = _VOXEL_SIZE
        vsize_b = _VOXEL_SIZE * 3.0  # 意図的に異なるvoxel_size
        space_a = _add_space(repo, space_def_dir, "b1", "A", zoom="0", voxel_size=vsize_a)
        space_b = _add_space(repo, space_def_dir, "b1", "B", zoom="0", voxel_size=vsize_b)

        # まず、文字列としては同一だが、voxel_sizeが異なるため物理座標としては
        # 異なる点に解決されることを確認する(resolverレベルの前提の再確認)。
        shared_str = "0/10/20/30"
        resolved_a = resolver.resolve_local_center(space_a, shared_str)
        resolved_b = resolver.resolve_local_center(space_b, shared_str)
        assert resolved_a != resolved_b, "voxel_sizeが異なるのに同じ物理座標に解決されてしまった"

        # 正しい対応点(既知transformで関係付けた、別々の文字列)を複数用意し、
        # そこに「文字列だけ偶然一致するが、意味的には無関係な」ペアを1つ
        # 混ぜても、全体の推定がresolveされた座標に基づいて行われ、文字列の
        # 一致によって挙動が変わらないことを確認する。
        known = RigidTransform2D(yaw_rad=1.1, translation=(0.5, 0.5, 0.0))
        points_a = [(0.2, 0.3, 0.0), (1.7, 0.4, 0.0), (0.9, 2.1, 0.0), (3.3, 1.1, 0.0)]
        points_b = [known.apply(p) for p in points_a]
        ids_a = [_id_for_point("0", p, vsize_a) for p in points_a]
        ids_b = [_id_for_point("0", p, vsize_b) for p in points_b]

        correspondences = [
            (LocalCorrespondencePoint(space_a, a), LocalCorrespondencePoint(space_b, b))
            for a, b in zip(ids_a, ids_b)
        ]
        # 文字列だけが偶然一致する、意味的には無関係な1ペアを追加混入させる
        correspondences.append(
            (LocalCorrespondencePoint(space_a, shared_str), LocalCorrespondencePoint(space_b, shared_str))
        )

        solution = estimate_local_to_local_transform(resolver, correspondences)

        # 5点中4点は正しいtransformに従うため、外れ値が1つ混ざっても
        # best-fitのyaw/translationは既知transformに近い値になるはず
        # (文字列一致に引きずられて全く違う結果にはならないことの確認)。
        assert solution.status in (SolutionStatus.SOLVED, SolutionStatus.WARNING_HIGH_RESIDUAL)
        assert math.isclose(solution.yaw_rad, known.yaw_rad, abs_tol=0.1)
        assert math.isclose(solution.translation[0], known.translation[0], abs_tol=0.5)
        assert math.isclose(solution.translation[1], known.translation[1], abs_tol=0.5)
    print("test_same_id_string_different_space_uses_resolved_coordinates: OK")


def test_reflection_input_is_not_adopted():
    """reflection(鏡映)関係にある対応点集合を与えても、出力されるtransformは
    常にdet(R)=+1の真の回転のみ(RigidTransform2DのAPI自体がreflectionを
    表現できない)。回転だけでは説明できないため、RMSEが明確に大きくなる
    ことを確認する(reflectionを「こっそり採用」していないことの証拠)。"""
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        space_b = _add_space(repo, space_def_dir, "b1", "B")

        points_a = [(0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.0, 2.0, 0.0), (2.0, 3.0, 0.0)]
        # y軸反転(reflection、det=-1)。回転+並進では厳密に再現できない関係。
        points_b = [(x, -y, 0.0) for x, y, _ in points_a]

        correspondences = _correspondences_from_points(
            space_a, points_a, "0", _VOXEL_SIZE, space_b, points_b, "0", _VOXEL_SIZE
        )
        solution = estimate_local_to_local_transform(resolver, correspondences)

        # RigidTransform2D自体がreflectionを表現できないため、解が出た場合は
        # 必ず回転のみ。ここではRMSEが明確に非ゼロ(=reflectionを再現できて
        # いない)ことを確認する。
        assert solution.status in (SolutionStatus.SOLVED, SolutionStatus.WARNING_HIGH_RESIDUAL)
        assert solution.rmse_m > 0.5, (
            f"reflection関係の入力なのにRMSEが小さすぎる(reflectionを採用してしまった疑い): {solution.rmse_m}"
        )
    print("test_reflection_input_is_not_adopted: OK")


def test_single_correspondence_is_unsolvable():
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        space_b = _add_space(repo, space_def_dir, "b1", "B")

        correspondences = [
            (
                LocalCorrespondencePoint(space_a, _id_for_point("0", (1.0, 1.0, 0.0))),
                LocalCorrespondencePoint(space_b, _id_for_point("0", (5.0, 5.0, 0.0))),
            )
        ]
        solution = estimate_local_to_local_transform(resolver, correspondences)

        assert solution.status == SolutionStatus.UNSOLVABLE
        assert solution.n_correspondences == 1
        assert solution.yaw_rad is None
        assert solution.translation is None
    print("test_single_correspondence_is_unsolvable: OK")


def test_zero_correspondences_is_unsolvable():
    with tempfile.TemporaryDirectory() as tmp:
        repo, _space_def_dir, resolver = _make_repo_and_resolver(tmp)
        solution = estimate_local_to_local_transform(resolver, [])
        assert solution.status == SolutionStatus.UNSOLVABLE
        assert solution.n_correspondences == 0
    print("test_zero_correspondences_is_unsolvable: OK")


def test_degenerate_coincident_points_is_unsolvable():
    """2点以上あっても、全対応点がほぼ同一点に潰れている場合は回転が
    数値的に不定なためUNSOLVABLEになることを確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        space_b = _add_space(repo, space_def_dir, "b1", "B")

        same_point_a = (1.0, 1.0, 0.0)
        same_point_b = (5.0, 5.0, 0.0)
        correspondences = [
            (
                LocalCorrespondencePoint(space_a, _id_for_point("0", same_point_a)),
                LocalCorrespondencePoint(space_b, _id_for_point("0", same_point_b)),
            )
            for _ in range(3)
        ]
        solution = estimate_local_to_local_transform(resolver, correspondences)
        assert solution.status == SolutionStatus.UNSOLVABLE
    print("test_degenerate_coincident_points_is_unsolvable: OK")


if __name__ == "__main__":
    test_two_points_recovers_known_yaw_and_translation()
    test_three_or_more_points_best_fit_exact()
    test_noisy_correspondences_best_fit_close_to_true_transform()
    test_different_zoom_levels_between_endpoints()
    test_same_id_string_different_space_uses_resolved_coordinates()
    test_reflection_input_is_not_adopted()
    test_single_correspondence_is_unsolvable()
    test_zero_correspondences_is_unsolvable()
    test_degenerate_coincident_points_is_unsolvable()
    print()
    print("全テスト成功。")
