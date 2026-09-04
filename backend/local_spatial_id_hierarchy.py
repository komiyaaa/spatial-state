"""
backend/local_spatial_id_hierarchy.py

Local Spatial ID("z/f/x/y")の、zoom level階層内での親子関係を、座標変換を
一切経由せずID同士の関係だけで直接扱う共通モジュール(ユーザー指示:
2026-08-30。Step 3実装で埋め込んでいたparent ID計算を独立させた)。

【設計方針】
- 親IDの決定は、そのLocal Space自身のCoordinateDefinition.unit-size
  (段数・voxel_sizeの階層)だけを見て、ID同士の階層関係(floor division)
  から直接求める。voxel_center等の物理座標への変換
  (point_to_spatial_id.py・point_cloud_voxelization.py)は一切経由しない
  ("voxel_center → world_points_to_spatial_ids"という再変換をparent ID
  生成の本質にしない)。
- floor(floor(x/m)/n) == floor(x/(mn))(m, n は正の実数/整数、n は正の整数)
  という数学的性質により、子のf/x/yそれぞれを
  2^(子のzoom_level - target_zoom_level) で整数除算するだけで、親gridの
  f/x/yが一意に定まる(originもrotationも使わない)。
- target_zoom_levelが、渡されたcoordinate_definition自身のunit-sizeに
  存在することを検証する。
- 子と親のvoxel_size比が正確に2^Δ(倍々階層)であることも検証する
  (space_definition_generator._build_unit_size_tableは常にこの階層を
  生成するが、このモジュール単体で不正なcoordinate_definitionを渡された
  場合にも安全側に倒すため)。
- local_spatial_id文字列自体はspace_idを含まない。異なるLocal Spaceの
  同じID文字列同士を混同しないこと(space_idとの組での識別)は、常に
  呼び出し側の責務であり、このモジュール単体では判断できない。

backend/spatial_voxel_aggregation.py(SpatialVoxel aggregation)・将来の
Structural Label aggregation(ロードマップStep 4)の両方が、この
parent_local_spatial_id()を共通のID階層変換として再利用する想定。
"""
from __future__ import annotations

import math


def parse_local_spatial_id(local_spatial_id: str) -> tuple:
    """"z/f/x/y" を (zoom, f, x, y) の4整数へ分解する。"""
    zoom_str, f_str, x_str, y_str = local_spatial_id.split("/")
    return int(zoom_str), int(f_str), int(x_str), int(y_str)


def parent_local_spatial_id(
    local_spatial_id: str,
    target_zoom_level: int,
    coordinate_definition: dict,
) -> str:
    """finestに限らず任意のLocal Spatial IDから、target_zoom_levelの親IDを、
    ID階層関係(floor division)だけで直接求める(座標変換を経由しない)。

    target_zoom_level == 子のzoom levelの場合は、自分自身(恒等)を返す。
    target_zoom_levelが子のzoom levelより細かい(数値が大きい)場合、
    coordinate_definitionのunit-sizeに存在しない場合、子と親のvoxel_size比が
    2の階乗になっていない場合は、いずれもValueErrorを送出する。
    """
    child_zoom, f, x, y = parse_local_spatial_id(local_spatial_id)

    if "unit-size" not in coordinate_definition:
        raise ValueError("coordinate_definitionにunit-sizeがありません。")
    unit_size = coordinate_definition["unit-size"]

    if str(child_zoom) not in unit_size:
        raise ValueError(
            f"local_spatial_id '{local_spatial_id}' のzoom level {child_zoom} が"
            f"このCoordinateDefinitionのunit-sizeに存在しません。"
        )
    if str(target_zoom_level) not in unit_size:
        raise ValueError(
            f"target_zoom_level {target_zoom_level} はこのLocal Spaceのunit-sizeに"
            f"存在しません(有効なzoom_level: {sorted((int(k) for k in unit_size))})。"
        )
    if target_zoom_level > child_zoom:
        raise ValueError(
            f"target_zoom_level({target_zoom_level})は子のzoom level({child_zoom})を"
            f"超えられません(それより細かいlevelには変換できません)。"
        )

    if target_zoom_level == child_zoom:
        return local_spatial_id

    delta = child_zoom - target_zoom_level
    factor = 2 ** delta

    # 倍々階層であることの検証(voxel_size比が正確に2^deltaであること)。
    child_size = unit_size[str(child_zoom)]
    target_size = unit_size[str(target_zoom_level)]
    if not math.isclose(target_size / child_size, factor, rel_tol=1e-9):
        raise ValueError(
            f"zoom {child_zoom} -> {target_zoom_level} のvoxel_size比が2の階乗に"
            f"なっていません(期待値={factor}, 実際={target_size / child_size})。"
            f"このCoordinateDefinitionのunit-sizeが倍々系列でない可能性があります。"
        )

    return f"{target_zoom_level}/{f // factor}/{x // factor}/{y // factor}"
