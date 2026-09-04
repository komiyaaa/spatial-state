"""
backend/tests/test_convert_to_scan_json.py

Spatial State更新実験(新見附校舎)に向けた2件の修正(2026-09-02)の回帰テスト:

1. server._find_space_definition() が、tokutei_codeの完全一致で座標定義を
   解決すること(旧実装は前方一致だったため、orphan化した旧バージョン
   ファイル("G002v3.json"のような)がファイル名の辞書順で優先されてしまい、
   実データのG002で実際に誤った(voxel_size=0.1mの)CoordinateDefinitionが
   選ばれる不具合があった)。
2. server.convert_to_scan_json() が、固定のzoom_level(旧DEFAULT_ZOOM_LEVEL=9)
   ではなく、対象Local Space自身のCoordinateDefinitionのfinest zoomを使う
   こと。finest zoomの「番号」自体は部屋の大きさによって異なるが(小さい
   部屋ほど小さい番号になる)、対応するvoxel_sizeは常に0.03m
   (space_definition_generator.MIN_VOXEL_SIZE)であることを確認する。

実データ(backend/space_definitions/)には一切書き込まない
(server.SPACE_DEF_DIRを一時ディレクトリへ差し替えてテストする)。

実行方法(リポジトリルートから):
    python backend/tests/test_convert_to_scan_json.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import open3d as o3d

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import server  # noqa: E402
from space_definition_generator import finest_zoom_level, generate_space_definition  # noqa: E402


def _write_space_def(space_def_dir: Path, tokutei_code: str, space_def: dict) -> None:
    (space_def_dir / f"{tokutei_code}.json").write_text(
        json.dumps(space_def, ensure_ascii=False), encoding="utf-8"
    )


def _make_room_points(sx, sy, sz, nx=8, ny=8, nz=5):
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    return np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)


def _write_ply(points, path):
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    assert o3d.io.write_point_cloud(str(path), pcd)


def test_find_space_definition_exact_match_ignores_orphaned_version_file():
    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = Path(tmp)
        original_dir = server.SPACE_DEF_DIR
        server.SPACE_DEF_DIR = space_def_dir
        try:
            current = {
                "id": "G002", "degree": 0.0, "rad": 0.0, "height": 3.0,
                "origin": [0, 0, 0], "unit-size": {"0": 1.0}, "bounds": [[0, 0, 0]] * 8,
            }
            # ファイル名の辞書順では "G002v3.json" が "G002.json" より後になる
            # (旧・前方一致実装だとこちらが誤って選ばれていた)。
            orphaned = {
                "id": "G002v3", "degree": 0.0, "rad": 0.0, "height": 20.5,
                "origin": [0, 0, 0], "unit-size": {"0": 2.0}, "bounds": [[0, 0, 0]] * 8,
            }
            _write_space_def(space_def_dir, "G002", current)
            _write_space_def(space_def_dir, "G002v3", orphaned)

            resolved = server._find_space_definition("some_building-G002")
            assert resolved is not None
            assert resolved["id"] == "G002", f"orphanなG002v3.jsonが誤って選ばれた: {resolved}"
        finally:
            server.SPACE_DEF_DIR = original_dir
    print("test_find_space_definition_exact_match_ignores_orphaned_version_file: OK")


def test_find_space_definition_missing_file_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        original_dir = server.SPACE_DEF_DIR
        server.SPACE_DEF_DIR = Path(tmp)
        try:
            assert server._find_space_definition("b1-NOSUCHCODE") is None
        finally:
            server.SPACE_DEF_DIR = original_dir
    print("test_find_space_definition_missing_file_returns_none: OK")


def test_convert_to_scan_json_uses_finest_zoom_from_space_definition():
    """max zoomが9ではない(=DEFAULT_ZOOM_LEVEL旧値と異なる)Local Spaceでも、
    finest=0.03mでscan_jsonが生成されることを確認する(小さい部屋・大きい部屋の
    両方で、finestのzoom番号自体は異なるが、voxel_sizeは常に0.03mになる)。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def_dir = tmp_path / "space_definitions"
        space_def_dir.mkdir()
        original_dir = server.SPACE_DEF_DIR
        server.SPACE_DEF_DIR = space_def_dir
        try:
            small_points = _make_room_points(2.0, 1.5, 2.0)
            small_def = generate_space_definition(small_points, "SMALLROOM", rotation_rad=0.0)
            finest_small = finest_zoom_level(small_def)
            _write_space_def(space_def_dir, "SMALLROOM", small_def)

            large_points = _make_room_points(40.0, 30.0, 3.0)
            large_def = generate_space_definition(large_points, "LARGEROOM", rotation_rad=0.0)
            finest_large = finest_zoom_level(large_def)
            _write_space_def(space_def_dir, "LARGEROOM", large_def)

            assert finest_small != finest_large, "テスト前提(finest zoom番号が異なる)が崩れている"
            assert finest_small != 9 or finest_large != 9, "テスト前提(旧DEFAULT_ZOOM_LEVEL=9と異なる)が崩れている"

            for tokutei_code, expected_finest, points in (
                ("SMALLROOM", finest_small, small_points),
                ("LARGEROOM", finest_large, large_points),
            ):
                space_def = server._find_space_definition(f"b1-{tokutei_code}")
                assert space_def["unit-size"][str(expected_finest)] == 0.03, (
                    f"{tokutei_code}: finest zoomのvoxel_sizeが0.03mではない: "
                    f"{space_def['unit-size'][str(expected_finest)]}"
                )

                ply_path = tmp_path / f"{tokutei_code}.ply"
                _write_ply(points, ply_path)
                out_dir = tmp_path / "scan_json" / tokutei_code
                out_path = server.convert_to_scan_json(ply_path, out_dir, f"b1-{tokutei_code}")
                result = json.loads(out_path.read_text(encoding="utf-8"))

                assert result["zoom_level"] == expected_finest, (
                    f"{tokutei_code}: zoom_level={result['zoom_level']} != finest={expected_finest}"
                )
                assert result["voxel_count"] > 0, f"{tokutei_code}: voxelが1件も生成されなかった"
                for sid in result["hits"]:
                    zoom_str = sid.split("/")[0]
                    assert zoom_str == str(expected_finest), (
                        f"{tokutei_code}: hitsのspatial_id '{sid}' がfinest zoom({expected_finest})で生成されていない"
                    )
        finally:
            server.SPACE_DEF_DIR = original_dir
    print("test_convert_to_scan_json_uses_finest_zoom_from_space_definition: OK")


