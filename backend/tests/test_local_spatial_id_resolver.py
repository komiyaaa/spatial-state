"""
backend/spatial_id/local_spatial_id.py の動作確認テスト(ロードマップPhase 3.1、
2026-09-02に座標系1/2の分離を反映)。

このモジュールが解決するのは2つの異なる座標系であることに注意
(モジュールdocstring参照):
  1. intrinsic local physical coordinate(resolve_local_center) —
     ((x+0.5)s, (y+0.5)s, (f+0.5)s)。origin/rotationは使わない。
     Nodal correspondence / RigidTransform2D推定専用。
  2. provisional/world coordinate(resolve_provisional_world_center) —
     1にorigin/rotationを適用し、Base Mapと同じ座標系へ戻したもの。
     Viewer専用。

resolve_center()はresolve_provisional_world_center()の非推奨エイリアス
(Viewer互換のためだけに残っている)。

実データのregistry(backend/data/registry/)には一切触れず、一時ディレクトリ上に
独立したLocalSpaceRepositoryを構築して使う。

実行方法(リポジトリルートから):
    python backend/tests/test_local_spatial_id_resolver.py
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
from point_to_spatial_id import world_points_to_spatial_ids  # noqa: E402
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402
from space_definition_generator import finest_zoom_level, generate_space_definition  # noqa: E402
from spatial_id.local_spatial_id import LocalSpatialIdResolver, resolve_local_center  # noqa: E402

_TOL = 1e-9


def _make_room_points(sx=10.0, sy=4.0, sz=3.0, nx=6, ny=6, nz=4) -> np.ndarray:
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    return np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)


def _build_repo_with_space(tmp, building_id, tokutei_code, space_def) -> LocalSpaceRepository:
    """1つのLocal Spaceだけを持つLocalSpaceRepositoryを一時ディレクトリ上に作る。
    既存の複数spaceを1つのrepoにまとめたい場合はadd_space()を使う。"""
    space_def_dir = Path(tmp) / "space_definitions"
    space_def_dir.mkdir(exist_ok=True)
    registry_dir = Path(tmp) / "registry"
    repo = LocalSpaceRepository(registry_dir, space_def_dir)
    add_space(repo, space_def_dir, building_id, tokutei_code, space_def)
    return repo


def add_space(repo: LocalSpaceRepository, space_def_dir: Path, building_id, tokutei_code, space_def) -> str:
    (space_def_dir / f"{tokutei_code}.json").write_text(
        json.dumps(space_def, ensure_ascii=False), encoding="utf-8"
    )
    repo.create(building_id=building_id, tokutei_code=tokutei_code, floor=1, zoom_level=0)
    return f"{building_id}-{tokutei_code}"


# ============================================================
# 座標系2: provisional/world coordinate(resolve_provisional_world_center)
# Viewer専用。既存のvoxel_center挙動(Step 1〜4)と同じ数式。
# ============================================================

def test_world_center_finest_is_grid_center_at_3cm():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        finest = finest_zoom_level(space_def)
        voxel_size = space_def["unit-size"][str(finest)]
        assert math.isclose(voxel_size, 0.03, abs_tol=_TOL)

        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        center = resolver.resolve_provisional_world_center("b1-R1", f"{finest}/0/0/0")
        # forward変換の回転式(det=-1、y成分が反転する仕様。point_to_spatial_id.py参照)
        # により、theta=0でもrel_y = -local_y になる(rel_x = local_xのみ素直に一致)。
        origin = space_def["origin"]
        expected = (origin[0] + voxel_size / 2, origin[1] - voxel_size / 2, origin[2] + voxel_size / 2)
        for a, b in zip(center, expected):
            assert math.isclose(a, b, abs_tol=1e-6)
    print("test_world_center_finest_is_grid_center_at_3cm: OK")


def test_world_center_upper_zoom_uses_that_zooms_voxel_size():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        finest = finest_zoom_level(space_def)
        upper = finest - 1
        upper_voxel_size = space_def["unit-size"][str(upper)]
        assert upper_voxel_size != space_def["unit-size"][str(finest)]

        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        center = resolver.resolve_provisional_world_center("b1-R1", f"{upper}/2/3/1")
        # theta=0でもrel_y = -local_yになる仕様(上のtest参照)
        origin = space_def["origin"]
        expected = (
            origin[0] + (3 + 0.5) * upper_voxel_size,
            origin[1] - (1 + 0.5) * upper_voxel_size,
            origin[2] + (2 + 0.5) * upper_voxel_size,
        )
        for a, b in zip(center, expected):
            assert math.isclose(a, b, abs_tol=1e-6)
    print("test_world_center_upper_zoom_uses_that_zooms_voxel_size: OK")


def test_world_center_adjacent_interval_equals_voxel_size():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.3)
        finest = finest_zoom_level(space_def)
        voxel_size = space_def["unit-size"][str(finest)]

        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        c0 = resolver.resolve_provisional_world_center("b1-R1", f"{finest}/5/5/5")
        c1 = resolver.resolve_provisional_world_center("b1-R1", f"{finest}/5/6/5")  # x方向に1つ隣
        dist = math.dist(c0, c1)
        assert math.isclose(dist, voxel_size, abs_tol=1e-6), f"隣接voxel間隔がvoxel_sizeと一致しない: {dist}"
    print("test_world_center_adjacent_interval_equals_voxel_size: OK")


def test_world_center_rotation_is_applied():
    with tempfile.TemporaryDirectory() as tmp:
        space_def_flat = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        space_def_rot = generate_space_definition(_make_room_points(), "R2", rotation_rad=math.pi / 6)
        finest = finest_zoom_level(space_def_flat)

        registry_dir = Path(tmp) / "registry"
        space_def_dir = Path(tmp) / "space_definitions"
        space_def_dir.mkdir()
        repo = LocalSpaceRepository(registry_dir, space_def_dir)
        add_space(repo, space_def_dir, "b1", "R1", space_def_flat)
        add_space(repo, space_def_dir, "b1", "R2", space_def_rot)
        resolver = LocalSpatialIdResolver(repo)

        c_flat = resolver.resolve_provisional_world_center("b1-R1", f"{finest}/2/4/1")
        c_rot = resolver.resolve_provisional_world_center("b1-R2", f"{finest}/2/4/1")
        assert not math.isclose(c_flat[0], c_rot[0], abs_tol=1e-6) or not math.isclose(c_flat[1], c_rot[1], abs_tol=1e-6), (
            "rotation_radが異なるのに、水平座標が一致してしまった(回転が適用されていない)"
        )
    print("test_world_center_rotation_is_applied: OK")


def test_forward_id_world_center_forward_round_trip():
    """world座標 → (forward)ID → resolve_provisional_world_center → center →
    (forward再適用)ID が元と同じIDに戻ることを確認する(既存forward
    conventionとの整合性)。round-tripにはworld座標が必要なため、座標系2
    (provisional/world coordinate)でのみ成立する検証。"""
    with tempfile.TemporaryDirectory() as tmp:
        for i, rotation_rad in enumerate((0.0, 0.3, -1.1, math.pi / 2)):
            trial_dir = Path(tmp) / str(i)
            trial_dir.mkdir()
            space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=rotation_rad)
            finest = finest_zoom_level(space_def)
            repo = _build_repo_with_space(trial_dir, "b1", "R1", space_def)
            resolver = LocalSpatialIdResolver(repo)

            points = _make_room_points()[::7]  # 間引いて代表点をいくつか使う
            original_ids = world_points_to_spatial_ids(points, space_def, finest)

            centers = np.array([resolver.resolve_provisional_world_center("b1-R1", sid) for sid in original_ids])
            round_trip_ids = world_points_to_spatial_ids(centers, space_def, finest)

            assert original_ids == round_trip_ids, (
                f"round-trip不整合(rotation_rad={rotation_rad}): {original_ids} != {round_trip_ids}"
            )
    print("test_forward_id_world_center_forward_round_trip: OK")


def test_world_center_same_id_string_different_space_id_resolves_differently():
    with tempfile.TemporaryDirectory() as tmp:
        space_def_a = generate_space_definition(_make_room_points(sx=10.0), "R1", rotation_rad=0.0)
        space_def_b = generate_space_definition(_make_room_points(sx=20.0, sy=8.0), "R2", rotation_rad=0.4)

        registry_dir = Path(tmp) / "registry"
        space_def_dir = Path(tmp) / "space_definitions"
        space_def_dir.mkdir()
        repo = LocalSpaceRepository(registry_dir, space_def_dir)
        add_space(repo, space_def_dir, "b1", "R1", space_def_a)
        add_space(repo, space_def_dir, "b1", "R2", space_def_b)
        resolver = LocalSpatialIdResolver(repo)

        shared_id_str = "0/0/0/0"  # 両方のspace defに存在するzoom level(0)を使う
        assert "0" in space_def_a["unit-size"] and "0" in space_def_b["unit-size"]

        center_a = resolver.resolve_provisional_world_center("b1-R1", shared_id_str)
        center_b = resolver.resolve_provisional_world_center("b1-R2", shared_id_str)
        assert center_a != center_b, "同じID文字列が別space_idで同じ物理座標に解決されてしまった"
    print("test_world_center_same_id_string_different_space_id_resolves_differently: OK")


def test_world_center_uses_owning_local_spaces_coordinate_definition_only():
    """resolverが、指定されたspace_id自身のCoordinateDefinitionだけを使い、
    他のLocal Spaceのoriginへフォールバック・混入しないことを確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def_a = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        space_def_b = generate_space_definition(_make_room_points(), "R2", rotation_rad=0.0)
        # 意図的にoriginをずらし、a/bを明確に区別できるようにする
        space_def_b["origin"] = [space_def_b["origin"][0] + 100.0, space_def_b["origin"][1], space_def_b["origin"][2]]
        finest = finest_zoom_level(space_def_a)

        registry_dir = Path(tmp) / "registry"
        space_def_dir = Path(tmp) / "space_definitions"
        space_def_dir.mkdir()
        repo = LocalSpaceRepository(registry_dir, space_def_dir)
        add_space(repo, space_def_dir, "b1", "R1", space_def_a)
        add_space(repo, space_def_dir, "b1", "R2", space_def_b)
        resolver = LocalSpatialIdResolver(repo)

        center_a = resolver.resolve_provisional_world_center("b1-R1", f"{finest}/0/0/0")
        center_b = resolver.resolve_provisional_world_center("b1-R2", f"{finest}/0/0/0")
        assert math.isclose(center_b[0] - center_a[0], 100.0, abs_tol=1e-6), (
            "space_id 'b1-R2' の解決が、自分自身のoriginを使っていない"
        )
    print("test_world_center_uses_owning_local_spaces_coordinate_definition_only: OK")


