"""
backend/repositories/spatial_voxel_cache_repository.py の動作確認テスト
(ロードマップStep 2・Step 3)。一時ディレクトリを使い、実データには一切触れない。

実行方法(リポジトリルートから):
    python backend/tests/test_spatial_voxel_cache_repository.py
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.spatial_voxel import AggregatedSpatialVoxel, SpatialVoxel  # noqa: E402
from repositories.spatial_voxel_cache_repository import SpatialVoxelCacheRepository  # noqa: E402

_TOL = 1e-6


def _make_voxel(sid, center, zoom_level=9, voxel_size=0.03):
    return SpatialVoxel(
        space_id="b1-G001", local_spatial_id=sid, zoom_level=zoom_level, voxel_size=voxel_size,
        point_count=1, voxel_center=list(center),
    )


def _make_aggregated_voxel(sid, center, zoom_level, voxel_size, count):
    return AggregatedSpatialVoxel(
        space_id="b1-G001", local_spatial_id=sid, zoom_level=zoom_level, voxel_size=voxel_size,
        voxel_center=list(center), source_voxel_count=count,
    )


def test_save_and_load_meta_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        voxels = [
            _make_voxel("9/0/0/0", [0.015, 0.015, 0.015]),
            _make_voxel("9/0/1/0", [0.045, 0.015, 0.015]),
            _make_voxel("9/0/0/1", [0.015, 0.045, 0.015]),
        ]
        meta = repo.save("b1-G001", voxels)
        assert meta["voxel_count"] == 3
        assert math.isclose(meta["voxel_size"], 0.03, abs_tol=_TOL)
        assert meta["zoom_level"] == 9

        loaded = repo.load_meta("b1-G001", 9)
        assert loaded == meta
    print("test_save_and_load_meta_roundtrip: OK")


def test_missing_cache_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        assert repo.load_meta("nonexistent", 9) is None
    print("test_missing_cache_returns_none: OK")


def test_positions_binary_matches_voxel_centers_sorted_by_id():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        voxels = [
            _make_voxel("9/0/1/0", [1.0, 2.0, 3.0]),
            _make_voxel("9/0/0/0", [4.0, 5.0, 6.0]),
        ]
        repo.save("b1-G001", voxels)

        raw = repo.positions_path("b1-G001", 9).read_bytes()
        arr = np.frombuffer(raw, dtype=np.float32).reshape(-1, 3)
        assert arr.shape == (2, 3)
        # local_spatial_id昇順ソート: "9/0/0/0" < "9/0/1/0"
        assert np.allclose(arr[0], [4.0, 5.0, 6.0], atol=_TOL)
        assert np.allclose(arr[1], [1.0, 2.0, 3.0], atol=_TOL)

        loaded_positions = repo.load_positions("b1-G001", 9)
        assert np.allclose(loaded_positions, arr)
    print("test_positions_binary_matches_voxel_centers_sorted_by_id: OK")


def test_invalidate_removes_cache():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        repo.save("b1-G001", [_make_voxel("9/0/0/0", [0.0, 0.0, 0.0])])
        assert repo.load_meta("b1-G001", 9) is not None
        repo.invalidate("b1-G001", 9)
        assert repo.load_meta("b1-G001", 9) is None
    print("test_invalidate_removes_cache: OK")


def test_invalidate_without_zoom_level_removes_all_levels():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        repo.save("b1-G001", [_make_voxel("9/0/0/0", [0.0, 0.0, 0.0])])
        repo.save("b1-G001", [_make_aggregated_voxel("8/0/0/0", [0.0, 0.0, 0.0], 8, 0.06, 2)])
        assert repo.load_meta("b1-G001", 9) is not None
        assert repo.load_meta("b1-G001", 8) is not None

        repo.invalidate("b1-G001")  # zoom_level省略 -> 全level破棄
        assert repo.load_meta("b1-G001", 9) is None
        assert repo.load_meta("b1-G001", 8) is None
    print("test_invalidate_without_zoom_level_removes_all_levels: OK")


def test_different_space_ids_are_kept_separate():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        repo.save("spaceA", [_make_voxel("9/0/0/0", [1.0, 1.0, 1.0])])
        repo.save("spaceB", [_make_voxel("9/0/0/0", [2.0, 2.0, 2.0])])  # 同じlocal_spatial_id文字列

        meta_a = repo.load_meta("spaceA", 9)
        meta_b = repo.load_meta("spaceB", 9)
        assert meta_a["space_id"] == "spaceA"
        assert meta_b["space_id"] == "spaceB"

        arr_a = np.frombuffer(repo.positions_path("spaceA", 9).read_bytes(), dtype=np.float32)
        arr_b = np.frombuffer(repo.positions_path("spaceB", 9).read_bytes(), dtype=np.float32)
        assert not np.allclose(arr_a, arr_b)
    print("test_different_space_ids_are_kept_separate: OK")


def test_multiple_zoom_levels_coexist_for_same_space():
    """Step 3: 同一space_idでも複数zoom levelのキャッシュが独立して共存できること。"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        repo.save("b1-G001", [_make_voxel("11/0/0/0", [0.015, 0.015, 0.015], zoom_level=11, voxel_size=0.03)])
        repo.save("b1-G001", [_make_aggregated_voxel("10/0/0/0", [0.03, 0.03, 0.03], 10, 0.06, 8)])

        meta_finest = repo.load_meta("b1-G001", 11)
        meta_coarse = repo.load_meta("b1-G001", 10)
        assert meta_finest["voxel_size"] == 0.03
        assert meta_coarse["voxel_size"] == 0.06
        assert meta_coarse["voxel_count"] == 1

        pos_finest = repo.load_positions("b1-G001", 11)
        pos_coarse = repo.load_positions("b1-G001", 10)
        assert not np.allclose(pos_finest, pos_coarse)
    print("test_multiple_zoom_levels_coexist_for_same_space: OK")


