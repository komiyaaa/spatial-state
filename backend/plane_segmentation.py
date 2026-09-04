"""
backend/plane_segmentation.py

Base Map点群(ワールド座標)から、RANSACで構造平面を反復抽出する。

【設計方針】
- Open3D(既存依存、backend/server.pyの_import_pcdと同じライブラリ)の
  PointCloud.segment_plane()をそのまま利用する(独自RANSAC実装はしない)。
- 1回抽出するごとにinlier点を除去し、残り点群が閾値を下回るか、
  最大平面数に達するまで繰り返す(反復RANSAC)。
- suggested_labelは法線方向・相対高さのみから決める単純なヒューリスティック
  であり、完全自動分類ではない。ユーザーがconfirmed_labelを変更できることを
  前提とする(このモジュールはsuggested_labelしか書き込まない)。
- 分類閾値はすべてPlaneSegmentationConfigに集約する(処理コード中に
  マジックナンバーを散在させない)。
- ここで返すPlaneの座標(centroid・normal・coefficients)はすべてワールド
  座標系(Base Map点群そのものの座標系)であり、CoordinateDefinition
  (provisionalなLocal Space座標系)には依存しない。
- segment_plane自体は空間連続性を条件にしないため、平面方程式上は同じでも
  空間的に非連続な複数clusterが1つのPlaneに混ざりうる(2026-09-03判明)。
  検出直後にsplit_plane_by_connectivity()でDBSCAN分割し、独立したclusterは
  別々のPlaneとして返す(このモジュール内で完結、他モジュールは無変更)。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from domain.structural_label import Plane, StructuralLabel


@dataclass
class PlaneSegmentationConfig:
    """RANSAC・suggested_label判定の閾値を1箇所にまとめたもの。"""

    # --- RANSAC(反復抽出)関連 ---
    distance_threshold: float = 0.03  # inlierとみなす平面からの距離(m)。3cm voxelと整合させる
    ransac_n: int = 3
    num_iterations: int = 1000
    min_plane_points: int = 50  # これ未満の点しか残らない/取れない場合は抽出を打ち切る
    max_planes: int = 20  # 抽出する平面数の上限(暴走防止)

    # --- suggested_label判定関連 ---
    vertical_normal_cos_threshold: float = 0.85  # |法線のZ成分|がこれ以上なら「ほぼ水平面」
    horizontal_normal_cos_threshold: float = 0.35  # |法線のZ成分|がこれ以下なら「ほぼ鉛直面」
    floor_height_ratio: float = 0.25  # 全体のZ範囲に対する相対高さがこれ以下ならFLOOR候補
    ceiling_height_ratio: float = 0.75  # 相対高さがこれ以上ならCEILING候補

    # --- Plane空間連続性分割関連(2026-09-03追加) ---
    # Open3Dのsegment_plane自体は空間連続性を条件にしないアルゴリズムのため、
    # 平面方程式上は同じでも空間的に非連続な複数clusterが1つのPlaneにまとまる
    # ことがある(例: 異なる部屋の壁・天井が偶然同一平面上にあるケース)。
    # 検出後にDBSCANで再分割し、独立したclusterは別々のPlaneとして扱う。
    # 実データ(G002/G003)での検証により、0.5mが既知のノイズ隙間(最大0.21m)と
    # 既知の実隙間(最小1.2m)の両方を正しく分ける値として確認済み。
    split_eps: float = 0.5  # 連結成分分割のDBSCAN半径(m)
    split_min_samples: int = 10  # DBSCANのmin_samples(密度によるノイズ耐性)
    split_min_cluster_points: int = 50  # 分割後、独立Planeとして残す最小点数


def _suggest_label(normal: np.ndarray, centroid: np.ndarray, points: np.ndarray, config: PlaneSegmentationConfig) -> StructuralLabel:
    """法線方向・相対高さだけから、ラベル候補をヒューリスティックに提案する。

    完全自動分類ではない前提(ユーザー指示)なので、判定に自信が持てない
    ケースはUNASSIGNEDに倒す。"""
    z_min = float(points[:, 2].min())
    z_max = float(points[:, 2].max())
    z_range = z_max - z_min if z_max > z_min else 1.0
    nz = abs(float(normal[2]))

    if nz >= config.vertical_normal_cos_threshold:
        rel_height = (float(centroid[2]) - z_min) / z_range
        if rel_height <= config.floor_height_ratio:
            return StructuralLabel.FLOOR
        if rel_height >= config.ceiling_height_ratio:
            return StructuralLabel.CEILING
        return StructuralLabel.UNASSIGNED
    if nz <= config.horizontal_normal_cos_threshold:
        return StructuralLabel.WALL
    return StructuralLabel.UNASSIGNED


def split_plane_by_connectivity(
    plane: Plane,
    points: np.ndarray,
    config: PlaneSegmentationConfig,
    inherited_confirmed_label: StructuralLabel | None = None,
) -> list:
    """1枚のPlaneのinlier点群を空間連続性(DBSCAN)で分割し、独立した
    coplanarクラスタごとに別々のPlaneを返す。

    分割が不要(有効クラスタが1つ以下)の場合は、元のplaneを完全に不変の
    まま1件返す(ノイズ点も一切削らない)。分割が起きる場合のみ、DBSCANの
    ノイズ点・split_min_cluster_points未満の小clusterがPlane/Structural
    Label生成対象から除外される(Base Map点群自体・point_indicesが参照する
    元の点配列は一切変更しない。除外されるのは、このplaneから作られる
    派生Planeの対象からのみ)。

    inherited_confirmed_labelを渡した場合、各子Planeのconfirmed_labelは
    (新たに提案されるsuggested_labelとは独立に)その値を継承する
    (既存data移行時、人間によるレビュー結果を保持するために使う)。
    省略時(新規検出時)は、各子が自分自身のcentroidから再提案された
    suggested_labelをconfirmed_labelとして使う(現状の「未レビュー時は
    自動提案値」という不変条件を維持する)。
    """
    from sklearn.cluster import DBSCAN

    indices = np.asarray(plane.point_indices, dtype=np.int64)
    plane_points = points[indices]
    if len(plane_points) < config.split_min_samples:
        return [plane]

    db_labels = DBSCAN(eps=config.split_eps, min_samples=config.split_min_samples).fit_predict(plane_points)
    cluster_ids = [c for c in np.unique(db_labels) if c != -1]
    valid_clusters = [c for c in cluster_ids if int(np.sum(db_labels == c)) >= config.split_min_cluster_points]

    if len(valid_clusters) <= 1:
        return [plane]

    normal = np.asarray(plane.normal, dtype=np.float64)
    children = []
    for i, cluster_id in enumerate(valid_clusters):
        mask = db_labels == cluster_id
        sub_indices = indices[mask]
        sub_points = plane_points[mask]
        sub_centroid = sub_points.mean(axis=0)
        suffix = chr(ord("a") + i) if i < 26 else f"_{i + 1}"
        suggested = _suggest_label(normal, sub_centroid, points, config)
        confirmed = inherited_confirmed_label if inherited_confirmed_label is not None else suggested
        children.append(Plane(
            plane_id=f"{plane.plane_id}{suffix}",
            space_id=plane.space_id,
            coefficients=list(plane.coefficients),
            normal=list(plane.normal),
            centroid=sub_centroid.tolist(),
            point_count=int(len(sub_indices)),
            point_indices=sub_indices.tolist(),
            suggested_label=suggested,
            confirmed_label=confirmed,
        ))
    return children


def segment_planes(points: np.ndarray, space_id: str, config: PlaneSegmentationConfig | None = None) -> list:
    """pointsから複数平面をRANSACで反復抽出し、list[Plane]を返す。

    point_indicesは、引数pointsのインデックス(0-origin)をそのまま指す。
    後段(plane_to_voxel_labels.build_voxel_labels)で同じpoints配列を
    再利用する前提(Base Mapファイルを再読み込みして同じ順序で得られる
    ことに依存する)。
    """
    import open3d as o3d

    if points is None or len(points) == 0:
        raise ValueError("points が空です(平面抽出には点群データが必要です)。")

    config = config or PlaneSegmentationConfig()
    points = np.asarray(points, dtype=np.float64)

    remaining_idx = np.arange(len(points))
    remaining_points = points.copy()
    planes = []
    plane_counter = 0

    while len(remaining_points) >= max(config.min_plane_points, config.ransac_n) and plane_counter < config.max_planes:
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(remaining_points)
        plane_model, inlier_local_idx = pcd.segment_plane(
            distance_threshold=config.distance_threshold,
            ransac_n=config.ransac_n,
            num_iterations=config.num_iterations,
        )
        inlier_local_idx = np.asarray(inlier_local_idx, dtype=np.int64)
        if len(inlier_local_idx) < config.min_plane_points:
            break

        a, b, c, d = (float(v) for v in plane_model)
        normal = np.array([a, b, c], dtype=np.float64)
        norm_len = np.linalg.norm(normal)
        if norm_len > 0:
            normal = normal / norm_len

        original_idx = remaining_idx[inlier_local_idx]
        plane_points = points[original_idx]
        centroid = plane_points.mean(axis=0)

        plane_counter += 1
        suggested = _suggest_label(normal, centroid, points, config)
        parent_plane = Plane(
            plane_id=f"P{plane_counter:03d}",
            space_id=space_id,
            coefficients=[a, b, c, d],
            normal=normal.tolist(),
            centroid=centroid.tolist(),
            point_count=int(len(original_idx)),
            point_indices=original_idx.tolist(),
            suggested_label=suggested,
            confirmed_label=suggested,
        )
        # 空間的に非連続なcoplanar clusterが1つのPlaneにまとまらないよう、
        # 検出直後にDBSCANで再分割する(分割不要ならparent_planeを1件返す、
        # split_plane_by_connectivity参照)。
        planes.extend(split_plane_by_connectivity(parent_plane, points, config))

        mask = np.ones(len(remaining_points), dtype=bool)
        mask[inlier_local_idx] = False
        remaining_points = remaining_points[mask]
        remaining_idx = remaining_idx[mask]

    return planes
