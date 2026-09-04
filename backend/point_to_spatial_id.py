"""
backend/point_to_spatial_id.py

ワールド座標点群を、Local Space自身のCoordinateDefinition(origin/rad/
unit-size)を使ってLocal Spatial ID("{zoom}/{f}/{x}/{y}")へ変換する、
順方向(point → ID)の変換ロジックを1箇所に集約したモジュール。本モジュールが
この変換の唯一の実装であり、server.py・plane_to_voxel_labels.py の両方が
これを参照する。

【zoom semantics(ユーザー指定: 2026-08-29、旧設計からの変更)】
以前は、水平(XY)方向はspace_def["unit-size"]、鉛直(Z/f)方向は
derive_vertical_unit_size()が独立に導出する別系列、という2系統の設計だった。
しかしこれには仕様上の矛盾があった: 「Z extentがXY footprintを超える縦長
空間では、共有のzoom_level整数でZ側をクランプすると、finest level(3cmの
はず)がXYはちょうど3cmなのにZは3cmに届かない(例: footprint 5m×5m・
高さ12mの空間でZ voxel sizeが0.06mになった)」という不具合が実際に発生した。

これを解消するため、**zoom level zはX/Y/Z共通の解像度levelとする**設計に
変更した。同一zoom levelでは、X/Y/Zすべて同じvoxel edge size
(= space_def["unit-size"][str(zoom_level)])を使う。つまりfinest levelは
必ず0.03m×0.03m×0.03mの立方voxelになる。XY方向とZ方向で別々の階層段数を
持つ設計(derive_vertical_unit_size・Zのクランプ処理)は廃止した。

Local Space生成側(backend/space_definition_generator.py)が、
`required_size = max(XY方向の必要包含サイズ, Z extent)`を包含できるまでの
単一のunit-size系列を生成する(この関数は変わらずspace_defの"unit-size"を
そのまま読むだけで、系列の生成自体には関与しない)。

【重要】origin・rad(rotation)による回転式自体は変更していない:
- local_y = rel_x*sin(theta) - rel_y*cos(theta) というY軸反転を含む変換式
  (det=-1、ガイドライン§2.5.2が要求する左手系。Phase 3のRigidTransform2D
  (det=+1の真の回転)とは別物であり、混同しないこと)

【Phase 3.1のLocalSpatialIdResolverとの責務の違い】
このモジュールは「point(ワールド座標) → Local Spatial ID」という順方向の
変換のみを担当する。Phase 3.1で計画されているLocalSpatialIdResolverは
「Local Spatial ID → そのLocal Space自身のローカル座標系内のvoxel中心点」
という逆方向の変換を担当する予定であり、責務が異なる。今回のzoom semantics
統一(X/Y/Z共通のunit-size[zoom_level])は、LocalSpatialIdResolver側でも
同じ規約(f/x/yいずれも同じunit-size[zoom_level]から逆算する)を使えば
そのまま整合する構造になっている。
"""
from __future__ import annotations

import numpy as np


def world_points_to_intrinsic(points: np.ndarray, space_def: dict) -> np.ndarray:
    """
    ワールド座標(N, 3)を、space_defの座標定義(origin・回転角)を使って、
    「intrinsic座標」(voxel量子化(floor)前の連続値、Local Spatial ID座標系
    そのもの)へ変換する。world_points_to_spatial_ids()のfloor手前までを
    切り出したもので、回転式自体は変更していない
    (2026-09-03、precise_registered.ply等をSpatial Resolutionのresolved
    Global座標へ変換するservice(services/global_coordinate_service.py)が、
    この式を複製せず再利用するために抽出した)。

    元実装(gen_local_spatialid.py の IdGenerator.calc_id)と数学的に
    同一の回転式を、1点ずつのPythonループではなくnumpyでベクトル化して
    適用している(大規模点群での高速化のため)。
    """
    origin = np.array(space_def["origin"], dtype=np.float64)
    theta = space_def["rad"]

    rel = points - origin
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    # 元実装(calc_id)と数学的に同一の回転式。local_yの符号に注意
    # (r・cos(θ-φ), r・sin(θ-φ) を直交座標の差分から直接計算した形)
    local_x = rel[:, 0] * cos_t + rel[:, 1] * sin_t
    local_y = rel[:, 0] * sin_t - rel[:, 1] * cos_t
    local_z = rel[:, 2]
    return np.stack([local_x, local_y, local_z], axis=1)


def world_points_to_spatial_ids(points: np.ndarray, space_def: dict, zoom_level: int) -> list:
    """
    ワールド座標(N, 3)を、space_defの座標定義(origin・回転角)を使って、
    Local Spatial ID(zoom/f/x/y文字列)の配列に変換する。

    X/Y/Zすべて同じvoxel size(space_def["unit-size"][str(zoom_level)])を
    使う(モジュールdocstring参照。以前あった鉛直方向の独立系列・クランプは
    廃止済み)。
    """
    voxel_size = space_def["unit-size"][str(zoom_level)]
    intrinsic = world_points_to_intrinsic(points, space_def)

    x_idx = np.floor(intrinsic[:, 0] / voxel_size).astype(int)
    y_idx = np.floor(intrinsic[:, 1] / voxel_size).astype(int)
    f_idx = np.floor(intrinsic[:, 2] / voxel_size).astype(int)

    return [f"{zoom_level}/{f}/{x}/{y}" for f, x, y in zip(f_idx, x_idx, y_idx)]