def test_save_accepts_aggregated_spatial_voxel():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        voxels = [_make_aggregated_voxel("9/0/0/0", [0.06, 0.06, 0.06], 9, 0.12, 16)]
        meta = repo.save("b1-G001", voxels)
        assert meta["voxel_count"] == 1
        assert meta["voxel_size"] == 0.12
    print("test_save_accepts_aggregated_spatial_voxel: OK")


def test_load_local_spatial_ids_does_not_depend_on_voxel_center():
    """ユーザー指示(2026-08-31): 「座標からIDを復元してjoinする」構造を
    廃止したことの直接証明。voxel_centerに、そのIDの本来のgrid位置とは
    無関係な(floor演算で逆算しても一致しない)値を意図的に与えても、
    load_local_spatial_ids()が正しい元のID文字列を返すことを確認する
    (座標→ID変換を一切経由していないことの証拠)。"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        voxels = [
            _make_voxel("9/0/1/0", [999.0, -999.0, 12345.0]),
            _make_voxel("9/0/0/0", [-1.0, -1.0, -1.0]),
            _make_voxel("9/3/7/2", [0.0, 0.0, 0.0]),
        ]
        repo.save("b1-G001", voxels)

        ids = repo.load_local_spatial_ids("b1-G001", 9)
        # local_spatial_id昇順(positions.binと同じ順序)
        assert ids == ["9/0/0/0", "9/0/1/0", "9/3/7/2"]
    print("test_load_local_spatial_ids_does_not_depend_on_voxel_center: OK")


def test_local_spatial_ids_order_matches_positions_order():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        voxels = [
            _make_voxel("9/2/0/0", [7.0, 8.0, 9.0]),
            _make_voxel("9/0/0/0", [1.0, 2.0, 3.0]),
            _make_voxel("9/1/0/0", [4.0, 5.0, 6.0]),
        ]
        repo.save("b1-G001", voxels)

        ids = repo.load_local_spatial_ids("b1-G001", 9)
        positions = repo.load_positions("b1-G001", 9)
        assert len(ids) == len(positions) == 3
        expected_centers = {"9/0/0/0": [1.0, 2.0, 3.0], "9/1/0/0": [4.0, 5.0, 6.0], "9/2/0/0": [7.0, 8.0, 9.0]}
        for i, sid in enumerate(ids):
            assert np.allclose(positions[i], expected_centers[sid], atol=_TOL)
    print("test_local_spatial_ids_order_matches_positions_order: OK")


def test_order_fingerprint_present_and_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        voxels = [_make_voxel("9/0/0/0", [0.0, 0.0, 0.0]), _make_voxel("9/0/1/0", [1.0, 1.0, 1.0])]
        meta1 = repo.save("b1-G001", voxels)
        assert meta1["order_fingerprint"]

        repo2 = SpatialVoxelCacheRepository(Path(tmp) / "other")
        meta2 = repo2.save("b1-G001", voxels)
        assert meta1["order_fingerprint"] == meta2["order_fingerprint"], (
            "同じID集合・同じ順序なら、別インスタンス・別ディレクトリでもfingerprintは一致するべき"
        )

        assert repo.load_order_fingerprint("b1-G001", 9) == meta1["order_fingerprint"]
    print("test_order_fingerprint_present_and_deterministic: OK")


def test_order_fingerprint_changes_when_ids_change():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        meta_a = repo.save("b1-G001", [_make_voxel("9/0/0/0", [0.0, 0.0, 0.0])])
        meta_b = repo.save("b1-G002", [_make_voxel("9/9/9/9", [0.0, 0.0, 0.0])])
        assert meta_a["order_fingerprint"] != meta_b["order_fingerprint"]
    print("test_order_fingerprint_changes_when_ids_change: OK")


def test_invalidate_removes_ids_binary_too():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelCacheRepository(Path(tmp))
        repo.save("b1-G001", [_make_voxel("9/0/0/0", [0.0, 0.0, 0.0])])
        assert repo.ids_path("b1-G001", 9).exists()
        repo.invalidate("b1-G001", 9)
        assert not repo.ids_path("b1-G001", 9).exists()
        assert repo.load_meta("b1-G001", 9) is None
    print("test_invalidate_removes_ids_binary_too: OK")


if __name__ == "__main__":
    test_save_and_load_meta_roundtrip()
    test_missing_cache_returns_none()
    test_positions_binary_matches_voxel_centers_sorted_by_id()
    test_invalidate_removes_cache()
    test_invalidate_without_zoom_level_removes_all_levels()
    test_different_space_ids_are_kept_separate()
    test_multiple_zoom_levels_coexist_for_same_space()
    test_save_accepts_aggregated_spatial_voxel()
    test_load_local_spatial_ids_does_not_depend_on_voxel_center()
    test_local_spatial_ids_order_matches_positions_order()
    test_order_fingerprint_present_and_deterministic()
    test_order_fingerprint_changes_when_ids_change()
    test_invalidate_removes_ids_binary_too()
    print()
    print("全テスト成功。")
