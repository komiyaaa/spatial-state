"""
backend/repositories/voxel_color_cache_repository.py の動作確認テスト
(ロードマップStep 4)。一時ディレクトリを使い、実データには一切触れない。

実行方法(リポジトリルートから):
    python backend/tests/test_voxel_color_cache_repository.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from repositories.voxel_color_cache_repository import VoxelColorCacheRepository  # noqa: E402


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        repo = VoxelColorCacheRepository(Path(tmp))
        codes = np.array([0, 1, 2, 1], dtype=np.uint8)
        legend = {"0": [0.5, 0.5, 0.5], "1": [0.0, 1.0, 0.0], "2": [1.0, 0.0, 0.0]}
        meta = repo.save("spaceA", 9, "STRUCTURAL_LABEL", codes, legend)
        assert meta["voxel_count"] == 4
        assert meta["legend"] == legend

        loaded_meta = repo.load_meta("spaceA", 9, "STRUCTURAL_LABEL")
        assert loaded_meta == meta
        loaded_codes = repo.load_codes("spaceA", 9, "STRUCTURAL_LABEL")
        assert np.array_equal(loaded_codes, codes)
    print("test_save_and_load_roundtrip: OK")


def test_missing_cache_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        repo = VoxelColorCacheRepository(Path(tmp))
        assert repo.load_meta("spaceA", 9, "STRUCTURAL_LABEL") is None
    print("test_missing_cache_returns_none: OK")


def test_different_modes_coexist_for_same_space_and_zoom():
    with tempfile.TemporaryDirectory() as tmp:
        repo = VoxelColorCacheRepository(Path(tmp))
        repo.save("spaceA", 9, "DEFAULT", np.array([0, 0], dtype=np.uint8), {"0": [0.5, 0.5, 0.5]})
        repo.save("spaceA", 9, "STRUCTURAL_LABEL", np.array([1, 2], dtype=np.uint8), {"1": [0, 1, 0], "2": [1, 0, 0]})

        default_codes = repo.load_codes("spaceA", 9, "DEFAULT")
        label_codes = repo.load_codes("spaceA", 9, "STRUCTURAL_LABEL")
        assert not np.array_equal(default_codes, label_codes)
    print("test_different_modes_coexist_for_same_space_and_zoom: OK")


def test_invalidate_specific_entry():
    with tempfile.TemporaryDirectory() as tmp:
        repo = VoxelColorCacheRepository(Path(tmp))
        repo.save("spaceA", 9, "STRUCTURAL_LABEL", np.array([1], dtype=np.uint8), {"1": [0, 1, 0]})
        repo.save("spaceA", 8, "STRUCTURAL_LABEL", np.array([2], dtype=np.uint8), {"2": [1, 0, 0]})

        repo.invalidate("spaceA", 9, "STRUCTURAL_LABEL")
        assert repo.load_meta("spaceA", 9, "STRUCTURAL_LABEL") is None
        assert repo.load_meta("spaceA", 8, "STRUCTURAL_LABEL") is not None  # 別zoom levelは残る
    print("test_invalidate_specific_entry: OK")


def test_position_order_fingerprint_is_recorded():
    """ユーザー指示(2026-08-31): colors.binがどのpositions.bin instance順序
    から生成されたかを、座標やIDを再計算せず突き合わせだけで検証できるよう、
    position cache側のorder_fingerprintをそのまま複製して保持すること。"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = VoxelColorCacheRepository(Path(tmp))
        meta = repo.save(
            "spaceA", 9, "STRUCTURAL_LABEL", np.array([0, 1], dtype=np.uint8),
            {"0": [0.5, 0.5, 0.5], "1": [0, 1, 0]},
            position_order_fingerprint="abc123",
        )
        assert meta["position_order_fingerprint"] == "abc123"
        assert repo.load_meta("spaceA", 9, "STRUCTURAL_LABEL")["position_order_fingerprint"] == "abc123"
    print("test_position_order_fingerprint_is_recorded: OK")


def test_invalidate_all_for_space():
    with tempfile.TemporaryDirectory() as tmp:
        repo = VoxelColorCacheRepository(Path(tmp))
        repo.save("spaceA", 9, "DEFAULT", np.array([0], dtype=np.uint8), {"0": [0.5, 0.5, 0.5]})
        repo.save("spaceA", 8, "STRUCTURAL_LABEL", np.array([1], dtype=np.uint8), {"1": [0, 1, 0]})

        repo.invalidate("spaceA")
        assert repo.load_meta("spaceA", 9, "DEFAULT") is None
        assert repo.load_meta("spaceA", 8, "STRUCTURAL_LABEL") is None
    print("test_invalidate_all_for_space: OK")


if __name__ == "__main__":
    test_save_and_load_roundtrip()
    test_missing_cache_returns_none()
    test_different_modes_coexist_for_same_space_and_zoom()
    test_invalidate_specific_entry()
    test_position_order_fingerprint_is_recorded()
    test_invalidate_all_for_space()
    print()
    print("全テスト成功。")
