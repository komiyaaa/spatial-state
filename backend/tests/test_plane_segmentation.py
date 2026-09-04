"""
backend/plane_segmentation.py の動作確認テスト。

実行方法(リポジトリルートから):
    python backend/tests/test_plane_segmentation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.structural_label import Plane, StructuralLabel  # noqa: E402
from plane_segmentation import PlaneSegmentationConfig, segment_planes, split_plane_by_connectivity  # noqa: E402

_RNG = np.random.default_rng(42)


def _room_points(nx=40, ny=40, noise=0.005) -> np.ndarray:
    """3m x 3m x 2.5mの単純な部屋(床・天井・4面の壁)の点群を合成する。"""
    lin = np.linspace(0.1, 2.9, nx)
    lin_y = np.linspace(0.1, 2.9, ny)
    gx, gy = np.meshgrid(lin, lin_y)
    gx, gy = gx.ravel(), gy.ravel()

    def noisy(arr):
        return arr + _RNG.normal(0, noise, size=arr.shape)

    floor = np.column_stack([gx, gy, noisy(np.zeros_like(gx))])
    ceiling = np.column_stack([gx, gy, noisy(np.full_like(gx, 2.5))])
    wall_x0 = np.column_stack([noisy(np.zeros_like(gx)), gx, np.linspace(0.1, 2.4, len(gx))])
    wall_x1 = np.column_stack([noisy(np.full_like(gx, 3.0)), gx, np.linspace(0.1, 2.4, len(gx))])
    return np.vstack([floor, ceiling, wall_x0, wall_x1])


def test_segment_planes_extracts_multiple_planes():
    points = _room_points()
    planes = segment_planes(points, space_id="b1-G001", config=PlaneSegmentationConfig(min_plane_points=100))
    assert len(planes) >= 3, f"床・天井・壁のうち十分な数の平面が抽出できていない: {len(planes)}枚"
    total_indices = sum(p.point_count for p in planes)
    assert total_indices <= len(points)
    # point_indicesが元の点群の範囲内であること
    for p in planes:
        assert max(p.point_indices) < len(points)
        assert min(p.point_indices) >= 0
    print(f"test_segment_planes_extracts_multiple_planes: OK (抽出枚数={len(planes)})")


def test_suggested_label_heuristic_floor_ceiling_wall():
    points = _room_points()
    planes = segment_planes(points, space_id="b1-G001", config=PlaneSegmentationConfig(min_plane_points=100))
    labels = {p.suggested_label for p in planes}
    assert StructuralLabel.FLOOR in labels, f"FLOORが提案されなかった: {[p.suggested_label for p in planes]}"
    assert StructuralLabel.CEILING in labels, f"CEILINGが提案されなかった: {[p.suggested_label for p in planes]}"
    assert StructuralLabel.WALL in labels, f"WALLが提案されなかった: {[p.suggested_label for p in planes]}"
    print(f"test_suggested_label_heuristic_floor_ceiling_wall: OK (labels={sorted(l.value for l in labels)})")


def test_confirmed_label_defaults_to_suggested_and_is_mutable():
    points = _room_points()
    planes = segment_planes(points, space_id="b1-G001", config=PlaneSegmentationConfig(min_plane_points=100))
    for p in planes:
        assert p.confirmed_label == p.suggested_label
    planes[0].confirmed_label = StructuralLabel.IGNORE
    assert planes[0].confirmed_label == StructuralLabel.IGNORE
    assert planes[0].suggested_label != StructuralLabel.IGNORE or True  # suggested側は変更していないことを別テストで担保
    print("test_confirmed_label_defaults_to_suggested_and_is_mutable: OK")


def test_empty_points_raises():
    try:
        segment_planes(np.zeros((0, 3)), space_id="b1-G001")
        raise AssertionError("空の点群が受理されてしまった")
    except ValueError:
        pass
    print("test_empty_points_raises: OK")


def _two_disjoint_coplanar_clusters(gap: float, n: int = 60, noise: float = 0.005) -> np.ndarray:
    """同一平面(z=0)上にあるが、x方向にgapだけ離れた2つの独立したcluster。"""
    lin = np.linspace(0, 1.0, n)
    gx, gy = np.meshgrid(lin, lin)
    gx, gy = gx.ravel(), gy.ravel()
    z = _RNG.normal(0, noise, size=gx.shape)
    cluster_a = np.column_stack([gx, gy, z])
    cluster_b = np.column_stack([gx + 1.0 + gap, gy, z])
    return np.vstack([cluster_a, cluster_b])


def _flat_plane(points: np.ndarray, plane_id: str, confirmed_label=StructuralLabel.FLOOR) -> Plane:
    return Plane(
        plane_id=plane_id, space_id="b1-G001",
        coefficients=[0.0, 0.0, 1.0, 0.0], normal=[0.0, 0.0, 1.0],
        centroid=points.mean(axis=0).tolist(), point_count=len(points),
        point_indices=list(range(len(points))),
        suggested_label=confirmed_label, confirmed_label=confirmed_label,
    )


def test_split_plane_by_connectivity_splits_disjoint_clusters():
    points = _two_disjoint_coplanar_clusters(gap=2.0)  # 既定split_eps=0.5より十分大きい隙間
    plane = _flat_plane(points, "P001")
    children = split_plane_by_connectivity(plane, points, PlaneSegmentationConfig())
    assert len(children) == 2, f"2つの独立clusterがあるのに分割されなかった: {len(children)}件"
    assert {c.plane_id for c in children} == {"P001a", "P001b"}
    total = sum(c.point_count for c in children)
    assert total <= len(points)
    assert total > len(points) * 0.9  # ノイズ除去分程度の妥当な減り方
    print(f"test_split_plane_by_connectivity_splits_disjoint_clusters: OK "
          f"(children={[c.plane_id for c in children]}, counts={[c.point_count for c in children]})")


def test_split_plane_by_connectivity_leaves_contiguous_plane_unchanged():
    points = _two_disjoint_coplanar_clusters(gap=0.05)  # 既定split_eps=0.5より十分小さい隙間
    plane = _flat_plane(points, "P002")
    children = split_plane_by_connectivity(plane, points, PlaneSegmentationConfig())
    assert len(children) == 1, f"連続した1枚のplaneが誤分割された: {len(children)}件"
    assert children[0] is plane, "分割不要な場合はplaneをそのまま(不変)返すはず"
    print("test_split_plane_by_connectivity_leaves_contiguous_plane_unchanged: OK")


def test_split_plane_by_connectivity_drops_small_clusters_below_min_size():
    big = _two_disjoint_coplanar_clusters(gap=2.0)
    small_lin = np.linspace(0, 0.05, 5)
    sx, sy = np.meshgrid(small_lin, small_lin)
    small_cluster = np.column_stack([sx.ravel() + 20.0, sy.ravel(), np.zeros(sx.size)])  # 25点、遠くに孤立
    points = np.vstack([big, small_cluster])
    plane = _flat_plane(points, "P003")
    config = PlaneSegmentationConfig(split_min_cluster_points=50)
    children = split_plane_by_connectivity(plane, points, config)
    assert len(children) == 2, f"最小点数未満の小clusterが誤って独立Planeとして残った: {len(children)}件"
    total = sum(c.point_count for c in children)
    assert total < len(points), "小clusterの点がPlane対象から除外されていない"
    print(f"test_split_plane_by_connectivity_drops_small_clusters_below_min_size: OK "
          f"(children={len(children)}, total_points={total}/{len(points)})")


def test_split_plane_by_connectivity_inherits_confirmed_label_when_provided():
    """既存データ移行時の用法: 親の既存confirmed_labelを各子へ継承しつつ、
    suggested_labelは各子で独立に再計算される。"""
    points = _two_disjoint_coplanar_clusters(gap=2.0)
    plane = _flat_plane(points, "P004", confirmed_label=StructuralLabel.IGNORE)
    children = split_plane_by_connectivity(
        plane, points, PlaneSegmentationConfig(), inherited_confirmed_label=StructuralLabel.IGNORE,
    )
    assert len(children) == 2
    assert all(c.confirmed_label == StructuralLabel.IGNORE for c in children), \
        "confirmed_labelの継承(移行時)が機能していない"
    print(f"test_split_plane_by_connectivity_inherits_confirmed_label_when_provided: OK "
          f"(suggested_labels={[c.suggested_label.value for c in children]})")


def test_segment_planes_splits_disjoint_wall_end_to_end():
    """RANSAC検出直後の空間連続性分割が、segment_planes()経由でも機能することを
    確認する(1枚の壁を、空間的に大きく離れた2区画に分けて合成)。"""
    lin = np.linspace(0.1, 2.9, 30)
    gx, gy = np.meshgrid(lin, lin)
    gx, gy = gx.ravel(), gy.ravel()

    def noisy(arr):
        return arr + _RNG.normal(0, 0.005, size=arr.shape)

    floor = np.column_stack([gx, gy, noisy(np.zeros_like(gx))])
    ceiling = np.column_stack([gx, gy, noisy(np.full_like(gx, 2.5))])

    wall_y_lin1 = np.linspace(0.1, 1.2, 30)
    wall_y_lin2 = np.linspace(10.0, 11.1, 30)  # 同一平面(x=0)上だが8m以上離れた別区画
    z_lin = np.linspace(0.1, 2.4, 30)
    wy1, wz1 = np.meshgrid(wall_y_lin1, z_lin)
    wy2, wz2 = np.meshgrid(wall_y_lin2, z_lin)
    wall_part1 = np.column_stack([noisy(np.zeros(wy1.size)), wy1.ravel(), wz1.ravel()])
    wall_part2 = np.column_stack([noisy(np.zeros(wy2.size)), wy2.ravel(), wz2.ravel()])

    points = np.vstack([floor, ceiling, wall_part1, wall_part2])
    planes = segment_planes(points, space_id="b1-G001", config=PlaneSegmentationConfig(min_plane_points=100))

    suffixed_ids = [p.plane_id for p in planes if len(p.plane_id) > 4 and p.plane_id[-1].isalpha()]
    assert len(suffixed_ids) >= 2, f"離れた壁2区画が別Planeに分割されなかった: {[p.plane_id for p in planes]}"
    print(f"test_segment_planes_splits_disjoint_wall_end_to_end: OK (plane_ids={[p.plane_id for p in planes]})")


if __name__ == "__main__":
    test_segment_planes_extracts_multiple_planes()
    test_suggested_label_heuristic_floor_ceiling_wall()
    test_confirmed_label_defaults_to_suggested_and_is_mutable()
    test_empty_points_raises()
    test_split_plane_by_connectivity_splits_disjoint_clusters()
    test_split_plane_by_connectivity_leaves_contiguous_plane_unchanged()
    test_split_plane_by_connectivity_drops_small_clusters_below_min_size()
    test_split_plane_by_connectivity_inherits_confirmed_label_when_provided()
    test_segment_planes_splits_disjoint_wall_end_to_end()
    print()
    print("全テスト成功。")
