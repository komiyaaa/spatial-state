"""
backend/services/global_export_service.py

registration archive内の precise_registered.ply を、RESOLVED済みLocal
Spaceについて resolved Global座標へ変換し、別ファイル(derived artifact)
として書き出す(2026-09-03)。

【方針】
- 座標変換自体は必ず`services.global_coordinate_service.world_points_to_resolved_global()`
  を呼ぶだけで、独自の変換式・独自のmember_transforms_to_global再計算は
  行わない(Nodal Information・Spatial Resolutionの既存ロジックには一切
  触れない)。
- 元の`precise_registered.ply`は読み取り専用で扱い、絶対に上書きしない。
  出力は必ず別ファイル(`output_path`、例: `precise_registered_global.ply`)へ
  書く。`output_path == precise_ply_path`の場合は明示的にエラーにする
  (fail-closed)。
- 元PLYのXYZ以外の属性(現状のパイプラインで実際に生き残る可能性がある
  のはcolors(RGB)のみ — `run_vgicp()`がOpen3Dの`PointCloud`
  (points/colors/normalsのみ保持)経由で`precise_registered.ply`を
  書いているため、これ以外の属性(intensity等)はそもそも元ファイルにも
  存在しない。本モジュールも同じOpen3D I/Oを使い、存在する属性
  (colors)はそのまま複製し、それ以外を新規に作り出すことはしない)。
- Global座標へのexportは、Nodal Information変更後は再計算され得る
  derived artifactであり、source of truthではない。そのため、
  どのSpatial Resolution結果・どのregistration runから作られたかを
  必ずsidecarメタデータ(JSON)として残す。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import open3d as o3d

from domain.global_resolution import ComponentGlobalResolution
from services.global_coordinate_service import world_points_to_resolved_global


class GlobalExportError(RuntimeError):
    """Global exportを安全に実行できない状態(fail-closed)。"""


def _metadata_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".meta.json")


def export_precise_registered_to_global(
    precise_ply_path: Path,
    output_path: Path,
    space_id: str,
    space_def: dict,
    global_resolution: ComponentGlobalResolution,
    run_id: str,
    resolved_at: str | None = None,
) -> dict:
    """
    precise_registered.ply(world/provisional座標)を、resolved Global座標
    (target_epsgのメートル座標)へ変換した別ファイルとして書き出す。

    変換自体は`world_points_to_resolved_global()`をそのまま使う
    (world→intrinsic→globalのchainは既存実装のまま、ここでは再定義しない)。
    `global_resolution.status`がRESOLVEDでない場合、または`space_id`が
    `member_transforms_to_global`に無い場合は、`world_points_to_resolved_global()`
    自身が`GlobalCoordinateResolutionError`を送出する(fail-closed、
    このモジュールは追加のフォールバックを行わない)。

    :param precise_ply_path: 変換元。読み取り専用として扱い、一切書き込まない。
    :param output_path: 書き出し先(precise_ply_pathと同一パスは禁止)。
        colors(存在する場合)を含むPLYと、隣に`<output_path>.meta.json`を書く。
    :param space_id: precise_ply_pathが属するLocal Spaceのspace_id。
    :param space_def: そのspace_idのCoordinateDefinition(dict)。
    :param global_resolution: 対象space_idが属するcomponentの
        `ComponentGlobalResolution`(呼び出し側が既存のSpatial Resolution結果
        からそのまま渡す。ここでは再計算しない)。
    :param run_id: 変換元precise_registered.plyのregistration run_id
        (呼び出し側がregistration_results/のアーカイブから渡す)。
    :param resolved_at: このglobal_resolutionを含むSpatial Resolution実行の
        resolved_atタイムスタンプ(分かれば。無ければNoneのまま記録する)。
    :returns: sidecarへ書き込んだものと同じmetadata dict。
    """
    precise_ply_path = Path(precise_ply_path)
    output_path = Path(output_path)

    if not precise_ply_path.exists():
        raise GlobalExportError(f"変換元が見つかりません: {precise_ply_path}")
    if output_path.resolve() == precise_ply_path.resolve():
        raise GlobalExportError(
            "output_pathがprecise_ply_pathと同一です。元のprecise_registered.plyを"
            "上書きすることはできません(fail-closed)。"
        )

    pcd = o3d.io.read_point_cloud(str(precise_ply_path))
    points = np.asarray(pcd.points, dtype=np.float64)
    if len(points) == 0:
        raise GlobalExportError(f"点群が空です: {precise_ply_path}")

    # 座標変換は既存のworld_points_to_resolved_global()のみを使う
    # (Global未RESOLVED・space_idがmember_transforms_to_globalに無い場合は
    # ここでGlobalCoordinateResolutionErrorが送出される、fail-closed)。
    global_points = world_points_to_resolved_global(points, space_id, space_def, global_resolution)

    out_pcd = o3d.geometry.PointCloud()
    out_pcd.points = o3d.utility.Vector3dVector(global_points)
    has_colors = len(pcd.colors) == len(pcd.points) and len(pcd.colors) > 0
    if has_colors:
        out_pcd.colors = pcd.colors  # 元のRGBをそのまま複製する(座標のみ変換対象)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(output_path), out_pcd)

    transform = global_resolution.member_transforms_to_global[space_id]
    metadata = {
        "source_space_id": space_id,
        "source_run_id": run_id,
        "source_precise_registered_path": str(precise_ply_path),
        "output_path": str(output_path),
        "target_epsg": global_resolution.target_epsg,
        "spatial_resolution": {
            "component_id": global_resolution.component_id,
            "status": global_resolution.status.value,
            "resolved_at": resolved_at,
        },
        "applied_transform": transform.to_dict(),
        "point_count": int(len(global_points)),
        "colors_preserved": has_colors,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "derived_artifact": True,
        "note": (
            "このファイルはNodal Information/Spatial Resolutionから再計算可能な"
            "derived artifactであり、source of truthではない。Nodal Information"
            "変更後は古くなりうるため、再exportが必要。"
        ),
    }
    _metadata_path(output_path).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return metadata