def test_resolve_center_alias_matches_provisional_world_center():
    """resolve_center()(非推奨エイリアス)がresolve_provisional_world_center()と
    完全に同じ値を返すこと(Viewer互換が壊れていないこと)を確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.5)
        finest = finest_zoom_level(space_def)
        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        for sid in (f"{finest}/0/0/0", f"{finest}/3/2/1", f"{finest - 1}/1/0/0"):
            via_alias = resolver.resolve_center("b1-R1", sid)
            via_named = resolver.resolve_provisional_world_center("b1-R1", sid)
            assert via_alias == via_named, f"resolve_center()とresolve_provisional_world_center()が食い違う: {sid}"
    print("test_resolve_center_alias_matches_provisional_world_center: OK")


# ============================================================
# 座標系1: intrinsic local physical coordinate(resolve_local_center)
# Nodal correspondence / RigidTransform2D推定専用。origin/rotationは不使用。
# ============================================================

def test_local_center_finest_is_pure_grid_center_no_origin():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.7)
        finest = finest_zoom_level(space_def)
        voxel_size = space_def["unit-size"][str(finest)]

        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        center = resolver.resolve_local_center("b1-R1", f"{finest}/0/0/0")
        expected = (voxel_size / 2, voxel_size / 2, voxel_size / 2)
        for a, b in zip(center, expected):
            assert math.isclose(a, b, abs_tol=1e-9), "origin/rotationが混入している(座標系1はこれらを使わないはず)"
    print("test_local_center_finest_is_pure_grid_center_no_origin: OK")


def test_local_center_upper_zoom_uses_that_zooms_voxel_size():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        finest = finest_zoom_level(space_def)
        upper = finest - 1
        upper_voxel_size = space_def["unit-size"][str(upper)]

        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        center = resolver.resolve_local_center("b1-R1", f"{upper}/2/3/1")
        expected = ((3 + 0.5) * upper_voxel_size, (1 + 0.5) * upper_voxel_size, (2 + 0.5) * upper_voxel_size)
        for a, b in zip(center, expected):
            assert math.isclose(a, b, abs_tol=1e-9)
    print("test_local_center_upper_zoom_uses_that_zooms_voxel_size: OK")


def test_local_center_adjacent_interval_equals_voxel_size():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.9)
        finest = finest_zoom_level(space_def)
        voxel_size = space_def["unit-size"][str(finest)]

        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        c0 = resolver.resolve_local_center("b1-R1", f"{finest}/5/5/5")
        c1 = resolver.resolve_local_center("b1-R1", f"{finest}/5/6/5")
        dist = math.dist(c0, c1)
        assert math.isclose(dist, voxel_size, abs_tol=1e-9)
    print("test_local_center_adjacent_interval_equals_voxel_size: OK")


def test_local_center_ignores_origin_and_rotation():
    """同じunit-size・同じIDでも、origin/radだけが異なる2つのCoordinateDefinition
    から、resolve_local_center()が全く同じ値を返すこと(座標系1はorigin/radを
    一切使わないことの直接証拠、ユーザー指示: 2026-09-02)。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def_a = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        space_def_b = json.loads(json.dumps(space_def_a))  # 深いコピー
        space_def_b["origin"] = [space_def_b["origin"][0] + 999.0, space_def_b["origin"][1] - 42.0, space_def_b["origin"][2] + 7.0]
        space_def_b["rad"] = space_def_b["rad"] + math.pi / 3
        finest = finest_zoom_level(space_def_a)

        registry_dir = Path(tmp) / "registry"
        space_def_dir = Path(tmp) / "space_definitions"
        space_def_dir.mkdir()
        repo = LocalSpaceRepository(registry_dir, space_def_dir)
        add_space(repo, space_def_dir, "b1", "R1", space_def_a)
        add_space(repo, space_def_dir, "b1", "R2", space_def_b)
        resolver = LocalSpatialIdResolver(repo)

        sid = f"{finest}/4/3/2"
        center_a = resolver.resolve_local_center("b1-R1", sid)
        center_b = resolver.resolve_local_center("b1-R2", sid)
        assert center_a == center_b, (
            f"origin/radが異なるのにresolve_local_center()の結果が変わった: {center_a} != {center_b}"
        )
    print("test_local_center_ignores_origin_and_rotation: OK")


