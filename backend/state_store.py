"""
backend/state_store.py

space_idごとに、SpatialStateTrackerの内部状態をJSONファイルへ保存・復元する
永続化層(段階1本体の統合作業、CLAUDE.md 手順2)。

参照: docs/spatial_id_design_memo_v2.md §1(ER図)。1ファイル = 1 space_id とし、
ファイル内部を以下の3セクションに分けることで、v2メモのテーブル構成に
そのまま対応させる(将来SQLite等に載せ替える場合も、このJSON構造がそのまま
テーブル設計になる)。

    spatial_voxel        <-> SPATIAL_VOXEL(ボクセルごとの状態)
    label_fitness_history <-> LABEL_FITNESS_HISTORY(ラベルごとのfitness_score蓄積)
    scan_session         <-> SCAN_SESSION(スキャン1回=1レコード。patch_fitness_score等)

【structural_labelについて】
SPATIAL_VOXELテーブルには本来structural_label列があるが、spatial_state.VoxelState
自体にはこのフィールドが無い(tracker.update_voxel()は引数として受け取るだけで、
ボクセル自身には保存しない設計になっている)。構造ラベル(段階1の手順7)は
今回のスコープ外のため、永続化フォーマット上もこの列は省略している。
将来実装する場合はVoxelStateの拡張が必要。
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Optional

from spatial_state import ConfidenceFlag, Params, SpatialStateTracker, State, VoxelState


class StateStore:
    """SpatialStateTrackerの状態を、space_idごとに1つのJSONファイルへ
    永続化する。"""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, space_id: str) -> Path:
        return self.base_dir / f"{space_id}.json"

    def load(self, space_id: str, params: Optional[Params] = None) -> SpatialStateTracker:
        """保存済みの状態があれば復元し、無ければ新規のTrackerを返す。"""
        tracker = SpatialStateTracker(params or Params())
        path = self._path(space_id)
        if not path.exists():
            return tracker

        data = json.loads(path.read_text(encoding="utf-8"))

        for row in data.get("spatial_voxel", []):
            row = dict(row)
            voxel_id = row.pop("spatial_id")
            for key in ("z", "f", "x", "y"):
                row.pop(key, None)
            row["state"] = State(row["state"])
            row["confidence_flag"] = ConfidenceFlag(row["confidence_flag"])
            tracker.voxels[voxel_id] = VoxelState(voxel_id=voxel_id, **row)

        for row in data.get("label_fitness_history", []):
            tracker.fitness_history.setdefault(row["label"], []).append(row["fitness_score"])

        return tracker

    def save(self, space_id: str, tracker: SpatialStateTracker, session: Optional[dict] = None) -> None:
        """trackerの全ボクセル・fitness_historyをJSONに書き出す。

        session が渡された場合、scan_session 配列(SCAN_SESSION相当)に
        1件追記する(既存の記録は保持し、上書きしない)。
        """
        path = self._path(space_id)

        existing_sessions = []
        if path.exists():
            try:
                existing_sessions = json.loads(path.read_text(encoding="utf-8")).get("scan_session", [])
            except (json.JSONDecodeError, UnicodeDecodeError):
                existing_sessions = []

        spatial_voxel = []
        for voxel in tracker.voxels.values():
            row = dataclasses.asdict(voxel)
            voxel_id = row.pop("voxel_id")
            row["spatial_id"] = voxel_id
            row["state"] = voxel.state.value
            row["confidence_flag"] = voxel.confidence_flag.value
            zoom, f, x, y = voxel_id.split("/")
            row["z"], row["f"], row["x"], row["y"] = int(zoom), int(f), int(x), int(y)
            spatial_voxel.append(row)

        label_fitness_history = [
            {"label": label, "fitness_score": score}
            for label, scores in tracker.fitness_history.items()
            for score in scores
        ]

        scan_session = existing_sessions + [session] if session is not None else existing_sessions

        data = {
            "space_id": space_id,
            "spatial_voxel": spatial_voxel,
            "label_fitness_history": label_fitness_history,
            "scan_session": scan_session,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
