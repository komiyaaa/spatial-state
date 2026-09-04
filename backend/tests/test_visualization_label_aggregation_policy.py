"""
backend/visualization_label_aggregation_policy.py の動作確認テスト
(ロードマップStep 4)。

実行方法(リポジトリルートから):
    python backend/tests/test_visualization_label_aggregation_policy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from visualization_label_aggregation_policy import resolve_visualization_category  # noqa: E402


def test_empty_counts_is_no_label():
    assert resolve_visualization_category({}) == "NO_LABEL"
    print("test_empty_counts_is_no_label: OK")


def test_single_dominant_label_wins():
    assert resolve_visualization_category({"FLOOR": 8, "WALL": 1}) == "FLOOR"
    print("test_single_dominant_label_wins: OK")


def test_ambiguous_evidence_is_not_overwritten():
    # AMBIGUOUSが最多なら、そのままAMBIGUOUSが採用される(綺麗なlabelへ
    # すり替えない、evidenceを消さない)。
    result = resolve_visualization_category({"AMBIGUOUS": 5, "FLOOR": 3})
    assert result == "AMBIGUOUS"
    print("test_ambiguous_evidence_is_not_overwritten: OK")


def test_unresolved_evidence_is_not_overwritten():
    result = resolve_visualization_category({"UNRESOLVED": 4, "WALL": 2})
    assert result == "UNRESOLVED"
    print("test_unresolved_evidence_is_not_overwritten: OK")


def test_tie_is_resolved_deterministically():
    # 同数の場合、label名の昇順で最初のものを選ぶ(実行のたびに変わらない)
    result1 = resolve_visualization_category({"WALL": 3, "FLOOR": 3})
    result2 = resolve_visualization_category({"WALL": 3, "FLOOR": 3})
    assert result1 == result2 == "FLOOR"  # "FLOOR" < "WALL"
    print("test_tie_is_resolved_deterministically: OK")


if __name__ == "__main__":
    test_empty_counts_is_no_label()
    test_single_dominant_label_wins()
    test_ambiguous_evidence_is_not_overwritten()
    test_unresolved_evidence_is_not_overwritten()
    test_tie_is_resolved_deterministically()
    print()
    print("全テスト成功。")