def test_local_center_does_not_require_origin_or_rad_keys():
    """resolve_local_center()(free function)は、origin/radキーが存在しない
    coordinate_definitionでも解決できること(構造的にorigin/radへ依存して
    いないことの証拠)。"""
    coordinate_definition = {"unit-size": {"9": 0.1, "8": 0.2}}  # originもradも無い
    center = resolve_local_center("9/1/2/3", coordinate_definition)
    assert center == [(2 + 0.5) * 0.1, (3 + 0.5) * 0.1, (1 + 0.5) * 0.1]
    print("test_local_center_does_not_require_origin_or_rad_keys: OK")


def test_local_center_same_id_different_unit_size_resolves_differently():
    """異なるLocal Space同士で同一ID文字列を比較しない設計であることの確認:
    各space_idは常に自分自身のunit-sizeで解決するため、同じID文字列でも
    unit-sizeが異なれば座標系1でも結果が異なる。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def_a = generate_space_definition(_make_room_points(sx=10.0), "R1", rotation_rad=0.0)
        space_def_b = generate_space_definition(_make_room_points(sx=200.0, sy=100.0, sz=50.0), "R2", rotation_rad=0.0)

        registry_dir = Path(tmp) / "registry"
        space_def_dir = Path(tmp) / "space_definitions"
        space_def_dir.mkdir()
        repo = LocalSpaceRepository(registry_dir, space_def_dir)
        add_space(repo, space_def_dir, "b1", "R1", space_def_a)
        add_space(repo, space_def_dir, "b1", "R2", space_def_b)
        resolver = LocalSpatialIdResolver(repo)

        shared_id_str = "0/0/0/0"
        assert space_def_a["unit-size"]["0"] != space_def_b["unit-size"]["0"], "テスト前提(unit-sizeの違い)が崩れている"

        center_a = resolver.resolve_local_center("b1-R1", shared_id_str)
        center_b = resolver.resolve_local_center("b1-R2", shared_id_str)
        assert center_a != center_b
    print("test_local_center_same_id_different_unit_size_resolves_differently: OK")


def test_local_center_allows_mixed_zoom_correspondence():
    """異なるzoom level同士のcorrespondenceを許可する既存設計の確認:
    同一space_id内で、finestと上位zoomのIDを両方とも問題なく解決できる
    (どちらの側にもzoomを揃えることを強制する仕組みが無い)。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        finest = finest_zoom_level(space_def)
        upper = finest - 2
        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        p_finest = resolver.resolve_local_center("b1-R1", f"{finest}/0/0/0")
        p_upper = resolver.resolve_local_center("b1-R1", f"{upper}/0/0/0")
        assert p_finest != p_upper  # 例外にならず、それぞれ独立に解決できる
    print("test_local_center_allows_mixed_zoom_correspondence: OK")


