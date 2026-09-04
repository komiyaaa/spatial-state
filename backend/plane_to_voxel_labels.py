"""
backend/plane_to_voxel_labels.py

confirmed_labelがFLOOR/CEILING/WALLのPlaneについて、その支持点を
「そのLocal Space自身のCoordinateDefinition」で最小(3cm)Local Spatial ID
へ変換し、SpatialVoxelLabel(候補+解決結果)を組み立てる。

【設計方針(ユーザー指示: 2026-08-29)】
- Plane(点群上の平面の意味)とVoxel Label(Local Spatial IDに対応付けられた
  構造の意味)は別レイヤー。ここはその変換だけを担当し、競合解決の判断基準
  (何を採用しAMBIGUOUS/UNRESOLVEDにするか)はstructural_label_resolution_policy.py
  に委譲する(このファイルにルールを埋め込まない)。
- world→local spatial idの変換は、backend/point_to_spatial_id.py
  (world_points_to_spatial_ids)を共通利用する。この変換ロジックはもともと
  server.pyだけにあったが、server.pyを直接importするとimport順序(pygicpを
  scikit-learnより先に読み込む必要がある、server.py冒頭のコメント参照)が
  崩れ、実際にセグメンテーション違反を起こすことを確認したため、依存の軽い
  point_to_spatial_id.py(numpyのみに依存)へ切り出し、server.py・本モジュール
  の両方がそこを参照する形にした(ユーザー指示: 2026-08-29、重複実装の解消)。
  unit-size系列の生成ロジック(_build_unit_size_table)自体は
  space_definition_generator.pyをそのまま再利用する(変更なし)。
- 他のLocal Spaceのunit-sizeや共通zoom tableは絶対に使わず、必ず引数で
  渡されたそのLocal Space自身のspace_def(CoordinateDefinition)だけを使う。
- zoom_levelは、そのspace_defのunit-size系列の最も細かい段
  (space_definition_generator.finest_zoom_level)を使う。今回の運用では
  MIN_VOXEL_SIZE=0.03固定により、これは常に3cm levelになる。
- IGNORE・UNASSIGNEDのPlaneは、voxel structural labelを一切生成しない。
"""
from __future__ import annotations

import time
import uuid

import numpy as np

from domain.structural_label import (
    CONTRIBUTING_PLANE_LABELS,
    LabelCandidate,
    LabelFitnessHistoryEntry,
    SpatialVoxelLabel,
)
from point_to_spatial_id import world_points_to_spatial_ids
from space_definition_generator import finest_zoom_level
from structural_label_resolution_policy import StructuralLabelResolutionPolicyConfig, resolve_label


def build_voxel_labels(space_id: str, space_def: dict, planes: list, points: np.ndarray,
                        policy: StructuralLabelResolutionPolicyConfig | None = None):
    """confirmed_labelがFLOOR/CEILING/WALLのPlaneの支持点を、そのLocal Space
    自身の最小(3cm)Local Spatial IDへ変換し、(voxel_labels, history_entries)
    を返す。

    - space_def: そのLocal Space自身のCoordinateDefinition(生JSON辞書。
      "origin"/"rad"/"unit-size"を持つこと。unit-sizeはX/Y/Z共通、
      point_to_spatial_id.py参照)。他のLocal Spaceのものを渡してはならない。
    - planes: list[Plane]。point_indicesはpointsのインデックスを指す前提。
    - points: Planeがpoint_indicesを取ったのと同じ順序のワールド座標点群
      (例: Detect Planes時に読み込んだBase Mapファイルを同じ手順で再読み込み
      したもの)。
    """
    if "unit-size" not in space_def or "origin" not in space_def or "rad" not in space_def:
        raise ValueError("space_defが不正です(origin/rad/unit-sizeが必要): "
                          f"{sorted(space_def.keys())}")
    if points is None or len(points) == 0:
        raise ValueError("points が空です(voxelラベル変換には点群データが必要です)。")

    policy = policy or StructuralLabelResolutionPolicyConfig()
    zoom_level = finest_zoom_level(space_def)

    # voxel_id -> {label: {"count": int, "plane_ids": set[str]}}
    tally: dict = {}

    for plane in planes:
        if plane.confirmed_label not in CONTRIBUTING_PLANE_LABELS:
            continue
        if not plane.point_indices:
            continue
        indices = np.asarray(plane.point_indices, dtype=np.int64)
        if indices.max(initial=-1) >= len(points):
            raise ValueError(
                f"plane_id={plane.plane_id} のpoint_indicesがpoints配列の範囲外です"
                f"(max_index={indices.max()}, len(points)={len(points)})。"
                f"Detect Planes時と異なるBase Mapを使っている可能性があります。"
            )
        plane_points = points[indices]
        spatial_ids = world_points_to_spatial_ids(plane_points, space_def, zoom_level)
        for sid in spatial_ids:
            entry = tally.setdefault(sid, {})
            label_entry = entry.setdefault(plane.confirmed_label, {"count": 0, "plane_ids": set()})
            label_entry["count"] += 1
            label_entry["plane_ids"].add(plane.plane_id)

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    voxel_labels = []
    history_entries = []

    for sid, label_tally in tally.items():
        total = sum(v["count"] for v in label_tally.values())
        candidates = [
            LabelCandidate(
                label=label,
                fitness=(v["count"] / total) if total else 0.0,
                source_plane_ids=sorted(v["plane_ids"]),
            )
            for label, v in label_tally.items()
        ]
        candidates.sort(key=lambda c: c.fitness, reverse=True)

        resolved = resolve_label(candidates, policy)
        source_plane_ids = sorted({pid for c in candidates for pid in c.source_plane_ids})

        voxel_labels.append(SpatialVoxelLabel(
            space_id=space_id,
            local_spatial_id=sid,
            label_candidates=candidates,
            resolved_label=resolved,
            source_plane_ids=source_plane_ids,
            confirmed=False,
            created_at=now,
            updated_at=now,
        ))
        history_entries.append(LabelFitnessHistoryEntry(
            history_id=uuid.uuid4().hex,
            space_id=space_id,
            local_spatial_id=sid,
            timestamp=now,
            candidate_labels=candidates,
            resolved_label=resolved,
            source_plane_ids=source_plane_ids,
            policy_version=policy.version,
        ))

    return voxel_labels, history_entries
