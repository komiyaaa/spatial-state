"""
backend/spatial_state_view.py

Spatial State Updater(spatial_state/)の内部表現(state/confidence_flag/mu/
kappa/p_occ等)を、Viewer/Integrated Viewが依存してよい、安定した表示契約
(Read Model)へ変換するPresentation層。

【目標アーキテクチャにおける位置づけ】
    ... → Repository(state_store.py)
      → [Presentation / Read Model(このファイル)] → API → Viewer / Integrated View

【重要: Read Modelは表示契約であり、研究上のSpatial State定義そのものでは
ない】
ここで定義する語彙(presence/confidence/mobility)は、spatial_state/の
State/ConfidenceFlag Enum(内部表現、博士論文で今後差し替わり得る)を、
そのまま右から左へ転写したものではない。意図的に別の語彙・別の粒度にして
いる(例: OCCUPIED→PRESENT、SCAN_COVERED/NEVER_OBSERVED→どちらもUNOBSERVED
へ統合)。これにより、将来Updaterの内部状態表現(4状態の名称・数・alpha/beta/
mu/kappaの算出方法)が変わっても、Viewer側が依存するAPIコントラクトは
「この変換関数の実装を書き換えるだけ」で維持できる。

現在のmu→mobility判定閾値(0.4/0.6)は、以前はフロントエンド
(local_space_prototype.htmlのmuToMobilityFlag())にハードコードされていた
ものを、意味変換の責務としてこちらへそのまま移設しただけであり、式・閾値
自体は変更していない(暫定値・要較正である旨のコメントも維持する)。
"""
from __future__ import annotations

from typing import Dict

# 内部State(spatial_state/voxel.py)→ 表示契約presenceへの対応。
# SCAN_COVERED/NEVER_OBSERVEDは、現在のViewerがどちらも「証拠が無いので
# 描画しない」という同じ扱いをしているため、意図的に1つのUNOBSERVEDへ
# 統合する(Read Modelは表示契約であり、内部Enumの粒度をそのまま保つ
# 義務は無い)。
_PRESENCE_MAP = {
    "OCCUPIED": "PRESENT",
    "DECAYED": "ABSENT",
    "SCAN_COVERED": "UNOBSERVED",
    "NEVER_OBSERVED": "UNOBSERVED",
}

# 内部ConfidenceFlag(spatial_state/voxel.py)→ 表示契約confidenceへの対応。
_CONFIDENCE_MAP = {
    "CONFIRMED": "HIGH",
    "PENDING": "LOW",
}


def _mobility_hint(confidence_flag: str, mu: float) -> str:
    """spatial_state本体にはmobility(動的/静的)という概念自体が無く、連続値
    mu([0,1]、動的度合い)とconfidence_flagしか無い。Viewer表示用に、ここで
    STATIC/DYNAMIC/PENDINGへ変換する。閾値(0.4/0.6)は暫定値であり、実データ
    での較正が必要(docs/spatial_id_design_memo_v2.md §4)。"""
    if confidence_flag != "CONFIRMED":
        return "PENDING"
    if mu >= 0.6:
        return "DYNAMIC"
    if mu <= 0.4:
        return "STATIC"
    return "PENDING"


def build_voxel_view(voxel_summary: dict) -> dict:
    """SpatialStateTracker.summary()の1ボクセル分の内部表現を、Viewer向けの
    安定した意味表現(Read Model)へ変換する。"""
    state = voxel_summary["state"]
    confidence_flag = voxel_summary["confidence_flag"]
    return {
        "presence": _PRESENCE_MAP.get(state, "UNOBSERVED"),
        "confidence": _CONFIDENCE_MAP.get(confidence_flag, "LOW"),
        "mobility": _mobility_hint(confidence_flag, voxel_summary["mu"]),
    }


def build_spatial_state_view(tracker_summary: Dict[str, dict]) -> Dict[str, dict]:
    """space_id内の全ボクセルについてbuild_voxel_view()を適用する
    (GET /api/spatial-state/<space_id> ・ POST /api/registration-results の
    voxel_summaryの実体)。"""
    return {voxel_id: build_voxel_view(v) for voxel_id, v in tracker_summary.items()}