def test_local_center_invalid_zoom_level_raises():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        invalid_zoom = max(int(k) for k in space_def["unit-size"]) + 50
        try:
            resolver.resolve_local_center("b1-R1", f"{invalid_zoom}/0/0/0")
            raise AssertionError("存在しないzoom levelが解決できてしまった")
        except ValueError:
            pass
    print("test_local_center_invalid_zoom_level_raises: OK")


def test_local_center_invalid_local_spatial_id_string_raises():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)

        for bad_id in ("not-an-id", "9/0/0", "9/0/0/0/0", "9/a/0/0", ""):
            try:
                resolver.resolve_local_center("b1-R1", bad_id)
                raise AssertionError(f"不正なlocal_spatial_idが解決できてしまった: {bad_id!r}")
            except ValueError:
                pass
    print("test_local_center_invalid_local_spatial_id_string_raises: OK")


def test_unknown_space_id_raises_for_both_coordinate_systems():
    with tempfile.TemporaryDirectory() as tmp:
        space_def = generate_space_definition(_make_room_points(), "R1", rotation_rad=0.0)
        repo = _build_repo_with_space(tmp, "b1", "R1", space_def)
        resolver = LocalSpatialIdResolver(repo)
        for method in (resolver.resolve_local_center, resolver.resolve_provisional_world_center, resolver.resolve_center):
            try:
                method("nonexistent-space", "9/0/0/0")
                raise AssertionError(f"存在しないspace_idが{method}で解決できてしまった")
            except ValueError:
                pass
    print("test_unknown_space_id_raises_for_both_coordinate_systems: OK")


