"""
Base MapまたはCoordinateDefinitionが変更・再生成された場合に、Spatial ID
voxelのderived cache(positions/ids)・voxel color cacheが確実に破棄される
ことの確認テスト(2026-09-01調査・修正)。

【調査結果】
- SpatialVoxelCacheRepository.invalidate() / VoxelColorCacheRepository.invalidate()
  は既存(ロードマップStep 2〜4)から実装済みで、space_id単位で全zoom level・
  全modeを一括破棄できる(各リポジトリの単体テストで確認済み)。
- しかし、server.pyの実際の書き込み経路(/api/planes/detect でのBase Map
  再アップロード、/api/local-spaces でのCoordinateDefinition再生成)からは、
  これらのinvalidate()が一度も呼ばれていなかった(grep調査で確認)。
  既存space_idに対してこれらのエンドポイントが再度呼ばれると、古い点群・
  古い座標定義から計算されたキャッシュが残ったまま返り続ける欠落があった。
- 修正: server.py の detect_planes()(Base Map書き込み直後)・
  create_local_space()(CoordinateDefinition書き込み直後)に、
  spatial_voxel_cache_repo.invalidate(space_id)・
  voxel_color_cache_repo.invalidate(space_id) の呼び出しを追加した
  (zoom_level省略 = そのspace_idの全zoom levelを破棄)。

本ファイルは2種類のテストを行う:
1. 破棄能力そのもの(一時ディレクトリ、実データ非依存): 複数zoom level・
   複数modeにまたがるキャッシュが、space_id単位のinvalidate()呼び出し
   2回(positions側・color側)だけで全て消えること、かつ別space_idの
   キャッシュは巻き込まれないこと。
2. server.pyへの実際の配線確認(実データ非依存、pygicp等の重いパイプラインは
   一切実行しない): detect_planes()・create_local_space()の関数ソースに、
   上記2つのinvalidate()呼び出しが実際に含まれていることを
   inspect.getsource()で確認する(呼び出し忘れの再発を防ぐ回帰ガード)。
   Base Mapアップロード・点群処理・registry書き込みそのものは実行しない
   (実データ・実ファイルシステムへの副作用を避けるため)。

実行方法(リポジトリルートから):
    python backend/tests/test_cache_invalidation.py
"""
from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.spatial_voxel import SpatialVoxel  # noqa: E402
from repositories.spatial_voxel_cache_repository import SpatialVoxelCacheRepository  # noqa: E402
from repositories.voxel_color_cache_repository import VoxelColorCacheRepository  # noqa: E402


def _make_voxel(space_id, sid, zoom_level, voxel_size, center):
    return SpatialVoxel(
        space_id=space_id, local_spatial_id=sid, zoom_level=zoom_level, voxel_size=voxel_size,
        point_count=1, voxel_center=list(center),
    )


def test_invalidate_clears_all_zoom_levels_across_both_caches_for_one_space():
    """server.pyの実際の呼び出しパターン(2箇所とも
    spatial_voxel_cache_repo.invalidate(space_id) と
    voxel_color_cache_repo.invalidate(space_id) をzoom_level省略で呼ぶ)を
    そのまま再現し、複数zoom level・複数modeにまたがるキャッシュが
    一括で消えることを確認する。"""
    with tempfile.TemporaryDirectory() as tmp:
        pos_repo = SpatialVoxelCacheRepository(Path(tmp) / "spatial_voxel_cache")
        color_repo = VoxelColorCacheRepository(Path(tmp) / "voxel_color_cache")

        space_id = "b1-G002"
        # finest + 2段の上位level分のposition/idsキャッシュ
        pos_repo.save(space_id, [_make_voxel(space_id, "11/0/0/0", 11, 0.03, [0.015, 0.015, 0.015])])
        pos_repo.save(space_id, [_make_voxel(space_id, "9/0/0/0", 9, 0.12, [0.06, 0.06, 0.06])])
        pos_repo.save(space_id, [_make_voxel(space_id, "8/0/0/0", 8, 0.24, [0.12, 0.12, 0.12])])
        # 同じ3 zoom level分、DEFAULT/STRUCTURAL_LABEL両modeのcolorキャッシュ
        for z in (11, 9, 8):
            for mode in ("DEFAULT", "STRUCTURAL_LABEL"):
                color_repo.save(space_id, z, mode, np.array([0], dtype=np.uint8), {"0": [1, 1, 1]})

        assert pos_repo.load_meta(space_id, 11) is not None
        assert pos_repo.load_meta(space_id, 9) is not None
        assert pos_repo.load_meta(space_id, 8) is not None
        assert color_repo.load_meta(space_id, 11, "DEFAULT") is not None
        assert color_repo.load_meta(space_id, 9, "STRUCTURAL_LABEL") is not None
        assert color_repo.load_meta(space_id, 8, "STRUCTURAL_LABEL") is not None

        # server.pyの新設invalidation呼び出しと全く同じ形(zoom_level省略)
        pos_repo.invalidate(space_id)
        color_repo.invalidate(space_id)

        for z in (11, 9, 8):
            assert pos_repo.load_meta(space_id, z) is None, f"zoom={z} のposition cacheが残っている"
            for mode in ("DEFAULT", "STRUCTURAL_LABEL"):
                assert color_repo.load_meta(space_id, z, mode) is None, f"zoom={z} mode={mode} のcolor cacheが残っている"
    print("test_invalidate_clears_all_zoom_levels_across_both_caches_for_one_space: OK")


