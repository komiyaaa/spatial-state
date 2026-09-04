"""
backend/tests/test_registration_result_global_export_api.py

POST /api/registration-results/<space_id>/<run_dir>/export-global の回帰テスト
(2026-09-03追加)。実データ(backend/data/)には一切書き込まない
(server.REGISTRATION_RESULTS_DIR・SPACE_DEF_DIR・local_space_repo・
spatial_resolution_result_repoを一時ディレクトリ/使い捨てインスタンスへ
差し替えてテストする)。

確認内容:
1. RESOLVED済みの場合、既存のexport_precise_registered_to_global()を通じて
   precise_registered_global.ply + metadataが生成され、レスポンスに
   success/space_id/run_id/target_epsg/output_artifact/metadata_artifactが
   含まれること。座標がworld_points_to_resolved_global()と一致すること。
   元precise_registered.plyが無変更であること。
2. 未RESOLVED(NO_ANCHOR等)の場合はfail-closed(409、error_codeで区別可能)
3. Spatial Resolutionが一度も実行されていない場合(404、NO_RESOLUTION_RESULT)
4. space_idがどのcomponentにも属さない場合(404、SPACE_NOT_IN_ANY_COMPONENT)
5. precise_registered.ply自体が存在しない場合(404、PRECISE_REGISTERED_NOT_FOUND)

実行方法(backendディレクトリから):
    python -m pytest tests/test_registration_result_global_export_api.py -v
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import open3d as o3d

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import server  # noqa: E402
from domain.component_placement import ComponentPlacementResult, ComponentStatus  # noqa: E402
from domain.global_resolution import ComponentGlobalResolution, GlobalResolutionStatus  # noqa: E402
from domain.spatial_resolution_result import ComponentResolutionResult  # noqa: E402
from domain.transform import RigidTransform2D  # noqa: E402
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402
from repositories.spatial_resolution_result_repository import SpatialResolutionResultRepository  # noqa: E402
from services.global_coordinate_service import world_points_to_resolved_global  # noqa: E402

SPACE_DEF = {
    "id": "b1-S1", "degree": 30.0, "rad": math.radians(30.0), "height": 3.0,
    "origin": [2.0, -1.0, 0.0], "unit-size": {"9": 0.12}, "bounds": [[0, 0, 0]] * 8,
}
TRANSFORM = RigidTransform2D(yaw_rad=math.radians(60.0), translation=(500.0, -300.0, 5.0))


def _make_ply(path: Path, points: np.ndarray) -> None:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    assert o3d.io.write_point_cloud(str(path), pcd)


class _Env:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)

        self._orig_reg_dir = server.REGISTRATION_RESULTS_DIR
        self._orig_space_def_dir = server.SPACE_DEF_DIR
        self._orig_local_space_repo = server.local_space_repo
        self._orig_resolution_repo = server.spatial_resolution_result_repo

        server.REGISTRATION_RESULTS_DIR = tmp_path / "registration_results"
        server.SPACE_DEF_DIR = tmp_path / "space_definitions"
        server.SPACE_DEF_DIR.mkdir(parents=True, exist_ok=True)
        (server.SPACE_DEF_DIR / "b1-S1.json").write_text(json.dumps(SPACE_DEF), encoding="utf-8")

        server.local_space_repo = LocalSpaceRepository(tmp_path / "registry", server.SPACE_DEF_DIR)
        server.local_space_repo.create(building_id="b1", tokutei_code="S1", floor=1, zoom_level=9)

        server.spatial_resolution_result_repo = SpatialResolutionResultRepository(
            tmp_path / "spatial_resolution_results",
        )

        self.tmp_path = tmp_path
        self.client = server.app.test_client()

        self.run_dir = server.REGISTRATION_RESULTS_DIR / "b1-S1" / "run-001"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.points = np.array([[0.0, 0.0, 0.0], [3.0, 1.0, 2.0], [-2.0, 4.0, 0.5]])
        self.precise_ply_path = self.run_dir / "precise_registered.ply"
        _make_ply(self.precise_ply_path, self.points)
        return self

    def save_resolution(self, status: GlobalResolutionStatus, member_ids=("b1-S1",)):
        placement = ComponentPlacementResult(
            component_id="comp-1", member_space_ids=list(member_ids), root_space_id=member_ids[0],
            transforms={sid: RigidTransform2D.identity() for sid in member_ids}, status=ComponentStatus.RESOLVED,
        )
        global_resolution = ComponentGlobalResolution(
            component_id="comp-1", status=status,
            member_transforms_to_global=({"b1-S1": TRANSFORM} if status == GlobalResolutionStatus.RESOLVED else {}),
            target_epsg=6677,
        )
        result = ComponentResolutionResult(component_id="comp-1", local_placement=placement, global_resolution=global_resolution)
        server.spatial_resolution_result_repo.save("b1", [result], target_epsg=6677)

    def __exit__(self, *exc):
        server.REGISTRATION_RESULTS_DIR = self._orig_reg_dir
        server.SPACE_DEF_DIR = self._orig_space_def_dir
        server.local_space_repo = self._orig_local_space_repo
        server.spatial_resolution_result_repo = self._orig_resolution_repo
        self._tmp.cleanup()


def test_export_success_matches_chain_and_leaves_source_unchanged():
    with _Env() as env:
        env.save_resolution(GlobalResolutionStatus.RESOLVED)
        before_bytes = env.precise_ply_path.read_bytes()

        resp = env.client.post("/api/registration-results/b1-S1/run-001/export-global")
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert data["success"] is True
        assert data["space_id"] == "b1-S1"
        assert data["run_id"] == "run-001"
        assert data["target_epsg"] == 6677
        assert "output_artifact" in data and "metadata_artifact" in data

        output_path = Path(data["output_artifact"])
        assert output_path.exists()
        metadata_path = Path(data["metadata_artifact"])
        assert metadata_path.exists()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["source_space_id"] == "b1-S1"
        assert metadata["source_run_id"] == "run-001"
        assert metadata["target_epsg"] == 6677

        result_pcd = o3d.io.read_point_cloud(str(output_path))
        exported = np.asarray(result_pcd.points)
        expected = world_points_to_resolved_global(env.points, "b1-S1", SPACE_DEF, ComponentGlobalResolution(
            component_id="comp-1", status=GlobalResolutionStatus.RESOLVED,
            member_transforms_to_global={"b1-S1": TRANSFORM}, target_epsg=6677,
        ))
        assert np.allclose(exported, expected, atol=1e-4)

        assert env.precise_ply_path.read_bytes() == before_bytes, "元precise_registered.plyが変更された"
        print("test_export_success_matches_chain_and_leaves_source_unchanged: OK")


def test_export_fails_closed_when_not_resolved():
    with _Env() as env:
        for status in (
            GlobalResolutionStatus.NO_ANCHOR,
            GlobalResolutionStatus.GLOBAL_CONFLICT,
            GlobalResolutionStatus.ANCHOR_INSUFFICIENT,
        ):
            env.save_resolution(status)
            resp = env.client.post("/api/registration-results/b1-S1/run-001/export-global")
            data = resp.get_json()
            assert resp.status_code == 409, data
            assert data["error_code"] == status.value, data
            assert not (env.run_dir / "precise_registered_global.ply").exists()
        print("test_export_fails_closed_when_not_resolved: OK (NO_ANCHOR/GLOBAL_CONFLICT/ANCHOR_INSUFFICIENT)")


def test_export_fails_when_no_resolution_result_saved():
    with _Env() as env:
        resp = env.client.post("/api/registration-results/b1-S1/run-001/export-global")
        data = resp.get_json()
        assert resp.status_code == 404, data
        assert data["error_code"] == "NO_RESOLUTION_RESULT"
        print("test_export_fails_when_no_resolution_result_saved: OK")


def test_export_fails_when_space_not_in_any_component():
    with _Env() as env:
        env.save_resolution(GlobalResolutionStatus.RESOLVED, member_ids=("other-space",))
        resp = env.client.post("/api/registration-results/b1-S1/run-001/export-global")
        data = resp.get_json()
        assert resp.status_code == 404, data
        assert data["error_code"] == "SPACE_NOT_IN_ANY_COMPONENT"
        print("test_export_fails_when_space_not_in_any_component: OK")


def test_export_fails_when_precise_ply_missing():
    with _Env() as env:
        env.save_resolution(GlobalResolutionStatus.RESOLVED)
        resp = env.client.post("/api/registration-results/b1-S1/no-such-run/export-global")
        data = resp.get_json()
        assert resp.status_code == 404, data
        assert data["error_code"] == "PRECISE_REGISTERED_NOT_FOUND"
        print("test_export_fails_when_precise_ply_missing: OK")


if __name__ == "__main__":
    test_export_success_matches_chain_and_leaves_source_unchanged()
    test_export_fails_closed_when_not_resolved()
    test_export_fails_when_no_resolution_result_saved()
    test_export_fails_when_space_not_in_any_component()
    test_export_fails_when_precise_ply_missing()
    print()
    print("全テスト成功。")
