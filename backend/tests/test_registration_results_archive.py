"""
backend/tests/test_registration_results_archive.py

VGICP精密位置合わせ結果の明示保存(registration_results/、2026-09-02追加)の
回帰テスト。

確認内容:
1. `_sanitize_path_component()`がパストラバーサル対策として機能すること
2. `_archive_registration_result()`が既存パイプラインの出力をコピーし、
   registration_result.jsonへfitness/voxel_size/rotation/translationを
   記録すること。同一source_stemでも複数回呼べば上書きされず個別に残ること
3. `GET /api/registration-results/<space_id>`・
   `GET /api/registration-results/<space_id>/<run_dir>/<filename>` が
   正しく一覧・配信すること(許可されていないファイル名は拒否すること)

実データ(backend/data/registration_results/等)には一切書き込まない
(server.REGISTRATION_RESULTS_DIRを一時ディレクトリへ差し替えてテストする)。

実行方法(リポジトリルートから):
    python backend/tests/test_registration_results_archive.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import server  # noqa: E402


def test_sanitize_path_component_blocks_traversal():
    assert server._sanitize_path_component("normal_name", "fallback") == "normal_name"
    assert server._sanitize_path_component("../../etc/passwd", "fallback") == "passwd"
    assert server._sanitize_path_component("..", "fallback") == "fallback"
    assert server._sanitize_path_component("", "fallback") == "fallback"
    assert server._sanitize_path_component("a/b\\c", "fallback") == "c"
    print("test_sanitize_path_component_blocks_traversal: OK")


def test_archive_registration_result_copies_and_records_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        original_dir = server.REGISTRATION_RESULTS_DIR
        server.REGISTRATION_RESULTS_DIR = tmp_path / "registration_results"
        try:
            rough_path = tmp_path / "rough.ply"
            precise_path = tmp_path / "precise.ply"
            scan_json_path = tmp_path / "scan.json"
            rough_path.write_bytes(b"ROUGH_CONTENT")
            precise_path.write_bytes(b"PRECISE_CONTENT")
            scan_json_path.write_text(json.dumps({"hits": {}}), encoding="utf-8")

            transform_info = {
                "voxel_size": 0.3, "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "translation": [1.0, 2.0, 3.0], "fitness_score": 0.0123,
            }

            run_dir_1 = server._archive_registration_result(
                space_id="test-space", source_filename="元scan.ply", uploaded_filename="source_registered_1.ply",
                rough_path=rough_path, precise_path=precise_path, scan_json_path=scan_json_path,
                fitness_score=0.0123, transform_info=transform_info,
            )
            assert (run_dir_1 / "precise_registered.ply").read_bytes() == b"PRECISE_CONTENT"
            assert (run_dir_1 / "rough_registered.ply").read_bytes() == b"ROUGH_CONTENT"
            assert (run_dir_1 / "scan.json").exists()

            result = json.loads((run_dir_1 / "registration_result.json").read_text(encoding="utf-8"))
            assert result["source_filename"] == "元scan.ply"
            assert result["fitness_score"] == 0.0123
            assert result["voxel_size"] == 0.3
            assert result["rotation"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            assert result["translation"] == [1.0, 2.0, 3.0]
            assert "migrated_from_legacy" not in result  # 新規実行分にはこのフラグを付けない

            # 同一source_stemでも、2回目の呼び出しは別run_id(=別フォルダ)になり、上書きしない
            run_dir_2 = server._archive_registration_result(
                space_id="test-space", source_filename="元scan.ply", uploaded_filename="source_registered_1.ply",
                rough_path=rough_path, precise_path=precise_path, scan_json_path=scan_json_path,
                fitness_score=0.0123, transform_info=transform_info,
            )
            assert run_dir_1 != run_dir_2
            assert run_dir_1.exists() and run_dir_2.exists(), "1回目の結果が消えている(上書きされた)"
        finally:
            server.REGISTRATION_RESULTS_DIR = original_dir
    print("test_archive_registration_result_copies_and_records_metadata: OK")


def test_archive_registration_result_null_transform_when_none():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        original_dir = server.REGISTRATION_RESULTS_DIR
        server.REGISTRATION_RESULTS_DIR = tmp_path / "registration_results"
        try:
            rough_path = tmp_path / "rough.ply"
            precise_path = tmp_path / "precise.ply"
            rough_path.write_bytes(b"R")
            precise_path.write_bytes(b"P")

            run_dir = server._archive_registration_result(
                space_id="test-space", source_filename=None, uploaded_filename="source_registered_2.ply",
                rough_path=rough_path, precise_path=precise_path, scan_json_path=None,
                fitness_score=None, transform_info=None,
            )
            result = json.loads((run_dir / "registration_result.json").read_text(encoding="utf-8"))
            assert result["rotation"] is None
            assert result["translation"] is None
            assert result["source_filename"] is None
            assert result["scan_json_path"] is None
        finally:
            server.REGISTRATION_RESULTS_DIR = original_dir
    print("test_archive_registration_result_null_transform_when_none: OK")


def test_api_list_and_serve_registration_results():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        original_dir = server.REGISTRATION_RESULTS_DIR
        server.REGISTRATION_RESULTS_DIR = tmp_path / "registration_results"
        try:
            rough_path = tmp_path / "rough.ply"
            precise_path = tmp_path / "precise.ply"
            rough_path.write_bytes(b"ROUGH")
            precise_path.write_bytes(b"PRECISE_BYTES")

            server._archive_registration_result(
                space_id="api-test-space", source_filename="scanA.ply", uploaded_filename="source_registered_a.ply",
                rough_path=rough_path, precise_path=precise_path, scan_json_path=None,
                fitness_score=0.05, transform_info={"voxel_size": 0.5, "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation": [0, 0, 0], "fitness_score": 0.05},
            )

            client = server.app.test_client()

            list_resp = client.get("/api/registration-results/api-test-space")
            assert list_resp.status_code == 200
            data = list_resp.get_json()
            assert len(data["results"]) == 1
            assert data["results"][0]["source_filename"] == "scanA.ply"
            run_dir_name = data["results"][0]["run_dir"]

            # 未知のspace_idは空一覧(エラーにしない)
            empty_resp = client.get("/api/registration-results/no-such-space")
            assert empty_resp.status_code == 200
            assert empty_resp.get_json()["results"] == []

            # precise_registered.plyの配信(GUIから確認する対象)。
            # send_from_directory()が返すレスポンスはファイルハンドルを保持する
            # ため、Windowsで一時ディレクトリを片付けられるよう明示的にclose()する。
            ply_resp = client.get(f"/api/registration-results/api-test-space/{run_dir_name}/precise_registered.ply")
            try:
                assert ply_resp.status_code == 200
                assert ply_resp.data == b"PRECISE_BYTES"
            finally:
                ply_resp.close()

            # 許可されていないファイル名は拒否する
            denied_resp = client.get(f"/api/registration-results/api-test-space/{run_dir_name}/../../../etc/passwd")
            assert denied_resp.status_code in (400, 404)
            denied_resp.close()

            bad_filename_resp = client.get(f"/api/registration-results/api-test-space/{run_dir_name}/registration_result.json.bak")
            assert bad_filename_resp.status_code == 400
            bad_filename_resp.close()
        finally:
            server.REGISTRATION_RESULTS_DIR = original_dir
    print("test_api_list_and_serve_registration_results: OK")


if __name__ == "__main__":
    test_sanitize_path_component_blocks_traversal()
    test_archive_registration_result_copies_and_records_metadata()
    test_archive_registration_result_null_transform_when_none()
    test_api_list_and_serve_registration_results()
    print()
    print("全テスト成功。")
