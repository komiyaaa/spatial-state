"""
backend/tests/test_global_export_service.py

services/global_export_service.py の動作確認テスト。使い捨ての一時ディレクトリ
にのみ合成PLYを作成し、実データ(registration_results/等)には一切触れない。

確認内容:
- XYZ変換がworld→intrinsic→global chain(services.global_coordinate_service.
  world_points_to_resolved_global())と一致すること
- RGB(colors)が保持されること
- 複数点であること
- 非ゼロCoordinateDefinition rotation・非ゼロresolved yaw/translationの組み合わせ
- Global未RESOLVEDの場合はexportされない(出力ファイルが作られない)こと
- 元precise_registered.plyが一切変更されないこと(ハッシュ比較)

実行方法(backendディレクトリから):
    python -m pytest tests/test_global_export_service.py -v
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import open3d as o3d
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from domain.global_resolution import ComponentGlobalResolution, GlobalResolutionStatus  # noqa: E402
from domain.transform import RigidTransform2D  # noqa: E402
from services.global_coordinate_service import (  # noqa: E402
    GlobalCoordinateResolutionError,
    world_points_to_resolved_global,
)
from services.global_export_service import GlobalExportError, export_precise_registered_to_global  # noqa: E402


def _make_ply(path: Path, points: np.ndarray, colors: np.ndarray | None = None) -> None:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    if colors is not None:
        pcd.colors = o3d.utility.Vector3dVector(colors)
    assert o3d.io.write_point_cloud(str(path), pcd)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolved(status, transforms=None, target_epsg=6677) -> ComponentGlobalResolution:
    return ComponentGlobalResolution(
        component_id="comp-1", status=status, member_transforms_to_global=transforms or {},
        target_epsg=target_epsg,
    )


def test_xyz_matches_world_to_intrinsic_to_global_chain():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def = {"origin": [1.0, -2.0, 0.5], "rad": math.radians(23.0)}
        transform = RigidTransform2D(yaw_rad=math.radians(41.0), translation=(50.0, -30.0, 2.0))
        gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": transform})

        points = np.array([[0.0, 0.0, 0.0], [3.3, 4.4, 1.1], [-2.0, 5.0, 0.2]])
        src = tmp_path / "precise_registered.ply"
        _make_ply(src, points)

        out = tmp_path / "precise_registered_global.ply"
        metadata = export_precise_registered_to_global(
            src, out, "s1", space_def, gr, run_id="run-001", resolved_at="2026-09-03T00:00:00",
        )

        result_pcd = o3d.io.read_point_cloud(str(out))
        exported_points = np.asarray(result_pcd.points)

        expected = world_points_to_resolved_global(points, "s1", space_def, gr)
        assert np.allclose(exported_points, expected, atol=1e-5), (
            f"exportされた座標がworld_points_to_resolved_global()の結果と不一致: "
            f"{exported_points} vs {expected}"
        )
        assert metadata["point_count"] == 3
        print(f"test_xyz_matches_world_to_intrinsic_to_global_chain: OK ({exported_points})")


def test_rgb_colors_preserved():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def = {"origin": [0.0, 0.0, 0.0], "rad": 0.0}
        gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": RigidTransform2D.identity()})

        points = np.array([[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]])
        colors = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        src = tmp_path / "precise_registered.ply"
        _make_ply(src, points, colors)

        out = tmp_path / "precise_registered_global.ply"
        metadata = export_precise_registered_to_global(src, out, "s1", space_def, gr, run_id="run-002")

        result_pcd = o3d.io.read_point_cloud(str(out))
        exported_colors = np.asarray(result_pcd.colors)
        assert metadata["colors_preserved"] is True
        assert np.allclose(exported_colors, colors, atol=1e-3), f"RGBが保持されていない: {exported_colors}"
        print(f"test_rgb_colors_preserved: OK ({exported_colors})")


def test_multiple_points():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def = {"origin": [5.0, 5.0, 0.0], "rad": math.radians(-12.0)}
        transform = RigidTransform2D(yaw_rad=math.radians(8.0), translation=(1000.0, 2000.0, 10.0))
        gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": transform})

        rng = np.random.default_rng(42)
        points = rng.uniform(-10, 10, size=(500, 3))
        src = tmp_path / "precise_registered.ply"
        _make_ply(src, points)

        out = tmp_path / "precise_registered_global.ply"
        metadata = export_precise_registered_to_global(src, out, "s1", space_def, gr, run_id="run-003")

        result_pcd = o3d.io.read_point_cloud(str(out))
        exported_points = np.asarray(result_pcd.points)
        expected = world_points_to_resolved_global(points, "s1", space_def, gr)
        assert exported_points.shape == (500, 3)
        assert np.allclose(exported_points, expected, atol=1e-4)
        assert metadata["point_count"] == 500
        print("test_multiple_points: OK (500点)")


def test_nonzero_rotation_yaw_translation_combination():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def = {"origin": [12.3, -4.5, 1.0], "rad": math.radians(77.0)}
        transform = RigidTransform2D(yaw_rad=math.radians(-33.0), translation=(-500.0, 800.0, -5.0))
        gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": transform})

        points = np.array([[10.0, -3.0, 2.0], [0.5, 0.5, 0.5]])
        src = tmp_path / "precise_registered.ply"
        _make_ply(src, points)
        out = tmp_path / "precise_registered_global.ply"
        export_precise_registered_to_global(src, out, "s1", space_def, gr, run_id="run-004")

        result_pcd = o3d.io.read_point_cloud(str(out))
        exported_points = np.asarray(result_pcd.points)
        expected = world_points_to_resolved_global(points, "s1", space_def, gr)
        assert np.allclose(exported_points, expected, atol=1e-4)
        print("test_nonzero_rotation_yaw_translation_combination: OK")


def test_unresolved_is_not_exported():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def = {"origin": [0.0, 0.0, 0.0], "rad": 0.0}
        gr = _resolved(GlobalResolutionStatus.NO_ANCHOR)  # RESOLVEDではない

        points = np.array([[1.0, 2.0, 3.0]])
        src = tmp_path / "precise_registered.ply"
        _make_ply(src, points)
        out = tmp_path / "precise_registered_global.ply"

        with pytest.raises(GlobalCoordinateResolutionError):
            export_precise_registered_to_global(src, out, "s1", space_def, gr, run_id="run-005")

        assert not out.exists(), "未RESOLVEDなのに出力ファイルが作られてしまった"
        assert not out.with_suffix(out.suffix + ".meta.json").exists()
        print("test_unresolved_is_not_exported: OK (出力ファイルなし)")


def test_output_path_same_as_source_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def = {"origin": [0.0, 0.0, 0.0], "rad": 0.0}
        gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": RigidTransform2D.identity()})
        points = np.array([[1.0, 2.0, 3.0]])
        src = tmp_path / "precise_registered.ply"
        _make_ply(src, points)

        with pytest.raises(GlobalExportError):
            export_precise_registered_to_global(src, src, "s1", space_def, gr, run_id="run-006")
        print("test_output_path_same_as_source_rejected: OK")


def test_source_precise_registered_ply_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def = {"origin": [3.0, 1.0, 0.0], "rad": math.radians(15.0)}
        transform = RigidTransform2D(yaw_rad=math.radians(5.0), translation=(10.0, 20.0, 0.0))
        gr = _resolved(GlobalResolutionStatus.RESOLVED, {"s1": transform})

        points = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
        colors = np.array([[0.5, 0.5, 0.5], [0.1, 0.2, 0.3]])
        src = tmp_path / "precise_registered.ply"
        _make_ply(src, points, colors)
        before_hash = _file_hash(src)
        before_bytes = src.read_bytes()

        out = tmp_path / "precise_registered_global.ply"
        export_precise_registered_to_global(src, out, "s1", space_def, gr, run_id="run-007")

        after_hash = _file_hash(src)
        assert before_hash == after_hash, "元のprecise_registered.plyが変更されてしまった"
        assert src.read_bytes() == before_bytes
        assert out.exists() and out != src
        print("test_source_precise_registered_ply_unchanged: OK (元ファイルはバイト単位で無変更)")


if __name__ == "__main__":
    test_xyz_matches_world_to_intrinsic_to_global_chain()
    test_rgb_colors_preserved()
    test_multiple_points()
    test_nonzero_rotation_yaw_translation_combination()
    test_unresolved_is_not_exported()
    test_output_path_same_as_source_rejected()
    test_source_precise_registered_ply_unchanged()
    print()
    print("全テスト成功。")
