"""
backend/repositories/spatial_voxel_label_repository.py の動作確認テスト
(ロードマップStep 4で追加したメモリキャッシュを含む)。一時ディレクトリを
使い、実データには一切触れない。

実行方法(リポジトリルートから):
    python backend/tests/test_spatial_voxel_label_repository.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.structural_label import LabelCandidate, SpatialVoxelLabel, StructuralLabel  # noqa: E402
from repositories.spatial_voxel_label_repository import SpatialVoxelLabelRepository  # noqa: E402


def _make_label(sid, label=StructuralLabel.FLOOR):
    return SpatialVoxelLabel(
        space_id="b1-G001", local_spatial_id=sid,
        label_candidates=[LabelCandidate(label=label, fitness=1.0)],
        resolved_label=label,
    )


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelLabelRepository(Path(tmp))
        repo.save_all("b1-G001", [_make_label("9/0/0/0"), _make_label("9/0/1/0", StructuralLabel.WALL)])
        loaded = repo.load_all("b1-G001")
        assert len(loaded) == 2
        assert loaded["9/0/0/0"].resolved_label == StructuralLabel.FLOOR
        assert loaded["9/0/1/0"].resolved_label == StructuralLabel.WALL
    print("test_save_and_load_roundtrip: OK")


def test_memory_cache_returns_consistent_data_across_calls():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelLabelRepository(Path(tmp))
        repo.save_all("b1-G001", [_make_label("9/0/0/0")])

        first = repo.load_all("b1-G001")
        second = repo.load_all("b1-G001")  # キャッシュヒットのはず
        assert first.keys() == second.keys()
        assert first["9/0/0/0"].resolved_label == second["9/0/0/0"].resolved_label
    print("test_memory_cache_returns_consistent_data_across_calls: OK")


def test_memory_cache_invalidated_after_save_all():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelLabelRepository(Path(tmp))
        repo.save_all("b1-G001", [_make_label("9/0/0/0", StructuralLabel.FLOOR)])
        loaded_before = repo.load_all("b1-G001")
        assert loaded_before["9/0/0/0"].resolved_label == StructuralLabel.FLOOR

        repo.save_all("b1-G001", [_make_label("9/0/0/0", StructuralLabel.WALL)])
        loaded_after = repo.load_all("b1-G001")
        assert loaded_after["9/0/0/0"].resolved_label == StructuralLabel.WALL, (
            "save_all後にキャッシュが無効化されず、古いデータが返された"
        )
    print("test_memory_cache_invalidated_after_save_all: OK")


def test_memory_cache_invalidated_when_file_changes_externally():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelLabelRepository(Path(tmp))
        repo.save_all("b1-G001", [_make_label("9/0/0/0", StructuralLabel.FLOOR)])
        repo.load_all("b1-G001")  # キャッシュを温める

        # repoを経由せず、ファイルを直接書き換える(mtimeが変わる)
        import json
        time.sleep(0.01)
        path = repo._path("b1-G001")
        path.write_text(
            json.dumps({"9/0/0/0": _make_label("9/0/0/0", StructuralLabel.CEILING).to_dict()}, ensure_ascii=False),
            encoding="utf-8",
        )

        reloaded = repo.load_all("b1-G001")
        assert reloaded["9/0/0/0"].resolved_label == StructuralLabel.CEILING, (
            "ファイルが外部で変更された(mtime変化)のに、古いキャッシュが返された"
        )
    print("test_memory_cache_invalidated_when_file_changes_externally: OK")


def test_missing_file_returns_empty_dict():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelLabelRepository(Path(tmp))
        assert repo.load_all("nonexistent") == {}
    print("test_missing_file_returns_empty_dict: OK")


def test_different_space_ids_kept_separate():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelLabelRepository(Path(tmp))
        repo.save_all("spaceA", [_make_label("9/0/0/0", StructuralLabel.FLOOR)])
        repo.save_all("spaceB", [_make_label("9/0/0/0", StructuralLabel.WALL)])

        a = repo.load_all("spaceA")
        b = repo.load_all("spaceB")
        assert a["9/0/0/0"].resolved_label == StructuralLabel.FLOOR
        assert b["9/0/0/0"].resolved_label == StructuralLabel.WALL
    print("test_different_space_ids_kept_separate: OK")


if __name__ == "__main__":
    test_save_and_load_roundtrip()
    test_memory_cache_returns_consistent_data_across_calls()
    test_memory_cache_invalidated_after_save_all()
    test_memory_cache_invalidated_when_file_changes_externally()
    test_missing_file_returns_empty_dict()
    test_different_space_ids_kept_separate()
    print()
    print("全テスト成功。")