def test_convert_to_scan_json_explicit_zoom_level_override_still_works():
    """zoom_levelを明示的に渡した場合は、finest自動取得より優先されること
    (テスト・将来の用途のための後方互換パラメータ)。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        space_def_dir = tmp_path / "space_definitions"
        space_def_dir.mkdir()
        original_dir = server.SPACE_DEF_DIR
        server.SPACE_DEF_DIR = space_def_dir
        try:
            points = _make_room_points(2.0, 1.5, 2.0)
            space_def = generate_space_definition(points, "OVERRIDEROOM", rotation_rad=0.0)
            finest = finest_zoom_level(space_def)
            _write_space_def(space_def_dir, "OVERRIDEROOM", space_def)
            override_zoom = max(0, finest - 1)
            assert override_zoom != finest, "テスト前提(overrideがfinestと異なる)が崩れている"

            ply_path = tmp_path / "src.ply"
            _write_ply(points, ply_path)
            out_path = server.convert_to_scan_json(
                ply_path, tmp_path / "out", "b1-OVERRIDEROOM", zoom_level=override_zoom,
            )
            result = json.loads(out_path.read_text(encoding="utf-8"))
            assert result["zoom_level"] == override_zoom
        finally:
            server.SPACE_DEF_DIR = original_dir
    print("test_convert_to_scan_json_explicit_zoom_level_override_still_works: OK")


def test_disposable_space_resolves_current_definition_with_finest_003m():
    """(2026-09-03修正: 元は実データ ichigaya_tamachi-G002 を直接参照していたが、
    G002はLocal Space削除機能により意図的に削除された(復元しない、ユーザー指示)。
    実データ依存だったこのテストを、他のテストと同じ使い捨てfixtureパターン
    (server.SPACE_DEF_DIRを一時ディレクトリへ差し替え)へ置き換えた。
    generate_space_definition()が実際に生成したCoordinateDefinitionが、
    space_id完全一致で正しく解決され(正式な永続化キー、legacy tokutei_code
    フォールバックではない経路)、finest zoomのvoxel_sizeが常に0.03mで
    あることを確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = Path(tmp)
        original_dir = server.SPACE_DEF_DIR
        server.SPACE_DEF_DIR = space_def_dir
        try:
            space_id = "disposable_building-G900"
            points = _make_room_points(6.0, 8.0, 3.0)
            space_def = generate_space_definition(points, space_id, rotation_rad=0.0)
            _write_space_def(space_def_dir, space_id, space_def)

            resolved = server._find_space_definition(space_id)
            assert resolved is not None
            assert resolved["id"] == space_id, resolved["id"]
            finest = finest_zoom_level(resolved)
            assert resolved["unit-size"][str(finest)] == 0.03
        finally:
            server.SPACE_DEF_DIR = original_dir
    print("test_disposable_space_resolves_current_definition_with_finest_003m: OK")


if __name__ == "__main__":
    test_find_space_definition_exact_match_ignores_orphaned_version_file()
    test_find_space_definition_missing_file_returns_none()
    test_convert_to_scan_json_uses_finest_zoom_from_space_definition()
    test_convert_to_scan_json_explicit_zoom_level_override_still_works()
    test_disposable_space_resolves_current_definition_with_finest_003m()
    print()
    print("全テスト成功。")
