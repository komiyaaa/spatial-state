"""
scripts/migrate_gui_v1_to_v2.py の動作確認テスト。

実行方法(リポジトリルートから):
    python backend/tests/test_migrate_gui_v1_to_v2.py

【重要】このテストは、実際のリポジトリの backend/buildings.json 等には
一切触れない。migrate_gui_v1_to_v2 モジュールの各パス定数(BACKEND_DIR等)を
一時ディレクトリに差し替えた上でテストする。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import migrate_gui_v1_to_v2 as migrate  # noqa: E402


_SPACE_DEF_CONTENT = {
    "id": "G002", "degree": 1.0, "rad": 0.01, "height": 3.0, "origin": [0, 0, 0],
    "unit-size": {"0": 51.2}, "bounds": [[0, 0, 0]],
}


def _make_fake_repo(root: Path, space_def_filename: str = "G002v3.json") -> Path:
    """現行スキーマ相当のダミーデータを持つ、一時的な backend/ ディレクトリを作る。

    実際のリポジトリ同様、座標定義ファイルは既定で legacy命名(G002v3.json)にする
    (legacy filename resolutionのテストのため)。
    """
    backend_dir = root / "backend"
    (backend_dir / "space_definitions").mkdir(parents=True)

    (backend_dir / "buildings.json").write_text(
        json.dumps(
            [{"building_id": "b1", "real_estate_number": "未設定", "name": "テスト校舎", "address": ""}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (backend_dir / "local_spaces.json").write_text(
        json.dumps(
            [
                {
                    "space_id": "b1-G002",
                    "building_id": "b1",
                    "tokutei_code": "G002",
                    "floor": 1,
                    "zoom_level": 9,
                    "registered_at": "2026-01-01T00:00:00",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (backend_dir / "space_definitions" / space_def_filename).write_text(
        json.dumps(_SPACE_DEF_CONTENT, ensure_ascii=False), encoding="utf-8"
    )
    return backend_dir


def _patch_paths(backend_dir: Path) -> None:
    migrate.BACKEND_DIR = backend_dir
    migrate.OLD_BUILDINGS_PATH = backend_dir / "buildings.json"
    migrate.OLD_LOCAL_SPACES_PATH = backend_dir / "local_spaces.json"
    migrate.OLD_SPACE_DEFINITIONS_DIR = backend_dir / "space_definitions"
    migrate.NEW_REGISTRY_DIR = backend_dir / "data" / "registry"
    migrate.DEFAULT_BACKUP_ROOT = backend_dir / "data" / "_migration_backup"


def _snapshot(backend_dir: Path, space_def_filename: str = "G002v3.json") -> dict:
    """旧データの内容をスナップショットする(改変されていないことの確認用)。"""
    return {
        "buildings": (backend_dir / "buildings.json").read_text(encoding="utf-8"),
        "local_spaces": (backend_dir / "local_spaces.json").read_text(encoding="utf-8"),
        "space_def": (backend_dir / "space_definitions" / space_def_filename).read_text(encoding="utf-8"),
    }


def test_dry_run_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        backend_dir = _make_fake_repo(Path(tmp))
        _patch_paths(backend_dir)
        before = _snapshot(backend_dir)

        exit_code = migrate.main(["--dry-run"])

        assert exit_code == 0
        assert not migrate.NEW_REGISTRY_DIR.exists(), "dry-runなのに新スキーマファイルが書き込まれた"
        assert not (backend_dir / "space_definitions" / "G002.json").exists(), (
            "dry-runなのにcanonicalファイルがコピーされた"
        )
        assert _snapshot(backend_dir) == before, "dry-runなのに旧データが変更された"
    print("test_dry_run_does_not_write: OK")


def test_legacy_filename_unique_is_resolved_and_copied():
    """[E] G002v3.json のような legacy 命名が1件だけの場合、canonical名
    (G002.json)としてコピーされ、かつ元ファイルは削除されないこと。"""
    with tempfile.TemporaryDirectory() as tmp:
        backend_dir = _make_fake_repo(Path(tmp), space_def_filename="G002v3.json")
        _patch_paths(backend_dir)

        plan = migrate.build_migration_plan()
        resolution = plan.resolutions[0]
        assert resolution.status == "legacy_unique"
        assert resolution.resolved_path.name == "G002v3.json"
        assert not any("見つかりません" in w or "複数見つかりました" in w for w in plan.warnings)

        exit_code = migrate.main(["--apply"])
        assert exit_code == 0

        legacy_path = backend_dir / "space_definitions" / "G002v3.json"
        canonical_path = backend_dir / "space_definitions" / "G002.json"
        assert legacy_path.exists(), "legacy(元)ファイルが削除されてしまった"
        assert canonical_path.exists(), "canonical名のコピーが作成されていない"
        assert json.loads(canonical_path.read_text(encoding="utf-8")) == _SPACE_DEF_CONTENT
        assert legacy_path.read_text(encoding="utf-8") == json.dumps(_SPACE_DEF_CONTENT, ensure_ascii=False), (
            "legacyファイルの中身が変更されてしまった"
        )
    print("test_legacy_filename_unique_is_resolved_and_copied: OK")


def test_legacy_filename_ambiguous_is_not_auto_selected():
    """[F] 同一tokutei_codeに対しlegacy候補が複数ある場合、自動選択せず
    warningとして報告し、canonicalファイルは作られないこと。"""
    with tempfile.TemporaryDirectory() as tmp:
        backend_dir = _make_fake_repo(Path(tmp), space_def_filename="G002v3.json")
        # 曖昧な状況を作るため、もう1つlegacy候補を追加する
        (backend_dir / "space_definitions" / "G002v2.json").write_text(
            json.dumps(_SPACE_DEF_CONTENT, ensure_ascii=False), encoding="utf-8"
        )
        _patch_paths(backend_dir)

        plan = migrate.build_migration_plan()
        resolution = plan.resolutions[0]
        assert resolution.status == "legacy_ambiguous"
        assert set(p.name for p in resolution.candidates) == {"G002v2.json", "G002v3.json"}
        assert any("複数見つかりました" in w for w in plan.warnings), "曖昧な場合にwarningが出ていない"
        assert plan.space_definition_copies == [], "曖昧なのに自動でコピー対象にしてしまっている"

        exit_code = migrate.main(["--apply"])
        assert exit_code == 0
        canonical_path = backend_dir / "space_definitions" / "G002.json"
        assert not canonical_path.exists(), "曖昧なのにcanonicalファイルが作られてしまった"
        # 両方のlegacyファイルとも無傷であること
        assert (backend_dir / "space_definitions" / "G002v2.json").exists()
        assert (backend_dir / "space_definitions" / "G002v3.json").exists()
    print("test_legacy_filename_ambiguous_is_not_auto_selected: OK")


def test_apply_creates_correct_new_schema():
    """[C] staging生成が全て成功した場合、新registry一式が正式に反映されること。"""
    with tempfile.TemporaryDirectory() as tmp:
        backend_dir = _make_fake_repo(Path(tmp))
        _patch_paths(backend_dir)

        exit_code = migrate.main(["--apply"])
        assert exit_code == 0

        # staging領域が残っていないこと(反映後に消えていること)
        leftover_staging = list(migrate.NEW_REGISTRY_DIR.parent.glob("registry.staging_*"))
        assert leftover_staging == [], f"staging領域が残ってしまっている: {leftover_staging}"
        leftover_old = list(migrate.NEW_REGISTRY_DIR.parent.glob("registry.old_*"))
        assert leftover_old == [], f"旧registryの退避領域が残ってしまっている: {leftover_old}"

        buildings = json.loads((migrate.NEW_REGISTRY_DIR / "buildings.json").read_text(encoding="utf-8"))
        assert buildings == [{"building_id": "b1", "real_estate_number": "未設定", "name": "テスト校舎", "address": ""}]

        local_spaces = json.loads((migrate.NEW_REGISTRY_DIR / "local_spaces.json").read_text(encoding="utf-8"))
        assert len(local_spaces) == 1
        assert local_spaces[0]["space_id"] == "b1-G002"
        assert local_spaces[0]["real_estate_id"] is None, "real_estate_id(nullable)が追加されていない"
        assert local_spaces[0]["tokutei_code"] == "G002", "tokutei_codeが変更されてしまっている"

        placements = json.loads((migrate.NEW_REGISTRY_DIR / "placements.json").read_text(encoding="utf-8"))
        assert placements["b1-G002"]["status"] == "UNRESOLVED", (
            "既存の座標定義をGlobal Resolvedと勝手に認定してはいけない"
        )
        assert placements["b1-G002"]["global_origin"] is None
        assert placements["b1-G002"]["global_rotation_rad"] is None

        assert json.loads((migrate.NEW_REGISTRY_DIR / "nodal_endpoints.json").read_text(encoding="utf-8")) == []
        assert json.loads((migrate.NEW_REGISTRY_DIR / "nodal_connections.json").read_text(encoding="utf-8")) == []

        # 旧データ(legacy座標定義ファイル)は変更されていないこと
        assert (backend_dir / "space_definitions" / "G002v3.json").exists()

        # backupが作られていること
        backups = list(migrate.DEFAULT_BACKUP_ROOT.iterdir())
        assert len(backups) == 1
        assert (backups[0] / "buildings.json").exists()
        assert (backups[0] / "local_spaces.json").exists()
        assert (backups[0] / "space_definitions" / "G002v3.json").exists()
    print("test_apply_creates_correct_new_schema: OK")


def test_idempotent_double_apply():
    """[D] 同じ入力に対して2回applyしても、2回目は内容が変化しないこと。"""
    with tempfile.TemporaryDirectory() as tmp:
        backend_dir = _make_fake_repo(Path(tmp))
        _patch_paths(backend_dir)

        plan = migrate.build_migration_plan()
        backup_dir_1 = migrate.DEFAULT_BACKUP_ROOT / "run1"
        result_1 = migrate.apply_migration(plan, backup_dir_1)
        assert len(result_1["written"]) == 5
        assert len(result_1["skipped"]) == 0
        assert len(result_1["copied"]) == 1
        assert len(result_1["copy_skipped"]) == 0

        content_after_first = {
            p.name: json.loads(p.read_text(encoding="utf-8")) for p in migrate._target_files(plan)
        }

        # 2回目(同一planを再適用) -> 全ファイルがskip、canonicalコピーもskipされ、
        # (内容が同一のため)ディレクトリのswap自体も発生しないこと
        backup_dir_2 = migrate.DEFAULT_BACKUP_ROOT / "run2"
        result_2 = migrate.apply_migration(plan, backup_dir_2)
        assert len(result_2["written"]) == 0, "内容が同一なのに再書き込みされた(冪等性違反)"
        assert len(result_2["skipped"]) == 5
        assert len(result_2["copied"]) == 0, "既にcanonicalファイルがあるのに再コピーされた(冪等性違反)"
        assert len(result_2["copy_skipped"]) == 1

        content_after_second = {
            p.name: json.loads(p.read_text(encoding="utf-8")) for p in migrate._target_files(plan)
        }
        assert content_after_first == content_after_second, "2回目のapplyでデータが変化した"

        leftover_staging = list(migrate.NEW_REGISTRY_DIR.parent.glob("registry.staging_*"))
        assert leftover_staging == [], "内容が同一のケースでstaging領域の後片付けが漏れている"
    print("test_idempotent_double_apply: OK")


def test_staging_failure_leaves_no_registry_when_none_existed():
    """[A] 既存registryが無い状態で、staging生成の途中に例外が起きた場合、
    backend/data/registry/ が作られないこと(旧データも無傷)。"""
    with tempfile.TemporaryDirectory() as tmp:
        backend_dir = _make_fake_repo(Path(tmp))
        _patch_paths(backend_dir)
        before = _snapshot(backend_dir)
        assert not migrate.NEW_REGISTRY_DIR.exists(), "前提条件: 既存registryが無い状態のはず"

        original_write = migrate._write_json_atomic
        call_count = {"n": 0}

        def _flaky_write(path, data):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("staging生成中の疑似障害(3件目)")
            return original_write(path, data)

        migrate._write_json_atomic = _flaky_write
        try:
            raised = False
            try:
                migrate.main(["--apply"])
            except RuntimeError:
                raised = True
            assert raised, "staging生成失敗が正しく伝播しなかった"
        finally:
            migrate._write_json_atomic = original_write

        assert not migrate.NEW_REGISTRY_DIR.exists(), (
            "staging生成が一部失敗したのに、backend/data/registry/ が作られてしまった"
        )
        leftover_staging = list((migrate.NEW_REGISTRY_DIR.parent).glob("registry.staging_*")) \
            if migrate.NEW_REGISTRY_DIR.parent.exists() else []
        assert leftover_staging == [], "失敗したstaging領域の後片付けが漏れている"
        assert _snapshot(backend_dir) == before, "staging生成失敗時に旧データが変更された"
    print("test_staging_failure_leaves_no_registry_when_none_existed: OK")


def test_staging_failure_leaves_existing_registry_intact():
    """[B] 既存registryがある状態で、staging生成の途中に例外が起きた場合、
    既存registryの中身が一切変更されないこと。"""
    with tempfile.TemporaryDirectory() as tmp:
        backend_dir = _make_fake_repo(Path(tmp))
        _patch_paths(backend_dir)
        before = _snapshot(backend_dir)

        # 1回目は正常にapplyし、「既存registryがある」状態を作る
        plan = migrate.build_migration_plan()
        migrate.apply_migration(plan, migrate.DEFAULT_BACKUP_ROOT / "setup")
        existing_registry_snapshot = {
            p.name: p.read_text(encoding="utf-8") for p in migrate.NEW_REGISTRY_DIR.iterdir()
        }
        assert existing_registry_snapshot, "前提条件: 既存registryが作られているはず"

        # 2回目のapplyで、staging生成の途中に例外を起こす
        # (中身は変わらないが、独立したplanオブジェクトを渡すため
        #  再度build_migration_planしても差分検知には影響しない)
        original_write = migrate._write_json_atomic
        call_count = {"n": 0}

        def _flaky_write(path, data):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("staging生成中の疑似障害(2件目)")
            return original_write(path, data)

        migrate._write_json_atomic = _flaky_write
        try:
            raised = False
            try:
                migrate.apply_migration(plan, migrate.DEFAULT_BACKUP_ROOT / "attempt2")
            except RuntimeError:
                raised = True
            assert raised, "staging生成失敗が正しく伝播しなかった"
        finally:
            migrate._write_json_atomic = original_write

        after_registry_snapshot = {
            p.name: p.read_text(encoding="utf-8") for p in migrate.NEW_REGISTRY_DIR.iterdir()
        }
        assert after_registry_snapshot == existing_registry_snapshot, (
            "staging生成失敗時に、既存registryの内容が変更されてしまった"
        )
        leftover_staging = list(migrate.NEW_REGISTRY_DIR.parent.glob("registry.staging_*"))
        assert leftover_staging == [], "失敗したstaging領域の後片付けが漏れている"
        assert _snapshot(backend_dir) == before, "旧データも変更されていないはず"
    print("test_staging_failure_leaves_existing_registry_intact: OK")


if __name__ == "__main__":
    test_dry_run_does_not_write()
    test_legacy_filename_unique_is_resolved_and_copied()
    test_legacy_filename_ambiguous_is_not_auto_selected()
    test_apply_creates_correct_new_schema()
    test_idempotent_double_apply()
    test_staging_failure_leaves_no_registry_when_none_existed()
    test_staging_failure_leaves_existing_registry_intact()
    print()
    print("全テスト成功。")
