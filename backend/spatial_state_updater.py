"""
backend/spatial_state_updater.py

Registration/VGICP結果(scan_jsonのhits)から、Spatial State Algorithm
(spatial_state/、内部表現: alpha/beta/mu/kappa/4状態)への1回の更新適用を
オーケストレーションする層。

【目標アーキテクチャにおける位置づけ】
    Registration/VGICP → Spatial State Algorithm → [Updater(このファイル)]
      → Repository(state_store.py) → Presentation/Read Model(spatial_state_view.py)
      → API → Viewer / Integrated View

ここにあるのはUpdaterの入出力の配線(オーケストレーション)だけであり、
alpha/beta更新式・κ算出・4状態遷移そのもの(spatial_state/core.py・tracker.py・
voxel.py)は一切変更していない。将来、博士論文で確定した新しい更新アルゴリズムに
差し替える場合、影響範囲はこのファイルとspatial_state/パッケージの中身に閉じ、
backend/server.py本体・state_store.py(Repository)・spatial_state_view.py
(Read Model)・Viewer/Integrated Viewには触れずに済む
(呼び出し側は本関数のシグネチャ・戻り値の形だけを頼りにしている)。

元はbackend/server.py receive_registration_result() 内にインラインされていた
処理(Step4)を、式を一切変えずにそのまま抽出したもの。
"""
from __future__ import annotations

from typing import Dict, Optional

from spatial_neighbors import face_neighbors
from spatial_state import Params, SpatialStateTracker
from state_store import StateStore


def apply_scan_to_spatial_state(
    space_id: str,
    hits: Dict[str, int],
    fitness_score: Optional[float],
    state_store: StateStore,
    params: Params,
    session: Optional[dict] = None,
) -> Dict[str, dict]:
    """scan_jsonのhitsをSpatial State Updaterへ適用し、永続化する。

    戻り値は、今回処理したボクセルについての
    `SpatialStateTracker.summary()`と同じ内部表現(state/confidence_flag/
    p_occ/kappa/mu/n_obs/n_mu)の辞書。Viewer向けのRead Model変換
    (spatial_state_view.build_spatial_state_view)は、この関数の責務では
    なく呼び出し側(server.py)が行う。
    """
    if not hits:
        return {}

    tracker: SpatialStateTracker = state_store.load(space_id, params)

    # このスキャンで直接ヒットしたボクセル + その面隣接も処理対象にする
    voxels_to_process = set(hits.keys())
    for sid in list(hits.keys()):
        voxels_to_process.update(face_neighbors(sid))

    for sid in voxels_to_process:
        c_self = hits.get(sid, 0)
        neighbor_ids = face_neighbors(sid)
        neighbor_counts = [hits.get(n, 0) for n in neighbor_ids]
        # covered判定は簡易版(自身がヒット or 近傍ヒット数がh_cov以上)。
        # TODO: スキャン全体のバウンディングボックスに基づく判定への
        # 差し替えを検討する(CLAUDE.md 手順5参照)。
        covered = (c_self > 0) or (sum(1 for c in neighbor_counts if c > 0) >= params.h_cov)

        tracker.update_voxel(
            voxel_id=sid,
            c_self=c_self,
            neighbor_ids=neighbor_ids,
            neighbor_counts=neighbor_counts,
            covered=covered,
            patch_fitness=fitness_score,
            structural_label=None,  # 構造ラベルは現状未実装(CLAUDE.md 手順7参照)
        )

    state_store.save(space_id, tracker, session=session)

    full_summary = tracker.summary()
    return {sid: full_summary[sid] for sid in voxels_to_process}
