"""
backend/space_definition_generator.py

ベースマップ(点群)から、backend/space_definitions/*.json と同じ形式の
座標定義メタデータ(id, degree, rad, height, origin, unit-size, bounds)を
自動生成する。

元になっているアルゴリズムは、gen_local_spatialid.zip に含まれていた
LocalSpacialIDGenerater.py の LocalSpacialIDGenerator クラス(PCAによる
Z軸回りの自動回転検出 + バウンディング正方形 + ズームレベル別unit-sizeの
等比数列生成)を、pyvista/pyproj非依存(numpy/scikit-learnのみ)で移植した
もの。ロジック自体(回転角の正規化・正方形化・originの取り方)は変更していない。

【元実装からの変更点】
1. heightフィールド: 元実装は回転後のY方向の辺の長さ(もう一つの水平方向、
   実質バグ)を書き出していた。docs/spatial_id_design_memo_v2.md §5が
   「heightフィールドの意味が未確定」と指摘していたのはこれが原因。
   server.py側はこのフィールドを一切参照していない(boundsのZ成分から
   動的に鉛直方向のズーム系列を導出する)ため実害は無かったが、今回の
   移植では実際の鉛直範囲(max_z - min_z)に修正して書き出す。
2. rotation: 回転角はPCAによる自動検出が既定だが、`rotation_rad`を
   明示的に渡すとPCA検出をスキップし、その角度をそのまま使う
   (ユーザー指定: 2026-08-29。GUIから明示的な回転を必須入力にするため)。
   明示指定時は、PCA自動検出時に行っている「-45°〜45°への正規化」も
   行わない(ユーザーが入力した値をそのまま尊重する)。
3. unit-sizeテーブル: **minimum voxel sizeをLocal Spaceごとに独立した、
   厳密な最小値として扱う**(ユーザー指定: 2026-08-29)。指定された
   base_unit_sizeから2倍ずつ増やし、建物全体を包含できた時点で系列を
   確定する。段数(zoom level数)はLocal Spaceごとに異なってよく、
   特定のズームレベル(例: 9)の存在を保証するためにbase_unit_sizeを
   自動的に縮小することはしない(旧版はこの自動縮小ロジックを持って
   いたが、廃止した)。
4. minimum voxel size は 0.03m(3cm)に固定(ユーザー指定: 2026-08-29、
   構造平面ラベリング機能の追加に伴う決定)。GUIからの入力は廃止し、
   `MIN_VOXEL_SIZE`定数を既定値として使う。テスト・将来の用途のため
   `base_unit_size`引数自体は残すが、本番のAPI呼び出し(server.py)からは
   常にこの定数を渡す。
5. unit-sizeはX/Y/Z共通の立方体voxel一辺長を意味する(ユーザー指定:
   2026-08-29)。以前はXY方向(このモジュールが生成するunit-size)とZ方向
   (point_to_spatial_id.pyが独立に導出する系列)が別々の等比数列だったが、
   「Z extentがXY footprintを超える縦長空間で、finest levelがXYは3cmなのに
   Zは3cmに届かない」という仕様上の矛盾が生じたため廃止した。
   `required_size = max(XY方向の必要包含サイズ, Z extent)`を包含できるまで
   0.03mから倍々にした、**単一の**系列をunit-sizeとして生成する。
   XY footprintより必要な最上位サイズが大きくなる場合、それはこの
   provisionalな座標定義(zoom level数の決定)だけの話であり、bounds/origin
   (実際のBase Mapの点群範囲)自体を拡大・スケールするものではない。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from sklearn.decomposition import PCA

from spatial_id_constants import MIN_VOXEL_SIZE  # re-export: XY/Z共通の単一定義箇所

MIN_BASE_UNIT_SIZE = 0.01  # これより細かい minimum voxel size は非対応(下限ガード)


def _apply_z_axis_rotation_only(points: np.ndarray, rotation_rad: Optional[float] = None) -> dict:
    """点群を、Z軸回りの回転角で整列させ、バウンディング正方形を求める。

    rotation_radが指定された場合はPCAによる自動検出をスキップし、その角度を
    そのまま使う(-45°〜45°への正規化も行わない。ユーザーが明示的に指定した
    値をそのまま尊重するため)。Noneの場合は、点群のPCA第1主成分から自動検出
    する(LocalSpacialIDGenerater.py の apply_z_axis_rotation_only() と同一
    ロジック、後方互換のため維持)。
    """
    if rotation_rad is None:
        pca = PCA(n_components=3)
        pca.fit(points)

        principal_direction = pca.components_[0][:2]
        angle = math.atan2(principal_direction[1], principal_direction[0])
        if angle < 0:
            angle += 2 * math.pi

        # -45°〜45°の範囲に正規化する(建物の辺に沿った最小の回転角にするため)
        if math.pi / 4 < angle <= 3 * math.pi / 4:
            angle -= math.pi / 2
        elif angle > 3 * math.pi / 4:
            angle -= math.pi

        angle_rad = angle
    else:
        angle_rad = rotation_rad

    degree = math.degrees(angle_rad)

    rotation_matrix = np.array([
        [math.cos(-angle_rad), -math.sin(-angle_rad), 0],
        [math.sin(-angle_rad), math.cos(-angle_rad), 0],
        [0, 0, 1],
    ])
    rotated = points @ rotation_matrix.T

    min_x, min_y = rotated[:, 0].min(), rotated[:, 1].min()
    max_x, max_y = rotated[:, 0].max(), rotated[:, 1].max()
    min_z, max_z = rotated[:, 2].min(), rotated[:, 2].max()

    width = max_x - min_x
    y_extent = max_y - min_y
    length = max(width, y_extent)  # 大きい方の辺で正方形化する
    z_extent = max_z - min_z

    center_x = (max_x + min_x) / 2
    center_y = (max_y + min_y) / 2

    bbox_vertices_rotated = np.array([
        [center_x - length / 2, center_y - length / 2, min_z],
        [center_x - length / 2, center_y - length / 2, max_z],
        [center_x - length / 2, center_y + length / 2, min_z],
        [center_x - length / 2, center_y + length / 2, max_z],
        [center_x + length / 2, center_y - length / 2, min_z],
        [center_x + length / 2, center_y - length / 2, max_z],
        [center_x + length / 2, center_y + length / 2, min_z],
        [center_x + length / 2, center_y + length / 2, max_z],
    ])
    # 主成分空間から元の座標系に戻す
    bbox_vertices = bbox_vertices_rotated @ np.linalg.inv(rotation_matrix).T
    origin = bbox_vertices[2]

    return {
        "degree": degree,
        "rad": angle_rad,
        "bounds": bbox_vertices,
        "origin": origin,
        "length": length,
        "height": z_extent,
    }


def _build_unit_size_table(required_size: float, base_unit_size: float) -> dict:
    """base_unit_size(ユーザー指定のminimum voxel size)を厳密な最小値として
    使い、2倍ずつ増やしてrequired_size(XY方向の必要包含サイズとZ extentの
    大きい方、呼び出し元のgenerate_space_definition参照)を包含できた時点で
    系列を確定する。

    この系列はX/Y/Z共通で使う(単一のunit-size)。段数(zoom level数)は
    Local Spaceごとに異なってよい。特定のズームレベルの存在を保証するために
    base_unit_sizeを自動的に縮小することはしない(ユーザー指定:
    2026-08-29)。"""
    sizes = [base_unit_size]
    while sizes[-1] < required_size:
        sizes.append(sizes[-1] * 2)
    sizes = sorted(sizes, reverse=True)
    return {str(i): size for i, size in enumerate(sizes)}


def finest_zoom_level(space_def: dict) -> int:
    """space_def["unit-size"]の中で最も細かい(=最小voxel sizeの)ズームレベルの
    indexを返す(段数-1)。MIN_VOXEL_SIZE固定運用では、これが常に3cm level
    (X/Y/Zすべて共通)になる。"""
    return len(space_def["unit-size"]) - 1


def generate_space_definition(
    points: np.ndarray,
    space_def_id: str,
    base_unit_size: float = MIN_VOXEL_SIZE,
    rotation_rad: Optional[float] = None,
) -> dict:
    """点群(ワールド座標、(N,3))から、space_definitions/*.jsonと同じ形式の
    座標定義メタデータ(provisionalなCoordinateDefinition)を生成する。

    rotation_radを明示的に渡すと、その角度をそのまま採用する(PCA自動検出は
    行わない)。Noneの場合は従来通りPCAで自動検出する(後方互換)。

    unit-sizeはX/Y/Z共通の立方体voxel一辺長の系列(ユーザー指定:
    2026-08-29)。required_size = max(XY方向の必要包含サイズ, Z extent)を
    包含できるまで、base_unit_sizeから2倍ずつ増やして系列を確定する
    (Z extentがXY footprintを超える縦長空間でも、finest levelが必ず
    base_unit_sizeちょうどになることを保証するため)。XY footprintより
    必要な最上位サイズが大きくなる場合があるが、これはunit-size系列
    (段数)だけの話であり、bounds/origin(実際のBase Mapの点群範囲)を
    拡大・スケールするものではない。
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 3:
        raise ValueError(f"点群の点数が少なすぎます(PCAには最低3点必要): {len(points)}点")
    if base_unit_size < MIN_BASE_UNIT_SIZE:
        raise ValueError(
            f"minimum voxel size は {MIN_BASE_UNIT_SIZE}m 以上である必要があります"
            f"(指定値: {base_unit_size}m)。"
        )

    rotation_info = _apply_z_axis_rotation_only(points, rotation_rad=rotation_rad)
    required_size = max(rotation_info["length"], rotation_info["height"])
    unit_size_table = _build_unit_size_table(required_size, base_unit_size)

    return {
        "id": space_def_id,
        "degree": float(rotation_info["degree"]),
        "rad": float(rotation_info["rad"]),
        "height": float(rotation_info["height"]),
        "origin": rotation_info["origin"].tolist(),
        "unit-size": unit_size_table,
        "bounds": rotation_info["bounds"].tolist(),
    }
