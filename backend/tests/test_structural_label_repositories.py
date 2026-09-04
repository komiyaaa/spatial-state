"""
backend/repositories/{plane,spatial_voxel_label,label_fitness_history}_repository.py
の動作確認テスト。一時ディレクトリを使い、実データ(backend/data/)には
一切触れない。

実行方法(リポジトリルートから):
    python backend/tests/test_structural_label_repositories.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.structural_label import (  # noqa: E402
    LabelCandidate,
    LabelFitnessHistoryEntry,
    Plane,
    SpatialVoxelLabel,
    StructuralLabel,
)
from repositories.label_fitness_history_repository import LabelFitnessHistoryRepository  # noqa: E402
from repositories.plane_repository import PlaneRepository  # noqa: E402
from repositories.spatial_voxel_label_repository import SpatialVoxelLabelRepository  # noqa: E402


def _plane(plane_id, label=StructuralLabel.WALL):
    return Plane(
        plane_id=plane_id, space_id="b1-G001",
        coefficients=[0.0, 0.0, 1.0, 0.0], normal=[0.0, 0.0, 1.0], centroid=[0.0, 0.0, 0.0],
        point_count=2, point_indices=[0, 1], suggested_label=label, confirmed_label=label,
    )


def test_plane_repository_save_load_update_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        repo = PlaneRepository(Path(tmp))
        repo.save_planes("b1-G001", [_plane("P001"), _plane("P002", StructuralLabel.IGNORE)])

        loaded = repo.load_planes("b1-G001")
        assert len(loaded) == 2
        assert {p.plane_id for p in loaded} == {"P001", "P002"}

        updated = repo.update_plane_label("b1-G001", "P002", StructuralLabel.FLOOR)
        assert updated.confirmed_label == StructuralLabel.FLOOR

        reloaded = repo.load_planes("b1-G001")
        p002 = next(p for p in reloaded if p.plane_id == "P002")
        assert p002.confirmed_label == StructuralLabel.FLOOR, "confirmed_labelの変更が永続化されていない"
        # 別space_idのデータには影響しないこと
        assert repo.load_planes("b1-G999") == []
    print("test_plane_repository_save_load_update_roundtrip: OK")


def test_plane_repository_update_rejects_invalid_label():
    with tempfile.TemporaryDirectory() as tmp:
        repo = PlaneRepository(Path(tmp))
        repo.save_planes("b1-G001", [_plane("P001")])
        try:
            repo.update_plane_label("b1-G001", "P001", StructuralLabel.AMBIGUOUS)
            raise AssertionError("PlaneにAMBIGUOUSが受理されてしまった")
        except ValueError:
            pass
        try:
            repo.update_plane_label("b1-G001", "P999", StructuralLabel.WALL)
            raise AssertionError("存在しないplane_idが受理されてしまった")
        except ValueError:
            pass
    print("test_plane_repository_update_rejects_invalid_label: OK")


def test_spatial_voxel_label_repository_keeps_spaces_separate():
    with tempfile.TemporaryDirectory() as tmp:
        repo = SpatialVoxelLabelRepository(Path(tmp))
        v_a = SpatialVoxelLabel(
            space_id="b1-G001", local_spatial_id="9/0/0/0",
            label_candidates=[LabelCandidate(label=StructuralLabel.FLOOR, fitness=1.0)],
            resolved_label=StructuralLabel.FLOOR,
        )
        v_b = SpatialVoxelLabel(
            space_id="b1-G002", local_spatial_id="9/0/0/0",  # 同じ文字列だが別space_id
            label_candidates=[LabelCandidate(label=StructuralLabel.WALL, fitness=1.0)],
            resolved_label=StructuralLabel.WALL,
        )
        repo.save_all("b1-G001", [v_a])
        repo.save_all("b1-G002", [v_b])

        loaded_a = repo.load_all("b1-G001")
        loaded_b = repo.load_all("b1-G002")
        assert loaded_a["9/0/0/0"].resolved_label == StructuralLabel.FLOOR
        assert loaded_b["9/0/0/0"].resolved_label == StructuralLabel.WALL, (
            "同じlocal_spatial_id文字列が別space_id間で混同されている"
        )
    print("test_spatial_voxel_label_repository_keeps_spaces_separate: OK")


def test_label_fitness_history_repository_appends_without_overwriting():
    with tempfile.TemporaryDirectory() as tmp:
        repo = LabelFitnessHistoryRepository(Path(tmp))
        entry1 = LabelFitnessHistoryEntry(
            history_id="h1", space_id="b1-G001", local_spatial_id="9/0/0/0", timestamp="t1",
            candidate_labels=[LabelCandidate(label=StructuralLabel.FLOOR, fitness=1.0)],
            resolved_label=StructuralLabel.FLOOR,
        )
        entry2 = LabelFitnessHistoryEntry(
            history_id="h2", space_id="b1-G001", local_spatial_id="9/0/0/1", timestamp="t2",
            candidate_labels=[LabelCandidate(label=StructuralLabel.WALL, fitness=1.0)],
            resolved_label=StructuralLabel.WALL,
        )
        repo.append("b1-G001", [entry1])
        repo.append("b1-G001", [entry2])
        history = repo.load_all("b1-G001")
        assert len(history) == 2, "appendが既存履歴を上書きしてしまった"
        assert {h.history_id for h in history} == {"h1", "h2"}
    print("test_label_fitness_history_repository_appends_without_overwriting: OK")


if __name__ == "__main__":
    test_plane_repository_save_load_update_roundtrip()
    test_plane_repository_update_rejects_invalid_label()
    test_spatial_voxel_label_repository_keeps_spaces_separate()
    test_label_fitness_history_repository_appends_without_overwriting()
    print()
    print("全テスト成功。")