def test_different_hierarchy_depths_per_local_space():
    """Local Spaceごとにunit-sizeの段数(zoom level数)が独立でよいことを確認する
    (小さい部屋 = 段数が少ない、大きい部屋 = 段数が多い)。座標系1・2どちらでも成立。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def_small = generate_space_definition(_make_room_points(sx=1.0, sy=1.0, sz=1.0), "SMALL", rotation_rad=0.0)
        space_def_large = generate_space_definition(_make_room_points(sx=200.0, sy=100.0, sz=50.0), "LARGE", rotation_rad=0.0)
        depth_small = len(space_def_small["unit-size"])
        depth_large = len(space_def_large["unit-size"])
        assert depth_small != depth_large, "テスト前提が崩れている(段数が偶然同じ)"

        registry_dir = Path(tmp) / "registry"
        space_def_dir = Path(tmp) / "space_definitions"
        space_def_dir.mkdir()
        repo = LocalSpaceRepository(registry_dir, space_def_dir)
        add_space(repo, space_def_dir, "b1", "SMALL", space_def_small)
        add_space(repo, space_def_dir, "b1", "LARGE", space_def_large)
        resolver = LocalSpatialIdResolver(repo)

        finest_small = finest_zoom_level(space_def_small)
        finest_large = finest_zoom_level(space_def_large)
        resolver.resolve_local_center("b1-SMALL", f"{finest_small}/0/0/0")
        resolver.resolve_local_center("b1-LARGE", f"{finest_large}/0/0/0")

        deep_zoom_only_in_large = max(int(k) for k in space_def_large["unit-size"]) - min(
            int(k) for k in space_def_small["unit-size"]
        )
        if str(deep_zoom_only_in_large) not in space_def_small["unit-size"]:
            try:
                resolver.resolve_local_center("b1-SMALL", f"{deep_zoom_only_in_large}/0/0/0")
                raise AssertionError("SMALL側に存在しないzoom levelが解決できてしまった")
            except ValueError:
                pass
    print("test_different_hierarchy_depths_per_local_space: OK")


if __name__ == "__main__":
    # 座標系2: provisional/world coordinate(Viewer専用)
    test_world_center_finest_is_grid_center_at_3cm()
    test_world_center_upper_zoom_uses_that_zooms_voxel_size()
    test_world_center_adjacent_interval_equals_voxel_size()
    test_world_center_rotation_is_applied()
    test_forward_id_world_center_forward_round_trip()
    test_world_center_same_id_string_different_space_id_resolves_differently()
    test_world_center_uses_owning_local_spaces_coordinate_definition_only()
    test_resolve_center_alias_matches_provisional_world_center()
    # 座標系1: intrinsic local physical coordinate(Nodal correspondence専用)
    test_local_center_finest_is_pure_grid_center_no_origin()
    test_local_center_upper_zoom_uses_that_zooms_voxel_size()
    test_local_center_adjacent_interval_equals_voxel_size()
    test_local_center_ignores_origin_and_rotation()
    test_local_center_does_not_require_origin_or_rad_keys()
    test_local_center_same_id_different_unit_size_resolves_differently()
    test_local_center_allows_mixed_zoom_correspondence()
    test_local_center_invalid_zoom_level_raises()
    test_local_center_invalid_local_spatial_id_string_raises()
    # 共通
    test_unknown_space_id_raises_for_both_coordinate_systems()
    test_different_hierarchy_depths_per_local_space()
    print()
    print("全テスト成功。")
