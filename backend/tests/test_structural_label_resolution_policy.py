"""
backend/structural_label_resolution_policy.py の動作確認テスト。

実行方法(リポジトリルートから):
    python backend/tests/test_structural_label_resolution_policy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.structural_label import LabelCandidate, StructuralLabel  # noqa: E402
from structural_label_resolution_policy import (  # noqa: E402
    StructuralLabelResolutionPolicyConfig,
    resolve_label,
)


def test_no_candidates_is_unresolved():
    assert resolve_label([]) == StructuralLabel.UNRESOLVED
    print("test_no_candidates_is_unresolved: OK")


def test_single_dominant_candidate_resolves():
    candidates = [LabelCandidate(label=StructuralLabel.FLOOR, fitness=0.95, source_plane_ids=["P001"])]
    assert resolve_label(candidates) == StructuralLabel.FLOOR
    print("test_single_dominant_candidate_resolves: OK")


def test_low_fitness_is_unresolved():
    candidates = [LabelCandidate(label=StructuralLabel.WALL, fitness=0.2, source_plane_ids=["P001"])]
    policy = StructuralLabelResolutionPolicyConfig(min_fitness_to_resolve=0.6)
    assert resolve_label(candidates, policy) == StructuralLabel.UNRESOLVED
    print("test_low_fitness_is_unresolved: OK")


def test_conflicting_close_candidates_is_ambiguous():
    candidates = [
        LabelCandidate(label=StructuralLabel.WALL, fitness=0.55, source_plane_ids=["P001"]),
        LabelCandidate(label=StructuralLabel.FLOOR, fitness=0.45, source_plane_ids=["P002"]),
    ]
    policy = StructuralLabelResolutionPolicyConfig(min_fitness_to_resolve=0.3, min_dominance_ratio=1.5)
    assert resolve_label(candidates, policy) == StructuralLabel.AMBIGUOUS
    print("test_conflicting_close_candidates_is_ambiguous: OK")


def test_dominant_candidate_wins_over_weaker_conflict():
    candidates = [
        LabelCandidate(label=StructuralLabel.WALL, fitness=0.9, source_plane_ids=["P001"]),
        LabelCandidate(label=StructuralLabel.CEILING, fitness=0.1, source_plane_ids=["P002"]),
    ]
    policy = StructuralLabelResolutionPolicyConfig(min_fitness_to_resolve=0.6, min_dominance_ratio=1.5)
    assert resolve_label(candidates, policy) == StructuralLabel.WALL
    print("test_dominant_candidate_wins_over_weaker_conflict: OK")


def test_negative_fitness_raises():
    try:
        LabelCandidate(label=StructuralLabel.WALL, fitness=-0.1)
        raise AssertionError("負のfitnessが受理されてしまった")
    except ValueError:
        pass
    print("test_negative_fitness_raises: OK")


if __name__ == "__main__":
    test_no_candidates_is_unresolved()
    test_single_dominant_candidate_resolves()
    test_low_fitness_is_unresolved()
    test_conflicting_close_candidates_is_ambiguous()
    test_dominant_candidate_wins_over_weaker_conflict()
    test_negative_fitness_raises()
    print()
    print("全テスト成功。")