def test_invalidate_does_not_affect_other_space_ids():
    with tempfile.TemporaryDirectory() as tmp:
        pos_repo = SpatialVoxelCacheRepository(Path(tmp) / "spatial_voxel_cache")
        color_repo = VoxelColorCacheRepository(Path(tmp) / "voxel_color_cache")

        pos_repo.save("b1-G002", [_make_voxel("b1-G002", "9/0/0/0", 9, 0.12, [0.06, 0.06, 0.06])])
        pos_repo.save("b1-G003", [_make_voxel("b1-G003", "9/0/0/0", 9, 0.12, [0.06, 0.06, 0.06])])
        color_repo.save("b1-G002", 9, "DEFAULT", np.array([0], dtype=np.uint8), {"0": [1, 1, 1]})
        color_repo.save("b1-G003", 9, "DEFAULT", np.array([0], dtype=np.uint8), {"0": [1, 1, 1]})

        pos_repo.invalidate("b1-G002")
        color_repo.invalidate("b1-G002")

        assert pos_repo.load_meta("b1-G002", 9) is None
        assert color_repo.load_meta("b1-G002", 9, "DEFAULT") is None
        assert pos_repo.load_meta("b1-G003", 9) is not None, "別space_idのcacheが巻き込まれて消えた"
        assert color_repo.load_meta("b1-G003", 9, "DEFAULT") is not None, "別space_idのcacheが巻き込まれて消えた"
    print("test_invalidate_does_not_affect_other_space_ids: OK")


def test_server_detect_planes_invalidates_both_caches_on_base_map_reupload():
    """detect_planes()(Base Map再アップロード経路)のソースに、実際に
    spatial_voxel_cache_repo.invalidate・voxel_color_cache_repo.invalidateへの
    呼び出しが含まれることを確認する(呼び出し忘れの回帰ガード)。
    server.py自体のimport以外、重い処理(pygicp・点群読み込み等)は一切実行しない。"""
    import server  # noqa: E402  (このテストの中でのみ、必要な時に読み込む)
    src = inspect.getsource(server.detect_planes)
    assert "spatial_voxel_cache_repo.invalidate(" in src, "detect_planes()がpositionキャッシュを破棄していない"
    assert "voxel_color_cache_repo.invalidate(" in src, "detect_planes()がcolorキャッシュを破棄していない"
    print("test_server_detect_planes_invalidates_both_caches_on_base_map_reupload: OK")


def test_server_create_local_space_invalidates_both_caches_on_coordinate_definition_regen():
    """create_local_space()(CoordinateDefinition再生成経路)についても同様。"""
    import server  # noqa: E402
    src = inspect.getsource(server.create_local_space)
    assert "spatial_voxel_cache_repo.invalidate(" in src, "create_local_space()がpositionキャッシュを破棄していない"
    assert "voxel_color_cache_repo.invalidate(" in src, "create_local_space()がcolorキャッシュを破棄していない"
    # 上書き済みのspace_def_pathより後(=書き込み直後)に破棄していることも確認する
    assert src.index("space_def_path.write_text") < src.index("spatial_voxel_cache_repo.invalidate(")
    print("test_server_create_local_space_invalidates_both_caches_on_coordinate_definition_regen: OK")


if __name__ == "__main__":
    test_invalidate_clears_all_zoom_levels_across_both_caches_for_one_space()
    test_invalidate_does_not_affect_other_space_ids()
    test_server_detect_planes_invalidates_both_caches_on_base_map_reupload()
    test_server_create_local_space_invalidates_both_caches_on_coordinate_definition_regen()
    print()
    print("全テスト成功。")
