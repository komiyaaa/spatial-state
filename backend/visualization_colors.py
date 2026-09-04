"""
backend/visualization_colors.py

Local Space ViewerのSpatial ID voxel色分けに使う色を一元管理する定数
テーブル(ロードマップStep 4)。値は暫定(将来調整可能)。

【重要】Viewer(JS)側はこのテーブルの値をAPI経由(legend)で受け取って
使うだけであり、色の意味づけ(どのカテゴリが何色か)をViewer内に
ハードコードしない。色を追加・変更したい場合は、このファイルだけを
更新すればよい。
"""
from __future__ import annotations

# カテゴリ名 -> RGB(0.0〜1.0)。Structural Label(FLOOR/CEILING/WALL/
# IGNORE/UNASSIGNED/AMBIGUOUS/UNRESOLVED)+ DEFAULT(単色モード)+
# NO_LABEL(finest・上位levelともに、一切ラベル評価が無いvoxel)を
# 1箇所にまとめる。
CATEGORY_COLORS = {
    "DEFAULT": (0.56, 0.65, 0.72),      # 既存の単色voxel表示(0x8fa6b8)と同じ
    "FLOOR": (0.36, 0.62, 0.36),        # 緑系
    "CEILING": (0.55, 0.75, 0.90),      # 水色系
    "WALL": (0.80, 0.72, 0.55),         # ベージュ系
    "IGNORE": (0.55, 0.55, 0.55),       # グレー(Plane側のラベルだが念のため用意)
    "UNASSIGNED": (0.70, 0.70, 0.70),   # 薄いグレー
    "AMBIGUOUS": (0.95, 0.65, 0.15),    # オレンジ(要確認であることを示す)
    "UNRESOLVED": (0.85, 0.30, 0.30),   # 赤系(証拠不足であることを示す)
    "NO_LABEL": (0.75, 0.75, 0.75),     # ラベル評価が一切無いvoxel
}

# codeの割り当ては明示的な列挙順で固定する(辞書のキー順に依存しない)。
# Viewer側はこの順序を仮定せず、APIが返すlegend(code -> RGB)をそのまま使う。
CATEGORY_ORDER = [
    "DEFAULT", "FLOOR", "CEILING", "WALL", "IGNORE",
    "UNASSIGNED", "AMBIGUOUS", "UNRESOLVED", "NO_LABEL",
]
CATEGORY_TO_CODE = {name: i for i, name in enumerate(CATEGORY_ORDER)}


def build_legend() -> dict:
    """code(int, JSONキーは文字列) -> [r,g,b] の対応表を返す
    (カテゴリ数個だけの小さな辞書、巨大JSONにはならない)。"""
    return {str(CATEGORY_TO_CODE[name]): list(CATEGORY_COLORS[name]) for name in CATEGORY_ORDER}
