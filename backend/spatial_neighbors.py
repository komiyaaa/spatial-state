"""
backend/spatial_neighbors.py

空間ID("{zoom}/{f}/{x}/{y}"形式の文字列)から、面隣接(6方向: f±1, x±1, y±1)の
空間IDを計算する(段階1本体の統合作業、CLAUDE.md 手順4)。

【重要】spatial_state/params.py の n0 のデフォルト値(0.5)は、面隣接6個を
前提にした、近傍プーリングによる希釈込みの値(Params.expected_n_neighbors_for_validation
のデフォルトも6)。このリポジトリでは面隣接6を採用するため、Paramsのデフォルトの
ままでよい(近傍数を変える場合はexpected_n_neighbors_for_validationも合わせて
変更し、Params.validate()が通ることを確認すること)。
"""
from __future__ import annotations

_DELTAS = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]


def face_neighbors(spatial_id: str) -> list[str]:
    """空間IDの面隣接6個(f±1, x±1, y±1)の空間IDを返す。"""
    zoom, f, x, y = spatial_id.split("/")
    f, x, y = int(f), int(x), int(y)
    return [f"{zoom}/{f + df}/{x + dx}/{y + dy}" for df, dx, dy in _DELTAS]
