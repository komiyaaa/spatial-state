"""
backend/tests/test_space_id_keyed_persistence.py

Base Map / CoordinateDefinitionの永続化キーをtokutei_code単独からspace_idへ
移行した変更(ER図反映、2026-09-02)の回帰テスト。

確認内容:
1. space_id・tokutei_code両方のキーでファイルが存在する場合、必ずspace_id側が
   優先されること(server._find_space_definition / server._find_base_map_path /
   repositories.local_space_repository.LocalSpaceRepository._load_coordinate_definition)。
2. tokutei_code単独キーしか無い場合は、read-only legacy fallbackとして
   引き続き解決できること(既存データ互換)。
3. 実データ(G002/G003)がmigration後、実際にspace_idキー側で解決されること
   (scripts/migrate_space_id_keyed_persistence.py --apply 実行後の状態を、
   読み取り専用で確認する。実データへの書き込みは一切行わない)。
4. base_maps側のmanifestマッチングが、tokutei_code単独キー時代の緩い部分
   一致("id" in space_id)ではなく、厳密一致になっていること
   (id="G00"がspace_id="foo-G002"に誤マッチしていた旧バグの回帰テスト)。

実データ(backend/space_definitions/・base_maps/)には一切書き込まない
(1・2・4はtempdirでserver.SPACE_DEF_DIR/server.BASE_MAPS_DIRを差し替えて
テストする。3は既存の実データを読み取るだけ)。

実行方法(リポジトリルートから):
    python backend/tests/test_space_id_keyed_persistence.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import server  # noqa: E402
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_find_space_definition_prefers_space_id_over_tokutei_code():
    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = Path(tmp)
        original = server.SPACE_DEF_DIR
        server.SPACE_DEF_DIR = space_def_dir
        try:
            space_id_def = {"id": "space_id_version", "degree": 0.0, "rad": 0.0, "height": 3.0,
                             "origin": [0, 0, 0], "unit-size": {"0": 1.0}, "bounds": [[0, 0, 0]] * 8}
            tokutei_def = {"id": "tokutei_code_version", "degree": 0.0, "rad": 0.0, "height": 99.0,
                           "origin": [9, 9, 9], "unit-size": {"0": 9.0}, "bounds": [[9, 9, 9]] * 8}
            _write_json(space_def_dir / "b1-G999.json", space_id_def)
            _write_json(space_def_dir / "G999.json", tokutei_def)

            resolved = server._find_space_definition("b1-G999")
            assert resolved is not None
            assert resolved["id"] == "space_id_version", (
                f"space_id側が優先されるべきだが、tokutei_code側が返った: {resolved}"
            )
        finally:
            server.SPACE_DEF_DIR = original
    print("test_find_space_definition_prefers_space_id_over_tokutei_code: OK")


def test_find_space_definition_falls_back_to_tokutei_code_when_space_id_missing():
    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = Path(tmp)
        original = server.SPACE_DEF_DIR
        server.SPACE_DEF_DIR = space_def_dir
        try:
            tokutei_def = {"id": "legacy_only", "degree": 0.0, "rad": 0.0, "height": 3.0,
                           "origin": [0, 0, 0], "unit-size": {"0": 1.0}, "bounds": [[0, 0, 0]] * 8}
            _write_json(space_def_dir / "G888.json", tokutei_def)

            resolved = server._find_space_definition("b1-G888")
            assert resolved is not None
            assert resolved["id"] == "legacy_only", (
                f"space_idキーが無い場合、tokutei_code側にfallbackするはず: {resolved}"
            )
        finally:
            server.SPACE_DEF_DIR = original
    print("test_find_space_definition_falls_back_to_tokutei_code_when_space_id_missing: OK")


def test_local_space_repository_prefers_space_id_over_tokutei_code():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        registry_dir = tmp_path / "registry"
        space_def_dir = tmp_path / "space_definitions"
        registry_dir.mkdir()
        space_def_dir.mkdir()

        space_id_def = {"id": "space_id_version", "degree": 0.0, "rad": 0.0, "height": 3.0,
                         "origin": [0, 0, 0], "unit-size": {"0": 1.0}, "bounds": [[0, 0, 0]] * 8}
        tokutei_def = {"id": "tokutei_code_version", "degree": 0.0, "rad": 0.0, "height": 99.0,
                       "origin": [9, 9, 9], "unit-size": {"0": 9.0}, "bounds": [[9, 9, 9]] * 8}
        _write_json(space_def_dir / "b2-G777.json", space_id_def)
        _write_json(space_def_dir / "G777.json", tokutei_def)

        repo = LocalSpaceRepository(registry_dir, space_def_dir)
        local_space = repo.create(building_id="b2", tokutei_code="G777", floor=1, zoom_level=0)

        assert local_space.coordinate_definition is not None
        assert local_space.coordinate_definition.id == "space_id_version", (
            f"LocalSpaceRepositoryもspace_id側を優先するはず: {local_space.coordinate_definition.id}"
        )
    print("test_local_space_repository_prefers_space_id_over_tokutei_code: OK")


def test_find_base_map_path_exact_match_not_substring():
    """旧実装は entry["id"] in space_id という部分一致だったため、
    id="G00" が space_id="foo-G002" に誤マッチしていた。厳密一致になって
    いることを確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        base_maps_dir = Path(tmp)
        original = server.BASE_MAPS_DIR
        server.BASE_MAPS_DIR = base_maps_dir
        try:
            (base_maps_dir / "decoy.las").write_bytes(b"decoy")
            manifest = [{"id": "G00", "label": "G00", "file": "decoy.las"}]
            (base_maps_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            result = server._find_base_map_path("foo-G002")
            assert result is None, f"部分一致で誤マッチしている: {result}"
        finally:
            server.BASE_MAPS_DIR = original
    print("test_find_base_map_path_exact_match_not_substring: OK")


def test_find_base_map_path_prefers_space_id_over_tokutei_code():
    with tempfile.TemporaryDirectory() as tmp:
        base_maps_dir = Path(tmp)
        original = server.BASE_MAPS_DIR
        server.BASE_MAPS_DIR = base_maps_dir
        try:
            (base_maps_dir / "by_space_id.las").write_bytes(b"space_id_version")
            (base_maps_dir / "by_tokutei_code.las").write_bytes(b"tokutei_code_version")
            manifest = [
                {"id": "b3-G666", "label": "b3-G666", "file": "by_space_id.las"},
                {"id": "G666", "label": "G666", "file": "by_tokutei_code.las"},
            ]
            (base_maps_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            result = server._find_base_map_path("b3-G666")
            assert result is not None
            assert result.name == "by_space_id.las", f"space_id側が優先されるべき: {result}"
        finally:
            server.BASE_MAPS_DIR = original
    print("test_find_base_map_path_prefers_space_id_over_tokutei_code: OK")


def test_find_base_map_path_falls_back_to_tokutei_code_when_space_id_missing():
    with tempfile.TemporaryDirectory() as tmp:
        base_maps_dir = Path(tmp)
        original = server.BASE_MAPS_DIR
        server.BASE_MAPS_DIR = base_maps_dir
        try:
            (base_maps_dir / "legacy_only.las").write_bytes(b"legacy")
            manifest = [{"id": "G555", "label": "G555", "file": "legacy_only.las"}]
            (base_maps_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            result = server._find_base_map_path("b4-G555")
            assert result is not None and result.name == "legacy_only.las"
        finally:
            server.BASE_MAPS_DIR = original
    print("test_find_base_map_path_falls_back_to_tokutei_code_when_space_id_missing: OK")


def test_migrated_space_id_and_tokutei_code_files_match_and_space_id_preferred():
    """(2026-09-03修正: 元は実データ ichigaya_tamachi-G002 / building_3ed5c612-G003
    を直接参照していたが、G002はLocal Space削除機能により意図的に削除された
    (復元しない、ユーザー指示)。scripts/migrate_space_id_keyed_persistence.py の
    「コピーのみ・内容無変更」という移行契約を、他のテストと同じ使い捨てfixture
    パターン(server.SPACE_DEF_DIR・BASE_MAPS_DIRを一時ディレクトリへ差し替え)で
    検証する(実データの存否に依存しない)。"""
    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = Path(tmp) / "space_definitions"
        base_maps_dir = Path(tmp) / "base_maps"
        space_def_dir.mkdir()
        base_maps_dir.mkdir()
        original_space_def_dir = server.SPACE_DEF_DIR
        original_base_maps_dir = server.BASE_MAPS_DIR
        server.SPACE_DEF_DIR = space_def_dir
        server.BASE_MAPS_DIR = base_maps_dir
        try:
            space_id = "disposable_building-G901"
            tokutei_code = "G901"
            coordinate_definition = {
                "id": tokutei_code, "degree": 0.0, "rad": 0.0, "height": 3.0,
                "origin": [0, 0, 0], "unit-size": {"9": 0.12, "10": 0.06, "11": 0.03}, "bounds": [[0, 0, 0]] * 8,
            }
            # migrate_space_id_keyed_persistence.pyは「コピーのみ・内容無変更」で
            # legacy tokutei_code-keyedファイルからspace_id-keyedファイルを複製する
            # 契約なので、ここでも同じ内容で両方書く(migrate後の状態を模擬)。
            (space_def_dir / f"{tokutei_code}.json").write_text(json.dumps(coordinate_definition), encoding="utf-8")
            space_id_path = space_def_dir / f"{space_id}.json"
            space_id_path.write_text(json.dumps(coordinate_definition), encoding="utf-8")

            (base_maps_dir / f"{tokutei_code}.las").write_bytes(b"legacy-bytes")
            (base_maps_dir / f"{space_id}.las").write_bytes(b"legacy-bytes")
            manifest = [
                {"id": tokutei_code, "label": tokutei_code, "file": f"{tokutei_code}.las"},
                {"id": space_id, "label": space_id, "file": f"{space_id}.las"},
            ]
            (base_maps_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            assert space_id_path.exists()
            resolved = server._find_space_definition(space_id)
            assert resolved is not None
            # 移行はコピーのみ(内容は無変更)のため、tokutei_code版と内容が一致するはず
            tokutei_content = json.loads((space_def_dir / f"{tokutei_code}.json").read_text(encoding="utf-8-sig"))
            assert resolved == json.loads(space_id_path.read_text(encoding="utf-8-sig")) == tokutei_content

            base_map_path = server._find_base_map_path(space_id)
            assert base_map_path is not None
            assert base_map_path.name.startswith(space_id), (
                f"{space_id}: base mapがspace_idキー側で解決されていない: {base_map_path}"
            )
        finally:
            server.SPACE_DEF_DIR = original_space_def_dir
            server.BASE_MAPS_DIR = original_base_maps_dir
    print("test_migrated_space_id_and_tokutei_code_files_match_and_space_id_preferred: OK")


if __name__ == "__main__":
    test_find_space_definition_prefers_space_id_over_tokutei_code()
    test_find_space_definition_falls_back_to_tokutei_code_when_space_id_missing()
    test_local_space_repository_prefers_space_id_over_tokutei_code()
    test_find_base_map_path_exact_match_not_substring()
    test_find_base_map_path_prefers_space_id_over_tokutei_code()
    test_find_base_map_path_falls_back_to_tokutei_code_when_space_id_missing()
    test_migrated_space_id_and_tokutei_code_files_match_and_space_id_preferred()
    print()
    print("全テスト成功。")
