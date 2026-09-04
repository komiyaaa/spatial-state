"""
backend/server.py

Web UI(追加モード)からラフレジ結果を受け取り、次工程(VGICP精密位置合わせ・
JSON化)へ橋渡しするための、最小限のローカルサーバー。

パイプライン全体(system_architecture_memo.md §1 と対応):

    Web UI(追加モード、ラフレジ)
      → fetch POST /api/registration-results
      → 【本ファイル】受信・保存(data/rough_registered/)
      → run_vgicp()               ← VGICP_jissho_fast.py のアルゴリズムを移植済み
      → 保存(data/precise_registered/)
      → convert_to_scan_json()    ← 差し替えポイント(空間ID格子でのボクセル化)
      → 保存(data/scan_json/)
      → (この先、更新エンジンへ渡す。今回のスコープ外)

【run_vgicpについて】
VGICP_jissho_fast.py(既存実装)のアルゴリズムを、フォルダ一括処理から
「1回のリクエストにつき1組(target 1件・source 1件)」の処理に絞って移植した。
数式・アルゴリズム自体(複数ボクセルサイズを試し、fitness_scoreが最良のものを
採用する、というロジック)は変更していない。

必要な依存関係(requirements.txtに記載):
    pip install open3d laspy pygicp
※ pygicp(fast_gicpのPythonバインディング)は環境によってはビルドが必要な
  場合がある(https://github.com/SMRT-AIST/fast_gicp を参照)。

起動方法:
    pip install -r requirements.txt
    python backend/server.py
    → http://localhost:8000 で、フロント(local_space_prototype.html)ごと配信される
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, request, send_file, send_from_directory, jsonify

# 【重要・読み込み順を変えないこと】pygicp(fast_gicp)とscikit-learn
# (space_definition_generator.pyがPCAに使用)は、どちらもOpenMP系のネイティブ
# ランタイムを内包しており、Windows環境では「scikit-learnを先に読み込んだ
# 状態でpygicpのVGICPを実行すると、プロセスがセグメンテーション違反で
# 落ちる」という既知の競合がある(実際に発生を確認済み: sklearnを先に
# importした状態でFastVGICP().align()を呼ぶと確実にクラッシュし、pygicpを
# 先にimportしておけば、後からsklearnを読み込んでも問題ない)。
# そのため、space_definition_generator(≒scikit-learn)をimportするより前に、
# ここでpygicpを読み込んでおく(未インストール環境ではNoneのまま無視され、
# run_vgicp()側の既存のImportErrorフォールバックに委ねる)。
try:
    import pygicp  # noqa: F401  (読み込み順を固定するためのimport。使用箇所はrun_vgicp内)
except ImportError:
    pass

from domain import (
    ConnectionEndpointRef,
    NodalConnection,
    NodalEndpoint,
    NodalEndpointType,
)
from domain.global_resolution import ComponentGlobalResolution, GlobalResolutionStatus
from domain.nodal_connection import Correspondence
from domain.structural_label import StructuralLabel
from domain.visualization import VisualizationMode
from local_space_deletion_service import DeletionArchiveError, DeletionContext, build_deletion_plan, execute_deletion
from plane_segmentation import PlaneSegmentationConfig, segment_planes
from plane_to_voxel_labels import build_voxel_labels
from point_cloud_voxelization import voxelize_base_map_points
from point_to_spatial_id import world_points_to_spatial_ids
from registry import Registry
from repositories.building_repository import BuildingRepository
from repositories.label_fitness_history_repository import LabelFitnessHistoryRepository
from repositories.local_space_repository import LocalSpaceRepository
from repositories.nodal_connection_repository import NodalConnectionRepository
from repositories.nodal_endpoint_repository import NodalEndpointRepository
from repositories.plane_repository import PlaneRepository
from repositories.spatial_resolution_result_repository import SpatialResolutionResultRepository
from repositories.spatial_voxel_cache_repository import SpatialVoxelCacheRepository
from repositories.spatial_voxel_label_repository import SpatialVoxelLabelRepository
from repositories.voxel_color_cache_repository import VoxelColorCacheRepository
from services.global_coordinate_service import GlobalCoordinateResolutionError
from services.global_export_service import GlobalExportError, export_precise_registered_to_global
from services.spatial_resolution_service import estimate_connection_solution, resolve_building
from space_definition_generator import finest_zoom_level, generate_space_definition
from spatial_id.geographic_projection import DEFAULT_TARGET_EPSG
from spatial_id.global_spatial_id import StandardSpatialIdResolver
from spatial_id.local_spatial_id import LocalSpatialIdResolver
from spatial_state_updater import apply_scan_to_spatial_state
from spatial_state_view import build_spatial_state_view
from spatial_voxel_aggregation import aggregate_finest_positions_to_zoom_level
from spatial_state import Params
from state_store import StateStore
from visualization_colors import build_legend
from voxel_color_strategy import build_color_codes_for_mode

BASE_DIR = Path(__file__).resolve().parent.parent  # ui_proto/ (フロントの置き場所)
BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / "data"
ROUGH_DIR = DATA_DIR / "rough_registered"
PRECISE_DIR = DATA_DIR / "precise_registered"
SCAN_JSON_DIR = DATA_DIR / "scan_json"
BASE_MAPS_DIR = BASE_DIR / "base_maps"
SPACE_DEF_DIR = BACKEND_DIR / "space_definitions"
VGICP_LOG_DIR = DATA_DIR / "vgicp_logs"
TRACKER_STATE_DIR = DATA_DIR / "tracker_state"
PLANES_DIR = DATA_DIR / "planes"
VOXEL_LABELS_DIR = DATA_DIR / "voxel_labels"
STRUCTURAL_LABEL_HISTORY_DIR = DATA_DIR / "structural_label_fitness_history"
SPATIAL_VOXEL_CACHE_DIR = DATA_DIR / "spatial_voxel_cache"
VOXEL_COLOR_CACHE_DIR = DATA_DIR / "voxel_color_cache"
REGISTRY_DIR = DATA_DIR / "registry"
SPATIAL_RESOLUTION_RESULTS_DIR = DATA_DIR / "spatial_resolution_results"
# 検証用の一時出力フォルダ。本番の per-space_id フォルダ(ROUGH_DIR等)や
# 更新エンジン(state_store)には一切触れず、ラフレジ結果・VGICP結果・
# fitness_scoreだけを毎回新しいサブフォルダに書き出す(パイプライン単体の
# 動作確認・VGICPパラメータの検証用)。
VERIFY_OUTPUT_DIR = DATA_DIR / "verify_output"
# VGICP精密位置合わせ結果を、Spatial State更新手法の検証データとして人間が
# 直接使えるよう明示的に保存する場所(2026-09-02追加)。ROUGH_DIR/PRECISE_DIR/
# SCAN_JSON_DIRは既存のまま無変更。ここは既存パイプラインの出力を追加で
# コピーして{space_id}/{run_id}_{source_stem}/単位に整理するだけの、
# 読みやすさのためのアーカイブ層(source of truthの複製ではあるが、
# 上書きしない・削除しないという運用のため、本番データの整合性には影響しない)。
REGISTRATION_RESULTS_DIR = DATA_DIR / "registration_results"

for d in (ROUGH_DIR, PRECISE_DIR, SCAN_JSON_DIR, SPACE_DEF_DIR, VGICP_LOG_DIR, VERIFY_OUTPUT_DIR, SPATIAL_RESOLUTION_RESULTS_DIR, REGISTRATION_RESULTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")

# 更新エンジン(spatial_state)。paramsは近傍数6(面隣接)を前提にしたデフォルト値
# (spatial_state/params.py の expected_n_neighbors_for_validation=6 と対応)。
params = Params()
state_store = StateStore(TRACKER_STATE_DIR)
# 建物・ローカル空間の永続化(GUIの「建物を追加」→「ローカル空間を追加」フロー用)。
# base_maps/manifest.json・space_definitions/*.json と同様、実データとして
# git管理する対象なので、使い捨ての中間データ(DATA_DIR)ではなくbackend/直下に置く。
# registry(Registry)は後方互換のため残置しているが、呼び出し元は無い
# (Buildingはbuilding_repo、Local Spaceはlocal_space_repoに一本化済み。
# ER図反映、2026-09-02)。
registry = Registry(BACKEND_DIR)
# BuildingRepositoryは、legacy Registryと同一ファイル(backend/buildings.json)を
# そのまま読み書きする設計(repositories/building_repository.py参照)。
# ファイル・スキーマとも完全一致のため、移行(コピー等)は不要。
building_repo = BuildingRepository(BACKEND_DIR / "buildings.json")

# 構造平面(Plane)ラベリング機能の永続化層。いずれもBase Map/Plane/
# CoordinateDefinitionから再生成可能なderived dataという位置づけ
# (spatial_state・Phase 3 Nodal Informationとは完全に独立)。
plane_repo = PlaneRepository(PLANES_DIR)
voxel_label_repo = SpatialVoxelLabelRepository(VOXEL_LABELS_DIR)
label_fitness_history_repo = LabelFitnessHistoryRepository(STRUCTURAL_LABEL_HISTORY_DIR)
# Step 1のSpatialVoxel(Base Map点群のfinest voxel集約)を、Viewer向けの
# 軽量derived cacheとして保存する層(source of truthの複製ではない、
# repositories/spatial_voxel_cache_repository.py参照)。
spatial_voxel_cache_repo = SpatialVoxelCacheRepository(SPATIAL_VOXEL_CACHE_DIR)
# Step 4のcolor_code(Visualization / Coloring Strategyの計算結果)を、
# Viewer向けの軽量derived cacheとして保存する層(source of truthの複製
# ではない、repositories/voxel_color_cache_repository.py参照)。
voxel_color_cache_repo = VoxelColorCacheRepository(VOXEL_COLOR_CACHE_DIR)

# Nodal Information(結節点・接続)の永続化と、Spatial Resolution実行結果の
# derived cache(ロードマップPhase 3.6)。registry.py(建物・旧local_space管理)
# とは独立した新スキーマ(backend/domain・backend/repositories参照)。
# Spatial State・point-cloud registrationの既存実行系には一切触れない。
nodal_endpoint_repo = NodalEndpointRepository(REGISTRY_DIR / "nodal_endpoints.json")
nodal_connection_repo = NodalConnectionRepository(REGISTRY_DIR / "nodal_connections.json")
local_space_repo = LocalSpaceRepository(REGISTRY_DIR, SPACE_DEF_DIR)
spatial_resolution_result_repo = SpatialResolutionResultRepository(SPATIAL_RESOLUTION_RESULTS_DIR)

# VGICP_jissho_fast.py のデフォルト値をそのまま踏襲
DEFAULT_VOX_SIZES = [0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]
DEFAULT_DS_RESOLUTION = 0.05
DEFAULT_EARLY_STOP_THRESHOLD = 0.01

# pygicp(fast_gicp)はスレッド間で競合しうるため、直列化する
# (VGICP_jissho_fast.py と同じ対策)
_vgicp_lock = threading.Lock()


def _find_base_map_path(space_id: str) -> Path | None:
    """
    space_id(例: "ichigaya_tamachi-G002")から、対応するbase_maps/manifest.json
    のエントリを探し、そのファイルパスを返す。

    正式な永続化キーはspace_id(manifest.jsonの"id"がspace_idと完全一致する
    エントリ)。tokutei_code単独キー("id"がtokutei_codeと完全一致するエントリ)は
    既存データ互換のためのread-onlyなlegacy fallback(新規にはこちらへ書き込まない)。
    両方存在する場合は必ずspace_id側を優先する(ER図反映、2026-09-02)。

    【2026-09-02修正】旧実装は"id"がspace_idの部分文字列かどうかという緩い
    マッチング(`entry["id"] in space_id`)だったため、たとえば id="G00" が
    space_id="foo-G002"に誤マッチしうる不具合があった。space_id・tokutei_code
    いずれも完全一致に統一して解消する。
    """
    manifest_path = BASE_MAPS_DIR / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for entry in manifest:
        if entry.get("id") == space_id:
            path = BASE_MAPS_DIR / entry["file"]
            return path if path.exists() else None

    tokutei_code = space_id.rsplit("-", 1)[-1]
    for entry in manifest:
        if entry.get("id") == tokutei_code:
            path = BASE_MAPS_DIR / entry["file"]
            return path if path.exists() else None

    return None


# ============================================================
# VGICPによる精密位置合わせ(VGICP_jissho_fast.py のアルゴリズムを移植)
# ============================================================

def _import_pcd(path: Path):
    """拡張子に応じて点群を読み込む(VGICP_jissho_fast.py の import_pcd と同一)。"""
    import open3d as o3d
    import numpy as np

    if path.suffix.lower() == ".las":
        import laspy as lp
        point_cloud = lp.read(str(path))
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(
            np.vstack((point_cloud.x, point_cloud.y, point_cloud.z)).transpose()
        )
        pcd.colors = o3d.utility.Vector3dVector(
            np.vstack((point_cloud.red, point_cloud.green, point_cloud.blue)).transpose() / 65535
        )
    else:
        pcd = o3d.io.read_point_cloud(str(path))
    return pcd


def _transform(points, matrix):
    import numpy as np
    return np.dot(points, matrix[:3, :3].T) + matrix[:3, 3]


def run_vgicp(rough_ply_path: Path, output_path: Path, space_id: str = "unknown") -> tuple[Path, float | None, dict, dict | None]:
    """
    VGICP_jissho_fast.py のアルゴリズムそのもの(複数ボクセルサイズを試し、
    fitness_scoreが最良のものを採用、アーリーストッピング付き)を、
    「1組のtarget・sourceを精密位置合わせする」形に絞って実行する。

    target(ベースマップ)は、space_idからbase_maps/manifest.jsonを引いて特定する。
    見つからない場合は、精密位置合わせをスキップし、ラフレジ結果をそのまま返す
    (パイプライン全体の配線を止めないためのフォールバック)。

    返り値は (output_path, fitness_score, fitness_detail, transform_info) の
    タプル。fitness_scoreは、後段のconvert_to_scan_json()・spatial_stateの
    w_fit計算に使われる(spatial_id_design_memo_v2.md の
    SCAN_SESSION.patch_fitness_score に対応)。フォールバック時(VGICPスキップ時)
    はfitness_score=None、fitness_detail={}、transform_info=Noneとして扱う。

    fitness_detailは、試した各ボクセルサイズのfitness_scoreをまとめた辞書
    ({vsize: score, ...})。vgicp_logs/{space_id}_{stem}.json にも同じ内容を
    書き出す(既存のtxtログと同じ場所・命名規則、更新手法の較正で使うための
    記録、docs/spatial_id_design_memo_v2.md §4 参照)。

    transform_infoは、採用された変換(best_vsizeで得られたmatrix)の
    voxel_size・rotation(3x3)・translation(3)・fitness_scoreをまとめた辞書
    (backend/data/registration_results/のregistration_result.json向け、
    2026-09-02追加)。matrix自体はVGICP_jissho_fast.py由来のalign()の戻り値を
    そのまま分解して保持するだけで、位置合わせの計算式は一切変更していない。
    """
    target_path = _find_base_map_path(space_id)
    if target_path is None or not target_path.exists():
        print(f"[run_vgicp] ベースマップが見つかりません(space_id={space_id})。"
              f"ラフレジ結果をそのまま使います。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rough_ply_path.read_bytes())
        return output_path, None, {}, None, None

    try:
        import open3d as o3d
        import numpy as np
        import pygicp
    except ImportError as e:
        print(f"[run_vgicp] 依存ライブラリが未インストールです({e})。"
              f"pip install open3d pygicp を実行してください。ラフレジ結果をそのまま使います。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rough_ply_path.read_bytes())
        return output_path, None, {}, None

    target_pcd_full = _import_pcd(target_path)
    source_pcd_full = _import_pcd(rough_ply_path)
    target_point_count = len(target_pcd_full.points)
    source_point_count = len(source_pcd_full.points)

    # --- クロップ(sourceのバウンディングボックス+マージンでtargetを絞る) ---
    # 【ログ用(2026-09-02)】このマージン値自体は下のcrop計算と同じ0.1+0.3を
    # 独立に評価しただけの記録用の値であり、crop計算の式(min_bound/max_bound)は
    # 一切変更していない(浮動小数点演算の順序を変えて既存挙動に影響しないため)。
    crop_margin_m_for_log = 0.1 + 0.3
    src_points = np.asarray(source_pcd_full.points)
    bbox = o3d.geometry.AxisAlignedBoundingBox.create_from_points(
        o3d.utility.Vector3dVector(src_points)
    )
    min_bound = bbox.min_bound.copy() - 0.1 - 0.3
    max_bound = bbox.max_bound.copy() + 0.1 + 0.3
    extended_bbox = o3d.geometry.AxisAlignedBoundingBox(min_bound=min_bound, max_bound=max_bound)
    cropped_target = target_pcd_full.crop(extended_bbox)

    if len(cropped_target.points) == 0:
        print(f"[run_vgicp] クロップ後のtargetが空です(source範囲とtargetが重ならない)。"
              f"ラフレジ結果をそのまま使います。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rough_ply_path.read_bytes())
        return output_path, None, {}, None

    # --- ダウンサンプリング ---
    target_ds = cropped_target.voxel_down_sample(DEFAULT_DS_RESOLUTION)
    source_ds = source_pcd_full.voxel_down_sample(DEFAULT_DS_RESOLUTION)
    target_pts_ds = np.asarray(target_ds.points)
    source_pts_ds = np.asarray(source_ds.points)
    source_pts_full = np.asarray(source_pcd_full.points)

    # --- 複数ボクセルサイズを試し、fitness_scoreが最良のものを採用 ---
    log_path = VGICP_LOG_DIR / f"{space_id}_{rough_ply_path.stem}.txt"
    fitness_json_path = VGICP_LOG_DIR / f"{space_id}_{rough_ply_path.stem}.json"
    best_score = float("inf")
    best_points = None
    best_vsize = None
    best_matrix = None  # 採用された変換行列(4x4)。registration_result.json記録用(2026-09-02追加)
    vsize_scores: dict[str, float] = {}
    # 【実験記録の強化(2026-09-02、ユーザー指示)】アルゴリズム(early stopping・
    # voxel size系列・downsampling・fitness計算方法)は一切変更せず、各試行の
    # 詳細(所要時間・成功/失敗・エラー)と実行全体のコンテキストを記録するだけ。
    attempts: list[dict] = []
    early_stopped = False
    early_stopped_at: dict | None = None
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    start = time.perf_counter()

    for vsize in DEFAULT_VOX_SIZES:
        attempt_start = time.perf_counter()
        try:
            with _vgicp_lock:
                vgicp = pygicp.FastVGICP()
                vgicp.set_resolution(vsize)
                vgicp.set_input_target(target_pts_ds)
                vgicp.set_input_source(source_pts_ds)
                matrix = vgicp.align(np.eye(4, dtype=np.float32))
                score = vgicp.get_fitness_score(0.5)
        except Exception as e:
            print(f"[run_vgicp] vsize={vsize}: エラーのためスキップ({e})")
            attempts.append({
                "voxel_size": vsize,
                "fitness_score": None,
                "elapsed_time_sec": time.perf_counter() - attempt_start,
                "status": "failure",
                "error": str(e),
            })
            continue

        attempt_elapsed = time.perf_counter() - attempt_start

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"------------ {vsize} ------------\nfitness_score: {score:.5g}\n")
        vsize_scores[str(vsize)] = score
        attempts.append({
            "voxel_size": vsize,
            "fitness_score": score,
            "elapsed_time_sec": attempt_elapsed,
            "status": "success",
            "error": None,
        })

        if score < best_score:
            best_score = score
            best_points = _transform(source_pts_full, matrix)
            best_vsize = vsize
            best_matrix = matrix.copy()

        if best_score < DEFAULT_EARLY_STOP_THRESHOLD:
            early_stopped = True
            early_stopped_at = {"voxel_size": vsize, "fitness_score": score}
            break  # アーリーストッピング(VGICP_jissho_fast.pyと同じ)

    elapsed = time.perf_counter() - start

    run_info = {
        "timestamp": timestamp,
        "space_id": space_id,
        "target_path": str(target_path),
        "source_path": str(rough_ply_path),
        "target_point_count": target_point_count,
        "source_point_count": source_point_count,
        "target_point_count_downsampled": len(target_pts_ds),
        "source_point_count_downsampled": len(source_pts_ds),
        "downsample_resolution": DEFAULT_DS_RESOLUTION,
        "crop_margin_m": crop_margin_m_for_log,
        "voxel_size_sequence": DEFAULT_VOX_SIZES,
        "early_stop_threshold": DEFAULT_EARLY_STOP_THRESHOLD,
    }

    if best_points is None:
        print(f"[run_vgicp] すべてのボクセルサイズで失敗しました。ラフレジ結果をそのまま使います。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rough_ply_path.read_bytes())
        fitness_json_path.write_text(json.dumps({
            # --- 既存フィールド(後方互換のため形式・意味を変更しない) ---
            "space_id": space_id,
            "source_ply": str(rough_ply_path),
            "generated_at": timestamp,
            "vsize_fitness_scores": vsize_scores,
            "best_vsize": None,
            "best_fitness_score": None,
            "process_time_sec": elapsed,
            # --- 新規追加フィールド(実験記録の強化) ---
            "run_info": run_info,
            "attempts": attempts,
            "result": {
                "selected_voxel_size": None,
                "best_fitness_score": None,
                "total_elapsed_time_sec": elapsed,
                "early_stopped": False,
                "early_stopped_at": None,
                "attempted_voxel_size_count": len(attempts),
                "output_path": str(output_path),
                "status": "all_attempts_failed",
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path, None, {}, None

    result_pcd = o3d.geometry.PointCloud()
    result_pcd.points = o3d.utility.Vector3dVector(best_points)
    result_pcd.colors = source_pcd_full.colors

    output_path.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(output_path), result_pcd)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"----------------------------\nBest size: {best_vsize} m\n"
                f"Best score: {best_score}\nprocess_time: {elapsed:.5g}s\n")
        if early_stopped:
            f.write(
                f"Early stopped at vsize={early_stopped_at['voxel_size']} "
                f"(fitness_score={early_stopped_at['fitness_score']:.5g} "
                f"< threshold {DEFAULT_EARLY_STOP_THRESHOLD})\n"
            )

    fitness_json_path.write_text(json.dumps({
        # --- 既存フィールド(後方互換のため形式・意味を変更しない) ---
        "space_id": space_id,
        "source_ply": str(rough_ply_path),
        "generated_at": timestamp,
        "vsize_fitness_scores": vsize_scores,
        "best_vsize": best_vsize,
        "best_fitness_score": best_score,
        "process_time_sec": elapsed,
        # --- 新規追加フィールド(実験記録の強化。ユーザー指示: 2026-09-02) ---
        "run_info": run_info,
        "attempts": attempts,
        "result": {
            "selected_voxel_size": best_vsize,
            "best_fitness_score": best_score,
            "total_elapsed_time_sec": elapsed,
            "early_stopped": early_stopped,
            "early_stopped_at": early_stopped_at,
            "attempted_voxel_size_count": len(attempts),
            "output_path": str(output_path),
            "status": "ok",
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[run_vgicp] 完了: best_vsize={best_vsize} score={best_score:.5g} ({elapsed:.1f}s)")

    transform_info = {
        "voxel_size": best_vsize,
        "rotation": best_matrix[:3, :3].tolist(),
        "translation": best_matrix[:3, 3].tolist(),
        "fitness_score": best_score,
    }
    return output_path, best_score, vsize_scores, transform_info


# ============================================================
# 空間ID格子でのボクセル化(JSON化)
# ============================================================

def _find_space_definition(space_id: str) -> dict | None:
    """
    space_id(例: "ichigaya_tamachi-G002")から、対応する座標定義JSONを取得する。

    正式な永続化キーはspace_id(`space_definitions/{space_id}.json`)。
    tokutei_code単独キー(`space_definitions/{tokutei_code}.json`)は既存データ
    互換のためのread-onlyなlegacy fallback(新規にはこちらへ書き込まない)。
    両方存在する場合は必ずspace_id側を優先する
    (repositories.local_space_repository.LocalSpaceRepository._load_coordinate_definition
    と同じ規則。ER図反映、2026-09-02)。

    【旧経緯】さらに以前の実装は前方一致("id"が空間IDの特定コード部分で始まる
    ファイルを全部拾い、ファイル名の辞書順で最後のものを採用)だった。この
    ルールだと、orphan化した旧ファイル("G002v3.json"、id="G002v3"、
    voxel_size=0.1mの古いCoordinateDefinition)が、正しい現行ファイル
    ("G002.json"、id="G002"、voxel_size=0.03m)より辞書順で後になるため
    誤って採用されてしまう不具合を実データで確認した(convert_to_scan_json()の
    finest zoom取得の正しさに直結するため、完全一致に統一して解消した)。その後、
    tokutei_code単独キーがbuilding間で衝突しうる問題に対応するため、
    space_id優先+tokutei_codeフォールバックへ変更した。
    """
    def _load(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    space_id_result = _load(SPACE_DEF_DIR / f"{space_id}.json")
    if space_id_result is not None:
        return space_id_result

    tokutei_code = space_id.rsplit("-", 1)[-1]  # 例: "ichigaya_tamachi-G002" -> "G002"
    return _load(SPACE_DEF_DIR / f"{tokutei_code}.json")


def convert_to_scan_json(precise_ply_path: Path, out_dir: Path, space_id: str,
                          zoom_level: int | None = None,
                          fitness_score: float | None = None) -> Path | None:
    """
    精密位置合わせ済みの点群を読み込み、対応するローカル空間の座標定義
    (backend/space_definitions/)を使って空間ID格子でボクセル化し、
    「空間ID → ヒット点数」のJSONとして書き出す。

    fitness_score(run_vgicp()のbest_score)を、そのまま出力JSONに含める。
    更新エンジン(spatial_state)側で w_fit(§2.7)の計算に使うための橋渡し。

    座標定義が見つからない場合は、プレースホルダーのJSONにフォールバックする
    (パイプライン全体を止めないため)。

    【2026-09-02修正】zoom_levelは、固定値(旧DEFAULT_ZOOM_LEVEL=9)を
    使わず、既定では対象Local Space自身のCoordinateDefinitionから
    space_definition_generator.finest_zoom_level()で取得したfinest
    zoomを使う(zoom_level引数を明示的に渡した場合はそれを優先する。
    テスト等での上書き用に残しているだけで、本番の呼び出し元
    (receive_registration_result)は常にNoneのまま呼ぶ)。

    現行のLocal Space生成(space_definition_generator.py)はfinest voxel
    sizeを常にMIN_VOXEL_SIZE=0.03mに固定しているが、それに対応するzoom
    番号自体は部屋の大きさ(unit-sizeの段数)によって空間ごとに異なる
    (例: G002は11、小さい部屋は8等)。そのため「zoom番号を固定値とみなす」
    のではなく、常にその場空間自身のfinest zoomを都度取得する。
    """
    space_def = _find_space_definition(space_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{precise_ply_path.stem}.json"

    if space_def is None:
        print(f"[convert_to_scan_json] 座標定義が見つかりません(space_id={space_id})。"
              f"backend/space_definitions/ にJSONを配置してください。プレースホルダーを出力します。")
        placeholder = {
            "_note": "座標定義が見つからなかったため、ボクセル化していません。",
            "space_id": space_id,
            "source_ply": str(precise_ply_path),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        out_path.write_text(json.dumps(placeholder, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path

    # zoom_level未指定なら、この空間自身のCoordinateDefinitionからfinest
    # zoomを取得する(固定のzoom番号を「3cmのこと」とはみなさない。部屋の
    # 大きさによってfinestのzoom番号自体は空間ごとに異なる)。
    resolved_zoom_level = zoom_level if zoom_level is not None else finest_zoom_level(space_def)

    try:
        import open3d as o3d
        import numpy as np
    except ImportError as e:
        print(f"[convert_to_scan_json] 依存ライブラリが未インストールです({e})。プレースホルダーを出力します。")
        placeholder = {"_note": f"依存ライブラリ未インストール: {e}", "space_id": space_id}
        out_path.write_text(json.dumps(placeholder, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path

    pcd = o3d.io.read_point_cloud(str(precise_ply_path))
    points = np.asarray(pcd.points)

    if len(points) == 0:
        print(f"[convert_to_scan_json] 点群が空です: {precise_ply_path}")
        spatial_ids = []
    else:
        spatial_ids = world_points_to_spatial_ids(points, space_def, resolved_zoom_level)

    # 空間IDごとのヒット数を集計する(Step1: ヒットした空間ID集合、に対応)
    hits: dict[str, int] = {}
    for sid in spatial_ids:
        hits[sid] = hits.get(sid, 0) + 1

    result = {
        "space_id": space_id,
        "zoom_level": resolved_zoom_level,
        "source_ply": str(precise_ply_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "point_count": len(points),
        "voxel_count": len(hits),
        "fitness_score": fitness_score,
        "hits": hits,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[convert_to_scan_json] 完了: {len(points):,}点 → {len(hits):,}ボクセル "
          f"(zoom={resolved_zoom_level}, voxel_size={space_def['unit-size'][str(resolved_zoom_level)]}m)")
    return out_path


# ============================================================
# APIエンドポイント
# ============================================================

@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "local_space_prototype.html")


def _handle_verify_mode(ply_text: str, filename: str, space_id: str):
    """検証用の一時出力(VERIFY_OUTPUT_DIR)に、ラフレジ結果・VGICP結果・
    fitness_scoreを書き出すだけの、状態を一切変更しない経路。

    本番の rough_registered/precise_registered/tracker_state には触れない
    (何度リクエストを送っても、既存の建物・空間の永続状態に影響しない)。
    """
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    run_dir = VERIFY_OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    rough_path = run_dir / "rough.ply"
    rough_path.write_text(ply_text, encoding="utf-8")

    precise_path, fitness_score, vsize_scores, _transform_info = run_vgicp(rough_path, run_dir / "precise.ply", space_id=space_id)

    fitness_json_path = run_dir / "fitness.json"
    fitness_json_path.write_text(json.dumps({
        "run_id": run_id,
        "space_id": space_id,
        "source_filename": filename,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "best_fitness_score": fitness_score,
        "vsize_fitness_scores": vsize_scores,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[verify-mode] {space_id} / {filename} を検証用フォルダに出力しました: {run_dir}")

    return jsonify({
        "status": "ok",
        "mode": "verify",
        "run_id": run_id,
        "space_id": space_id,
        "verify_output_dir": str(run_dir),
        "rough_path": str(rough_path),
        "precise_path": str(precise_path) if precise_path else None,
        "fitness_score": fitness_score,
        "fitness_json_path": str(fitness_json_path),
    })


def _sanitize_path_component(name: str, fallback: str) -> str:
    """ファイル名・ディレクトリ名の1階層分として安全な文字列にする
    (パストラバーサル対策。ディレクトリ区切り・".."を除去し、英数字・
    アンダースコア・ハイフン・ドット以外は"_"に置き換える)。空になった
    場合はfallbackを使う。"""
    name = Path(name).name
    name = re.sub(r"[^0-9A-Za-z_.\-]", "_", name).strip(". ")
    return name or fallback


def _archive_registration_result(
    space_id: str,
    source_filename: str | None,
    uploaded_filename: str,
    rough_path: Path,
    precise_path: Path,
    scan_json_path: Path | None,
    fitness_score: float | None,
    transform_info: dict | None,
) -> Path:
    """VGICP精密位置合わせ結果を、後から人間がSpatial State更新手法の検証データ
    として直接使えるよう、backend/data/registration_results/{space_id}/
    {run_id}_{source_stem}/ へ分かりやすい形でコピー保存する(2026-09-02追加)。

    既存のROUGH_DIR/PRECISE_DIR/SCAN_JSON_DIR/vgicp_logsへの書き込みは一切
    変更しない。ここは、既にそれらへ書き出し済みのファイルを追加でコピー
    するだけの、読みやすさのためのアーカイブ層(source of truthはあくまで
    既存の各ディレクトリのまま)。precise_registered.plyは、Spatial State
    更新に実際に投入したprecise_path(引数)をそのままコピーするため、
    内容はbyte-identicalになる。

    run_idは_handle_verify_mode()と同じ形式({timestamp}_{uuid8桁})。同じ
    source_stem(元Sourceファイル名)であっても、run_idが毎回異なるため、
    複数回送信しても上書きされず個別のフォルダとして残る。
    """
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    source_stem = _sanitize_path_component(
        Path(source_filename).stem if source_filename else Path(uploaded_filename).stem,
        fallback="unknown_source",
    )
    space_id_safe = _sanitize_path_component(space_id, fallback="unknown_space")
    run_dir = REGISTRATION_RESULTS_DIR / space_id_safe / f"{run_id}_{source_stem}"
    run_dir.mkdir(parents=True, exist_ok=True)

    rough_dest = run_dir / "rough_registered.ply"
    precise_dest = run_dir / "precise_registered.ply"
    shutil.copy2(rough_path, rough_dest)
    shutil.copy2(precise_path, precise_dest)

    scan_dest = None
    if scan_json_path is not None and scan_json_path.exists():
        scan_dest = run_dir / "scan.json"
        shutil.copy2(scan_json_path, scan_dest)

    result_json_path = run_dir / "registration_result.json"
    result_json_path.write_text(json.dumps({
        "run_id": run_id,
        "space_id": space_id,
        "source_filename": source_filename,
        "uploaded_filename": uploaded_filename,
        "rough_registered_path": str(rough_dest),
        "precise_registered_path": str(precise_dest),
        "scan_json_path": str(scan_dest) if scan_dest else None,
        "fitness_score": fitness_score,
        "voxel_size": transform_info.get("voxel_size") if transform_info else None,
        "rotation": transform_info.get("rotation") if transform_info else None,
        "translation": transform_info.get("translation") if transform_info else None,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        # コピー元(既存パイプラインの正式な保存場所)への参照。source of
        # truthはあくまでこちら側であり、上のパスはそのコピー。
        "origin": {
            "rough_registered_path": str(rough_path),
            "precise_registered_path": str(precise_path),
            "scan_json_path": str(scan_json_path) if scan_json_path else None,
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[registration_results] アーカイブ保存: {run_dir}")
    return run_dir


@app.route("/api/registration-results", methods=["POST"])
def receive_registration_result():
    filename = request.headers.get("X-Filename") or f"source_{int(time.time())}.ply"
    space_id = request.headers.get("X-Space-Id") or "unknown"
    # 元Sourceファイル名(ユーザーが選択した実際のファイル名)。合成filenameとは
    # 別に、registration_results/のアーカイブで追跡するためだけに使う
    # (2026-09-02追加。未指定なら従来通りfilenameから推測する)。HTTPヘッダは
    # ASCII前提のため、クライアント側でencodeURIComponent()されている。
    source_filename_raw = request.headers.get("X-Source-Filename")
    source_filename = unquote(source_filename_raw) if source_filename_raw else None
    ply_text = request.get_data(as_text=True)

    if not ply_text:
        return jsonify({"error": "本文が空です"}), 400

    # --- 検証モード: 本番の per-space_id フォルダ・更新エンジンには一切触れず、
    # ラフレジ結果・VGICP結果・fitness_scoreを一時フォルダに書き出すだけで終わる。
    # ヘッダ "X-Verify-Mode: 1" が付いている場合のみ有効(既存の追加モードUIは
    # このヘッダを送らないため、本番挙動は変更されない)。
    if request.headers.get("X-Verify-Mode"):
        return _handle_verify_mode(ply_text, filename, space_id)

    # --- Step1: ラフレジ結果を保存する ---
    session_dir = ROUGH_DIR / space_id
    session_dir.mkdir(parents=True, exist_ok=True)
    rough_path = session_dir / filename
    rough_path.write_text(ply_text, encoding="utf-8")
    print(f"[受信] {space_id} / {filename} ({len(ply_text):,} bytes) を保存しました: {rough_path}")

    # --- Step2: VGICP(精密位置合わせ) ---
    precise_dir = PRECISE_DIR / space_id
    precise_path, fitness_score, _, transform_info = run_vgicp(rough_path, precise_dir / filename, space_id=space_id)

    # --- Step3: JSON化(更新エンジンへの入力形式) ---
    scan_json_path = None
    if precise_path is not None:
        scan_json_dir = SCAN_JSON_DIR / space_id
        scan_json_path = convert_to_scan_json(precise_path, scan_json_dir, space_id, fitness_score=fitness_score)

    # --- Step4: Spatial State Updaterへ反映する(オーケストレーションの
    # 中身はbackend/spatial_state_updater.pyへ抽出済み。ここで組み立てる
    # のはscan_json由来のhitsと、このセッション固有のメタデータのみ) ---
    voxel_summary_internal: dict = {}
    if scan_json_path is not None:
        scan_data = json.loads(scan_json_path.read_text(encoding="utf-8"))
        hits = scan_data.get("hits") or {}
        if hits:
            session = {
                "session_id": uuid.uuid4().hex,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source_ply": str(precise_path),
                "patch_fitness_score": fitness_score,
            }
            voxel_summary_internal = apply_scan_to_spatial_state(
                space_id, hits, fitness_score, state_store, params, session=session,
            )

    # Updaterの内部表現(state/confidence_flag/mu/kappa等)をそのままAPI
    # レスポンスに出さず、Read Model(spatial_state_view)を経由させる
    # (Viewer/Integrated ViewをUpdater内部表現へ直接依存させないため)。
    voxel_summary = build_spatial_state_view(voxel_summary_internal)

    # --- Step5: registration_results/ へ、人間が確認しやすい形でアーカイブ
    # 保存する(2026-09-02追加)。既存Step1〜4の出力(rough_path・precise_path・
    # scan_json_path、いずれもこの関数内で既に書き込み済み)をコピーするだけ。
    # 失敗してもStep1〜4の結果・レスポンス自体には影響させない
    # (アーカイブはあくまで追加の利便性機能であり、本番パイプラインの
    # 成否を左右しない)。
    registration_result_dir = None
    if precise_path is not None:
        try:
            registration_result_dir = _archive_registration_result(
                space_id=space_id,
                source_filename=source_filename,
                uploaded_filename=filename,
                rough_path=rough_path,
                precise_path=precise_path,
                scan_json_path=scan_json_path,
                fitness_score=fitness_score,
                transform_info=transform_info,
            )
        except OSError as e:
            print(f"[registration_results] アーカイブ保存に失敗しました(本番パイプラインには影響しません): {e}")

    return jsonify({
        "status": "ok",
        "space_id": space_id,
        "rough_registered_path": str(rough_path),
        "precise_registered_path": str(precise_path) if precise_path else None,
        "scan_json_path": str(scan_json_path) if scan_json_path else None,
        "fitness_score": fitness_score,
        "voxel_summary": voxel_summary,
        "registration_result_dir": str(registration_result_dir) if registration_result_dir else None,
    })


_REGISTRATION_RESULT_ALLOWED_FILENAMES = {
    "precise_registered.ply", "rough_registered.ply", "scan.json", "registration_result.json",
}


@app.route("/api/registration-results/<space_id>", methods=["GET"])
def list_registration_results(space_id):
    """backend/data/registration_results/{space_id}/ 配下の全run(registration_result.json)
    を新しい順で一覧表示する(GUIのRegistration Result画面用、2026-09-02追加)。"""
    space_dir = REGISTRATION_RESULTS_DIR / _sanitize_path_component(space_id, fallback="unknown_space")
    if not space_dir.exists():
        return jsonify({"space_id": space_id, "results": []})

    results = []
    for run_dir in sorted((p for p in space_dir.iterdir() if p.is_dir()), reverse=True):
        result_json_path = run_dir / "registration_result.json"
        if not result_json_path.exists():
            continue
        try:
            data = json.loads(result_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        data["run_dir"] = run_dir.name
        data["has_precise_ply"] = (run_dir / "precise_registered.ply").exists()
        data["has_precise_global_ply"] = (run_dir / "precise_registered_global.ply").exists()
        results.append(data)

    return jsonify({"space_id": space_id, "results": results})


@app.route("/api/registration-results/<space_id>/<run_dir>/<filename>", methods=["GET"])
def get_registration_result_file(space_id, run_dir, filename):
    """registration_results/{space_id}/{run_dir}/{filename} を配信する
    (GUIでprecise_registered.ply等を直接確認するため、2026-09-02追加)。
    許可された4ファイル名のみ・send_from_directoryでREGISTRATION_RESULTS_DIR
    配下に限定し、パストラバーサルを防ぐ。"""
    if filename not in _REGISTRATION_RESULT_ALLOWED_FILENAMES:
        return jsonify({"error": "許可されていないファイル名です"}), 400
    target_dir = (
        REGISTRATION_RESULTS_DIR
        / _sanitize_path_component(space_id, fallback="unknown_space")
        / _sanitize_path_component(run_dir, fallback="unknown_run")
    )
    if not (target_dir / filename).exists():
        return jsonify({"error": "ファイルが見つかりません"}), 404
    return send_from_directory(str(target_dir), filename)


@app.route("/api/registration-results/<space_id>/<run_dir>/export-global", methods=["POST"])
def export_registration_result_to_global(space_id, run_dir):
    """registration_results/{space_id}/{run_dir}/precise_registered.ply を、
    直近に保存済みのSpatial Resolution Result(RESOLVED済みのcomponentのみ)を
    使ってGlobal座標へ変換し、同じrunフォルダ内に別ファイル
    (precise_registered_global.ply + metadata)として書き出す(2026-09-03追加)。

    - 座標変換はservices.global_export_service.export_precise_registered_to_global()
      (内部でservices.global_coordinate_service.world_points_to_resolved_global()を
      使う)をそのまま呼ぶだけで、独自の変換式・独自のtransform再計算は行わない。
    - Nodal Information・Spatial Resolutionはここでは一切再実行しない。
      spatial_resolution_result_repo.load()で直近の保存済み結果を読むだけ。
    - Global RESOLVEDでない場合はfail-closedとし、レスポンスのerror_codeで
      NO_ANCHOR/GLOBAL_CONFLICT/ANCHOR_INSUFFICIENT等の状態を区別できるように
      する(GlobalResolutionStatusの値をそのまま使う)。
    """
    safe_space_id = _sanitize_path_component(space_id, fallback="unknown_space")
    safe_run_dir = _sanitize_path_component(run_dir, fallback="unknown_run")
    run_path = REGISTRATION_RESULTS_DIR / safe_space_id / safe_run_dir
    precise_ply_path = run_path / "precise_registered.ply"
    if not precise_ply_path.exists():
        return jsonify({
            "error": f"precise_registered.plyが見つかりません: {space_id}/{run_dir}",
            "error_code": "PRECISE_REGISTERED_NOT_FOUND",
        }), 404

    local_space = local_space_repo.get(space_id)
    if local_space is None:
        return jsonify({
            "error": f"space_id '{space_id}' が見つかりません。",
            "error_code": "LOCAL_SPACE_NOT_FOUND",
        }), 404

    space_def = _find_space_definition(space_id)
    if space_def is None:
        return jsonify({
            "error": f"space_id '{space_id}' のCoordinateDefinitionが見つかりません。",
            "error_code": "SPACE_DEFINITION_NOT_FOUND",
        }), 404

    result = spatial_resolution_result_repo.load(local_space.building_id)
    if result is None:
        return jsonify({
            "error": f"building '{local_space.building_id}' はまだSpatial Resolutionが実行されていません。",
            "error_code": "NO_RESOLUTION_RESULT",
        }), 404

    component_dict = next(
        (c for c in result.get("components", []) if space_id in c["local_placement"]["member_space_ids"]),
        None,
    )
    if component_dict is None:
        return jsonify({
            "error": f"space_id '{space_id}' はどのcomponent(Local↔Local connectionのグループ)にも属していません。",
            "error_code": "SPACE_NOT_IN_ANY_COMPONENT",
        }), 404

    global_resolution = ComponentGlobalResolution.from_dict(component_dict["global_resolution"])
    if global_resolution.status != GlobalResolutionStatus.RESOLVED:
        return jsonify({
            "error": (
                f"space_id '{space_id}' が属するcomponentはGlobal未解決です"
                f"(status={global_resolution.status.value})。"
            ),
            "error_code": global_resolution.status.value,
        }), 409

    output_path = run_path / "precise_registered_global.ply"
    try:
        metadata = export_precise_registered_to_global(
            precise_ply_path, output_path, space_id, space_def, global_resolution,
            run_id=run_dir, resolved_at=result.get("resolved_at"),
        )
    except GlobalCoordinateResolutionError as e:
        # world_points_to_resolved_global()自身のfail-closed(上のstatusチェックと
        # 二重になるが、念のための安全策として残す)。
        return jsonify({"error": str(e), "error_code": "GLOBAL_TRANSFORM_UNAVAILABLE"}), 409
    except GlobalExportError as e:
        return jsonify({"error": str(e), "error_code": "EXPORT_FAILED"}), 400

    return jsonify({
        "success": True,
        "space_id": space_id,
        "run_id": run_dir,
        "target_epsg": metadata["target_epsg"],
        "output_artifact": str(output_path),
        "metadata_artifact": str(output_path.with_suffix(output_path.suffix + ".meta.json")),
        "metadata": metadata,
    })


@app.route("/api/spatial-state/<space_id>", methods=["GET"])
def get_spatial_state(space_id):
    """space_idごとの、全ボクセルの現在の表示状態一覧を返す(3Dビューワ用)。

    ここで返す値はSpatial State Updaterの内部表現(alpha/beta/mu/kappa/
    state/confidence_flag)ではなく、spatial_state_view.build_spatial_state_view()
    を経由したRead Model(presence/confidence/mobility)。Viewer/Integrated
    Viewは、この安定した語彙だけに依存し、内部表現を直接参照しない。
    """
    tracker = state_store.load(space_id, params)
    return jsonify({
        "space_id": space_id,
        "voxels": build_spatial_state_view(tracker.summary()),
    })


# ============================================================
# 建物・ローカル空間の追加(GUIからの一気通貫フロー)
# ============================================================

def _update_base_map_manifest(entry_id: str, label: str, filename: str) -> None:
    """base_maps/manifest.json に、新しいベースマップのエントリを追記する
    (同じidが既にあれば、fileを差し替える)。"""
    manifest_path = BASE_MAPS_DIR / "manifest.json"
    manifest = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = [e for e in manifest if e.get("id") != entry_id]
    manifest.append({"id": entry_id, "label": label, "file": filename})
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/api/buildings", methods=["GET"])
def list_buildings():
    return jsonify({"buildings": [b.to_dict() for b in building_repo.list_all()]})


@app.route("/api/buildings", methods=["POST"])
def create_building():
    body = request.get_json(force=True, silent=True) or {}
    try:
        building = building_repo.create(
            name=body.get("name", ""),
            real_estate_number=body.get("real_estate_number", ""),
            address=body.get("address", ""),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "building": building.to_dict()})


@app.route("/api/buildings/<building_id>/local-spaces", methods=["GET"])
def list_building_local_spaces(building_id):
    # Local Spaceの一覧はlocal_space_repo(backend/data/registry/local_spaces.json、
    # Phase 2 Domain/Repository)をsource of truthとする(ロードマップPhase 3.7、
    # 旧registry側の一覧取得メソッドは使わない。理由はcreate_local_space()参照)。
    spaces = local_space_repo.list_all(building_id=building_id)
    return jsonify({"building_id": building_id, "local_spaces": [s.to_dict() for s in spaces]})


# ============================================================
# Nodal Information CRUD / Spatial Resolution実行入口
# (ロードマップPhase 3.6。Spatial State・point-cloud registrationとは
#  完全に独立したAPI群。既存の実行系には一切変更を加えていない)
# ============================================================


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@app.route("/api/nodal-endpoints", methods=["GET"])
def list_nodal_endpoints():
    space_id = request.args.get("space_id")
    endpoints = nodal_endpoint_repo.list_all(space_id=space_id)
    return jsonify({"endpoints": [e.to_dict() for e in endpoints]})


@app.route("/api/nodal-endpoints", methods=["POST"])
def create_nodal_endpoint():
    body = request.get_json(force=True, silent=True) or {}
    try:
        endpoint = NodalEndpoint(
            endpoint_id=str(uuid.uuid4()),
            type=NodalEndpointType(body["type"]),
            label=body.get("label"),
            created_at=_iso_now(),
            updated_at=_iso_now(),
            space_id=body.get("space_id"),
            local_spatial_id=body.get("local_spatial_id"),
            local_point=body.get("local_point"),
            global_spatial_id=body.get("global_spatial_id"),
        )
        nodal_endpoint_repo.create(endpoint)
    except (ValueError, KeyError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "endpoint": endpoint.to_dict()})


@app.route("/api/nodal-endpoints/<endpoint_id>", methods=["GET"])
def get_nodal_endpoint(endpoint_id):
    endpoint = nodal_endpoint_repo.get(endpoint_id)
    if endpoint is None:
        return jsonify({"error": f"endpoint_id '{endpoint_id}' が見つかりません。"}), 404
    return jsonify({"endpoint": endpoint.to_dict()})


@app.route("/api/nodal-endpoints/<endpoint_id>", methods=["PUT"])
def update_nodal_endpoint(endpoint_id):
    body = request.get_json(force=True, silent=True) or {}
    try:
        endpoint = NodalEndpoint(
            endpoint_id=endpoint_id,
            type=NodalEndpointType(body["type"]),
            label=body.get("label"),
            created_at=body.get("created_at"),
            updated_at=_iso_now(),
            space_id=body.get("space_id"),
            local_spatial_id=body.get("local_spatial_id"),
            local_point=body.get("local_point"),
            global_spatial_id=body.get("global_spatial_id"),
        )
        nodal_endpoint_repo.update(endpoint)
    except (ValueError, KeyError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "endpoint": endpoint.to_dict()})


@app.route("/api/nodal-endpoints/<endpoint_id>", methods=["DELETE"])
def delete_nodal_endpoint(endpoint_id):
    nodal_endpoint_repo.delete(endpoint_id)
    return jsonify({"status": "ok"})


@app.route("/api/nodal-connections", methods=["GET"])
def list_nodal_connections():
    building_id = request.args.get("building_id")
    connections = nodal_connection_repo.list_all(building_id=building_id)
    return jsonify({"connections": [c.to_dict() for c in connections]})


@app.route("/api/nodal-connections", methods=["POST"])
def create_nodal_connection():
    body = request.get_json(force=True, silent=True) or {}
    try:
        connection = NodalConnection(
            connection_id=str(uuid.uuid4()),
            building_id=body["building_id"],
            endpoint_space_a=ConnectionEndpointRef.from_dict(body["endpoint_space_a"]),
            endpoint_space_b=ConnectionEndpointRef.from_dict(body["endpoint_space_b"]),
            correspondences=[],
            created_at=_iso_now(),
            updated_at=_iso_now(),
        )
        nodal_connection_repo.create(connection)
    except (ValueError, KeyError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "connection": connection.to_dict()})


@app.route("/api/nodal-connections/<connection_id>", methods=["GET"])
def get_nodal_connection(connection_id):
    connection = nodal_connection_repo.get(connection_id)
    if connection is None:
        return jsonify({"error": f"connection_id '{connection_id}' が見つかりません。"}), 404
    return jsonify({"connection": connection.to_dict()})


@app.route("/api/nodal-connections/<connection_id>", methods=["DELETE"])
def delete_nodal_connection(connection_id):
    nodal_connection_repo.delete(connection_id)
    return jsonify({"status": "ok"})


@app.route("/api/nodal-connections/<connection_id>/correspondences", methods=["POST"])
def add_nodal_connection_correspondence(connection_id):
    body = request.get_json(force=True, silent=True) or {}
    connection = nodal_connection_repo.get(connection_id)
    if connection is None:
        return jsonify({"error": f"connection_id '{connection_id}' が見つかりません。"}), 404
    try:
        correspondence = Correspondence(
            pair_id=str(uuid.uuid4()),
            node_a_id=body["node_a_id"],
            node_b_id=body["node_b_id"],
        )
    except KeyError as e:
        return jsonify({"error": f"必須フィールドがありません: {e}"}), 400
    connection.correspondences.append(correspondence)
    connection.updated_at = _iso_now()
    nodal_connection_repo.update(connection)
    return jsonify({"status": "ok", "connection": connection.to_dict()})


@app.route("/api/nodal-connections/<connection_id>/estimate", methods=["POST"])
def estimate_nodal_connection(connection_id):
    connection = nodal_connection_repo.get(connection_id)
    if connection is None:
        return jsonify({"error": f"connection_id '{connection_id}' が見つかりません。"}), 404
    local_resolver = LocalSpatialIdResolver(local_space_repo)
    global_resolver = StandardSpatialIdResolver()
    try:
        updated = estimate_connection_solution(connection, nodal_endpoint_repo, local_resolver, global_resolver)
        nodal_connection_repo.update(updated)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "connection": updated.to_dict()})


@app.route("/api/spatial-resolution/resolve", methods=["POST"])
def resolve_spatial_resolution():
    body = request.get_json(force=True, silent=True) or {}
    building_id = body.get("building_id")
    if not building_id:
        return jsonify({"error": "building_idは必須です。"}), 400
    target_epsg = body.get("target_epsg", DEFAULT_TARGET_EPSG)
    try:
        results = resolve_building(building_id, nodal_connection_repo, nodal_endpoint_repo, local_space_repo, target_epsg)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    saved = spatial_resolution_result_repo.save(building_id, results, target_epsg)
    return jsonify({"status": "ok", "result": saved})


@app.route("/api/spatial-resolution/results/<building_id>", methods=["GET"])
def get_spatial_resolution_result(building_id):
    result = spatial_resolution_result_repo.load(building_id)
    if result is None:
        return jsonify({"error": f"building_id '{building_id}' の結果がまだありません。"}), 404
    return jsonify({"result": result})


def _compute_space_id(building_id: str, tokutei_code: str) -> str:
    """registry.create_local_spaceと同じ規則(building_id-tokutei_code)。
    Local Space登録前(Detect Planes時点)でも、最終的なspace_idを前もって
    計算するために使う(Plane/VoxelLabelの保存キーをspace_id単位に揃えるため、
    duplicateしているが1行のみ)。"""
    return f"{building_id}-{tokutei_code}"




@app.route("/api/planes/detect", methods=["POST"])
def detect_planes():
    """ベースマップ(.las/.ply、生バイト)をアップロードし、RANSACで構造平面を
    反復抽出する(Local Space生成フローの最初のステップ)。

    まだregistry(建物配下のローカル空間一覧)にはLocal Spaceを作成しない
    (それは/api/local-spacesの責務)。ここではbase_maps/{space_id}{ext}
    への保存とmanifest登録、およびPlaneの検出・保存(backend/data/planes/
    {space_id}.json)のみを行う。

    space_idは、後で実際に作成されるLocal Spaceと同じ規則
    (building_id-tokutei_code)で前もって計算し、Planeの保存キー・
    ベースマップの保存キーとして使う(2026-09-02: 正式な永続化キーを
    tokutei_code単独からspace_idへ変更。ER図反映)。
    """
    building_id = request.headers.get("X-Building-Id")
    tokutei_code = request.headers.get("X-Tokutei-Code")
    filename = request.headers.get("X-Filename") or "base_map.las"

    if not building_id or not tokutei_code:
        return jsonify({"error": "X-Building-Id・X-Tokutei-Codeヘッダが必要です"}), 400
    if building_repo.get(building_id) is None:
        return jsonify({"error": f"building_id '{building_id}' が見つかりません"}), 404

    body = request.get_data()
    if not body:
        return jsonify({"error": "本文が空です(ベースマップファイルを送ってください)"}), 400

    space_id = _compute_space_id(building_id, tokutei_code)

    ext = Path(filename).suffix or ".las"
    base_map_filename = f"{space_id}{ext}"
    base_map_path = BASE_MAPS_DIR / base_map_filename
    base_map_path.parent.mkdir(parents=True, exist_ok=True)
    base_map_path.write_bytes(body)
    _update_base_map_manifest(space_id, space_id, base_map_filename)

    # 【cache invalidation(2026-09-01調査)】既存のtokutei_codeに対して
    # Base Mapが再アップロードされた場合、finest Spatial ID voxel(positions/ids)は
    # 旧点群から計算されたまま残ってしまう。上位levelのderived aggregation・
    # voxel color(Visualization Step 4)もfinestを起点に導出されるため連鎖的に
    # 古いまま返り続ける。space_id単位で(全zoom level・全mode)無条件に破棄する。
    spatial_voxel_cache_repo.invalidate(space_id)
    voxel_color_cache_repo.invalidate(space_id)

    try:
        import numpy as np
        pcd = _import_pcd(base_map_path)
        points = np.asarray(pcd.points)
        planes = segment_planes(points, space_id=space_id, config=PlaneSegmentationConfig())
    except Exception as e:
        return jsonify({"error": f"平面抽出に失敗しました: {e}"}), 500

    plane_repo.save_planes(space_id, planes)

    return jsonify({
        "status": "ok",
        "space_id": space_id,
        "point_count": int(len(points)),
        "plane_count": len(planes),
        "planes": [p.to_dict() for p in planes],
    })


@app.route("/api/planes/<space_id>", methods=["GET"])
def list_planes(space_id):
    planes = plane_repo.load_planes(space_id)
    return jsonify({"space_id": space_id, "planes": [p.to_dict() for p in planes]})


@app.route("/api/planes/<space_id>/<plane_id>", methods=["PATCH"])
def update_plane_label(space_id, plane_id):
    """Planeのconfirmed_labelをユーザーが変更する(完全自動分類にしないための
    唯一の書き込み経路)。suggested_labelは変更しない(元の提案として残す)。"""
    body = request.get_json(silent=True) or {}
    confirmed_label_raw = body.get("confirmed_label")
    if not confirmed_label_raw:
        return jsonify({"error": "confirmed_labelが必要です"}), 400
    try:
        confirmed_label = StructuralLabel(confirmed_label_raw)
        plane = plane_repo.update_plane_label(space_id, plane_id, confirmed_label)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "plane": plane.to_dict()})


@app.route("/api/local-spaces", methods=["POST"])
def create_local_space():
    """既に/api/planes/detectでアップロード・平面抽出済みのベースマップを使い、
    以下を一括で行う:

    1. base_maps/{space_id}{ext} を読み込む(本文にファイルが添付されて
       いれば、それで上書き保存してから使う。従来通りの直接アップロードも
       後方互換として許容する。tokutei_code単独キーのファイルはread-only
       legacy fallbackとしてのみ参照する)
    2. 点群から座標定義を自動生成し、space_definitions/{space_id}.json に書き出す
       (minimum voxel sizeは常にMIN_VOXEL_SIZE=0.03m固定。backend/
       space_definition_generator.py、gen_local_spatialid.zip 由来の
       アルゴリズムを移植したものを再利用)
    3. registry(建物配下のローカル空間一覧)に登録する
    4. backend/data/planes/{space_id}.json のPlane(confirmed_labelが
       FLOOR/CEILING/WALLのもの)を、このLocal Space自身のCoordinateDefinition
       で最小(3cm)Local Spatial IDへ変換し、SPATIAL_VOXEL_LABEL・
       LABEL_FITNESS_HISTORY相当のデータとして保存する

    X-Rotation-Degree(度)が指定されればその角度をそのまま使う(PCA自動検出は
    行わない)。未指定ならPCA自動検出にフォールバックする(既存呼び出し元との
    後方互換のため。GUIからの新規作成では常に指定される想定)。

    ここで生成する座標定義(origin/rotation/bounds/unit-size)はいずれも
    provisionalであり、resolved global placementとは無関係
    (Phase 3のNodal Information側の関心事)。
    """
    building_id = request.headers.get("X-Building-Id")
    tokutei_code = request.headers.get("X-Tokutei-Code")
    filename = request.headers.get("X-Filename") or "base_map.las"
    floor_raw = request.headers.get("X-Floor", "1")
    rotation_degree_raw = request.headers.get("X-Rotation-Degree")

    if not building_id or not tokutei_code:
        return jsonify({"error": "X-Building-Id・X-Tokutei-Codeヘッダが必要です"}), 400

    if building_repo.get(building_id) is None:
        return jsonify({"error": f"building_id '{building_id}' が見つかりません"}), 404

    try:
        floor = int(floor_raw)
    except ValueError:
        return jsonify({"error": "X-Floorは数値である必要があります"}), 400

    rotation_rad = None
    if rotation_degree_raw not in (None, ""):
        try:
            rotation_rad = math.radians(float(rotation_degree_raw))
        except ValueError:
            return jsonify({"error": "X-Rotation-Degreeは数値である必要があります"}), 400

    # base_maps・space_definitionsの正式な永続化キーはspace_id
    # (tokutei_code単独キーはlegacy fallback。ER図反映、2026-09-02)。
    space_id = _compute_space_id(building_id, tokutei_code)

    body = request.get_data()
    if body:
        # 後方互換: 本文にファイルが添付されていれば、従来通りここで保存する
        # (/api/planes/detect を経由しない直接呼び出しもこれで動作する)。
        ext = Path(filename).suffix or ".las"
        base_map_filename = f"{space_id}{ext}"
        base_map_path = BASE_MAPS_DIR / base_map_filename
        base_map_path.parent.mkdir(parents=True, exist_ok=True)
        base_map_path.write_bytes(body)
        _update_base_map_manifest(space_id, space_id, base_map_filename)
    else:
        base_map_path = _find_base_map_path(space_id)
        if base_map_path is None:
            return jsonify({
                "error": f"space_id '{space_id}'(またはtokutei_code '{tokutei_code}')の"
                         f"ベースマップが見つかりません。先に /api/planes/detect で"
                         f"ベースマップをアップロードしてください。"
            }), 400

    try:
        import numpy as np
        pcd = _import_pcd(base_map_path)
        points = np.asarray(pcd.points)
        space_def = generate_space_definition(
            points, space_def_id=space_id, rotation_rad=rotation_rad,
        )
    except Exception as e:
        return jsonify({"error": f"座標定義の生成に失敗しました: {e}"}), 500

    # 正式な永続化キーはspace_id(ER図反映、2026-09-02)。space_def内の"id"も
    # ファイル名と一致させる(space_def_idにspace_idを渡している、上記参照)。
    space_def_path = SPACE_DEF_DIR / f"{space_id}.json"
    space_def_path.write_text(json.dumps(space_def, ensure_ascii=False, indent=2), encoding="utf-8")

    # 【cache invalidation(2026-09-01調査)】既存のspace_idに対してこの
    # エンドポイントが再度呼ばれ、CoordinateDefinitionが再生成された場合
    # (直後のregistry.create_local_spaceがspace_id重複でエラーになる場合も
    # 含む。space_def_pathは既にこの時点で上書き済みのため)、古い
    # CoordinateDefinitionを前提に計算されたSpatial ID voxel cache・voxel
    # color cacheが残っていると、新しい座標定義と食い違ったまま返り続ける。
    # space_id単位で(全zoom level・全mode)無条件に破棄する。
    spatial_voxel_cache_repo.invalidate(space_id)
    voxel_color_cache_repo.invalidate(space_id)

    # Local Spaceごとにunit-sizeの段数(zoom level数)が独立して決まるため、
    # このLocal Spaceの基準ズームレベルは、実際に生成された系列の最も細かい
    # 段(最終index、常にMIN_VOXEL_SIZE=0.03mに一致する)とする。
    unit_size_levels = sorted(int(k) for k in space_def["unit-size"].keys())
    space_zoom_level = unit_size_levels[-1]

    try:
        # Local Spaceの永続化はlocal_space_repo(backend/data/registry/、
        # Phase 2 Domain/Repository)を唯一のsource of truthとする(ロードマップ
        # Phase 3.7)。旧registry側のlocal space作成メソッド(backend/local_spaces.json)
        # は書き込み先が分裂する原因だったため、ここでは呼ばない。
        # 建物側もbuilding_repo(BuildingRepository)に一本化済み(ER図反映、
        # 2026-09-02)。registry.pyは両者とも呼び出し元ゼロになったが、
        # ロールバック用に削除せず残置している。
        local_space = local_space_repo.create(
            building_id=building_id, tokutei_code=tokutei_code,
            floor=floor, zoom_level=space_zoom_level,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    space_id = local_space.space_id

    # Plane -> 3cm Local Spatial ID 構造ラベルの変換(FLOOR/CEILING/WALLのみ)。
    # Planeが1件も検出/保存されていなければ、voxel labelは0件のまま(エラーにしない)。
    planes = plane_repo.load_planes(space_id)
    voxel_label_count = 0
    if planes:
        voxel_labels, history_entries = build_voxel_labels(space_id, space_def, planes, points)
        voxel_label_repo.save_all(space_id, voxel_labels)
        label_fitness_history_repo.append(space_id, history_entries)
        voxel_label_count = len(voxel_labels)

    min_voxel_size = space_def["unit-size"][str(unit_size_levels[-1])]
    max_voxel_size = space_def["unit-size"][str(unit_size_levels[0])]

    return jsonify({
        "status": "ok",
        "local_space": local_space.to_dict(),
        "space_definition_path": str(space_def_path),
        "base_map_path": str(base_map_path),
        "plane_count": len(planes),
        "voxel_label_count": voxel_label_count,
        "space_definition_summary": {
            "degree": space_def["degree"],
            "rad": space_def["rad"],
            "height": space_def["height"],
            "origin": space_def["origin"],
            "bounds": space_def["bounds"],
            "zoom_level_count": len(unit_size_levels),
            "min_voxel_size": min_voxel_size,
            "max_voxel_size": max_voxel_size,
        },
    })


def _build_deletion_context() -> DeletionContext:
    return DeletionContext(
        local_space_repo=local_space_repo,
        nodal_endpoint_repo=nodal_endpoint_repo,
        nodal_connection_repo=nodal_connection_repo,
        spatial_resolution_result_repo=spatial_resolution_result_repo,
        spatial_voxel_cache_repo=spatial_voxel_cache_repo,
        voxel_color_cache_repo=voxel_color_cache_repo,
        space_def_dir=SPACE_DEF_DIR,
        base_maps_dir=BASE_MAPS_DIR,
        planes_dir=PLANES_DIR,
        voxel_labels_dir=VOXEL_LABELS_DIR,
        structural_label_history_dir=STRUCTURAL_LABEL_HISTORY_DIR,
        tracker_state_dir=TRACKER_STATE_DIR,
        registration_results_dir=REGISTRATION_RESULTS_DIR,
        rough_dir=ROUGH_DIR,
        precise_dir=PRECISE_DIR,
        scan_json_dir=SCAN_JSON_DIR,
        vgicp_log_dir=VGICP_LOG_DIR,
        archive_root=DATA_DIR / "_archived_local_spaces",
    )


@app.route("/api/local-spaces/<space_id>/deletion-preview", methods=["GET"])
def preview_local_space_deletion(space_id):
    """Local Space削除のdry-run。何もファイルを変更しない(read-only)。
    アーカイブ対象・invalidate対象キャッシュ・影響するNodal Endpoint/
    Connectionの一覧を返す(削除実行(DELETE)と同じbuild_deletion_plan()を
    使うため、previewと実削除内容は必ず一致する)。"""
    try:
        plan = build_deletion_plan(_build_deletion_context(), space_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(plan.to_dict())


@app.route("/api/local-spaces/<space_id>", methods=["DELETE"])
def delete_local_space(space_id):
    """Local Spaceを削除する(space_id・tokutei_codeは再利用可能になる)。

    実験記録性のあるデータ(CoordinateDefinition・Base Map・Plane・
    Structural Label・Spatial State・Registration Result等)は
    backend/data/_archived_local_spaces/ へコピー→完全性確認→削除、の順で
    退避する(fail-closed。アーカイブが確認できるまでactive側は変更しない)。
    表示用キャッシュ(spatial_voxel_cache・voxel_color_cache・
    spatial_resolution_results)は再生成可能なため、そのまま破棄する。
    Nodal Endpoint/Connectionはアーカイブmanifestへスナップショット後に
    削除する(他space_idのEndpoint自体は削除しない)。
    Buildingは削除しない。"""
    ctx = _build_deletion_context()
    try:
        plan = build_deletion_plan(ctx, space_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    try:
        result = execute_deletion(ctx, plan)
    except DeletionArchiveError as e:
        return jsonify({"error": f"アーカイブの検証に失敗したため、削除を中止しました(active側のデータは"
                                  f"変更されていません): {e}"}), 500

    return jsonify(result.to_dict())


# ============================================================
# Spatial ID voxel(Step 1のvoxelize_base_map_pointsをViewerへ接続する、
# ロードマップStep 2)+ zoom level切り替え(ロードマップStep 3)
# ============================================================

def _load_space_def_for_space_id(space_id: str) -> tuple:
    """space_idからtokutei_code・space_defを引く(既存local-spaces系
    エンドポイントと同じ規則)。見つからなければValueError。
    local_space_repo(Phase 2 Domain/Repository)をsource of truthとする
    (ロードマップPhase 3.7、旧registry側の一覧取得メソッドは使わない)。

    座標定義ファイルの検索はspace_id優先/tokutei_codeフォールバック
    (_find_space_definition()と同じ規則。ER図反映、2026-09-02)。"""
    local_space = local_space_repo.get(space_id)
    if local_space is None:
        raise ValueError(f"space_id '{space_id}' が見つかりません。")
    tokutei_code = local_space.tokutei_code

    space_def = _find_space_definition(space_id)
    if space_def is None:
        raise ValueError(
            f"space_definitions/{space_id}.json も space_definitions/{tokutei_code}.json も"
            f"見つかりません。"
        )
    return tokutei_code, space_def


def _resolve_requested_zoom_level(space_def: dict, zoom_level_raw, finest: int) -> int:
    """クエリパラメータ(文字列 or None)を、そのspace_defで有効なzoom_level
    整数に検証・変換する。未指定ならfinestを返す。"""
    if zoom_level_raw in (None, ""):
        return finest
    try:
        zoom_level = int(zoom_level_raw)
    except (TypeError, ValueError):
        raise ValueError(f"zoom_levelは整数である必要があります: {zoom_level_raw!r}")
    if str(zoom_level) not in space_def["unit-size"]:
        raise ValueError(
            f"zoom_level {zoom_level} はこのLocal Spaceに存在しません"
            f"(有効なzoom_level: {sorted((int(k) for k in space_def['unit-size']))})。"
        )
    return zoom_level


def _get_or_build_finest_spatial_voxel_cache(space_id: str, space_def: dict, tokutei_code: str,
                                              finest: int) -> dict:
    """finest levelのキャッシュ(meta+バイナリ)を返す。無ければBase Map点群から
    構築してキャッシュする(Step 1のvoxelize_base_map_pointsを1回だけ実行)。

    Base Map全点からのvoxelize_base_map_pointsは、実データ(G002・約123万点)
    で約30〜40秒かかることを実測済み(2026-08-29)。毎リクエスト再計算しないよう、
    初回のみ計算しSpatialVoxelCacheRepositoryへ永続化する(source of truthの
    複製ではなく、Base Map・CoordinateDefinitionから再生成可能なderived
    cache)。
    """
    cached = spatial_voxel_cache_repo.load_meta(space_id, finest)
    if cached is not None:
        return cached

    base_map_path = _find_base_map_path(space_id)
    if base_map_path is None:
        raise ValueError(f"space_id '{space_id}'(またはtokutei_code '{tokutei_code}')のベースマップが見つかりません。")

    import numpy as np
    pcd = _import_pcd(base_map_path)
    points = np.asarray(pcd.points)

    voxels = voxelize_base_map_points(space_id, space_def, points)
    return spatial_voxel_cache_repo.save(space_id, voxels)


def _get_or_build_spatial_voxel_cache(space_id: str, zoom_level_raw=None) -> dict:
    """space_id・(任意の)zoom_levelのSpatial ID voxelキャッシュ(meta+バイナリ)を
    返す。zoom_level未指定ならfinest。

    finestより粗いzoom_levelが要求された場合は、Base Mapを再読込・再voxelize
    せず、finestキャッシュ(voxel_center配列)を入力にspatial_voxel_aggregation
    でderivedする(ロードマップStep 3。上位levelは保存済みのfinestキャッシュ
    からのみ導出し、Base Map点群には二度と触れない)。
    """
    tokutei_code, space_def = _load_space_def_for_space_id(space_id)
    finest = finest_zoom_level(space_def)
    zoom_level = _resolve_requested_zoom_level(space_def, zoom_level_raw, finest)

    cached = spatial_voxel_cache_repo.load_meta(space_id, zoom_level)
    if cached is not None:
        return cached

    if zoom_level == finest:
        return _get_or_build_finest_spatial_voxel_cache(space_id, space_def, tokutei_code, finest)

    _get_or_build_finest_spatial_voxel_cache(space_id, space_def, tokutei_code, finest)
    finest_positions = spatial_voxel_cache_repo.load_positions(space_id, finest)
    voxels = aggregate_finest_positions_to_zoom_level(space_id, space_def, finest_positions, finest, zoom_level)
    return spatial_voxel_cache_repo.save(space_id, voxels)


@app.route("/api/local-spaces/<space_id>/spatial-voxels/levels", methods=["GET"])
def get_spatial_voxel_levels(space_id):
    """このLocal Space自身のCoordinateDefinition.unit-sizeから、選択可能な
    zoom level(とそのvoxel_size)一覧をfinest→coarseの順で返す
    (GUIのlevel選択UI用。他のLocal Spaceのunit-sizeは一切参照しない)。"""
    try:
        _, space_def = _load_space_def_for_space_id(space_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    finest = finest_zoom_level(space_def)
    levels = sorted(
        ({"zoom_level": int(z), "voxel_size": s} for z, s in space_def["unit-size"].items()),
        key=lambda e: -e["zoom_level"],
    )
    return jsonify({"space_id": space_id, "finest_zoom_level": finest, "levels": levels})


@app.route("/api/local-spaces/<space_id>/spatial-voxels", methods=["GET"])
def get_spatial_voxels_meta(space_id):
    """Spatial ID voxelのmeta情報を返す(初回はBase Mapまたはfinestキャッシュから
    構築、以降はキャッシュを返すため高速)。実データ配列はpositions.binで別途
    取得する(巨大JSONを1本で返さない)。

    ?zoom_level=<int> クエリパラメータで、finestより粗いlevelを要求できる
    (ロードマップStep 3)。未指定ならfinest(既存Step 2の挙動と同じ)。
    """
    try:
        meta = _get_or_build_spatial_voxel_cache(space_id, request.args.get("zoom_level"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(meta)


@app.route("/api/local-spaces/<space_id>/spatial-voxels/positions.bin", methods=["GET"])
def get_spatial_voxels_positions(space_id):
    """voxel_centerをFloat32(x,y,z)で連結した生バイナリを返す(meta.voxel_count
    *3 個のfloat32、little-endian)。Viewer側はArrayBufferとして取得し、
    Float32Arrayに変換して使う。?zoom_level=<int> はmetaと同じ。"""
    try:
        meta = _get_or_build_spatial_voxel_cache(space_id, request.args.get("zoom_level"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return send_file(
        spatial_voxel_cache_repo.positions_path(space_id, meta["zoom_level"]),
        mimetype="application/octet-stream",
    )


# ============================================================
# Visualization / Coloring Strategy(ロードマップStep 4)
# ============================================================

def _get_or_build_voxel_colors(space_id: str, zoom_level_raw=None, mode_raw=None) -> dict:
    """space_id・zoom_level・VisualizationModeのcolor_codeキャッシュ(meta+
    バイナリ)を返す。無ければvoxel_color_strategyで構築してキャッシュする。

    - positions.binと同じinstance順序を保証するため、position cache
      (_get_or_build_spatial_voxel_cache、既存)が保存したids.bin
      (compact int32のLocal Spatial ID一覧)をそのまま読み、同じ
      `ordered_ids`でcolor_codeを計算する(ユーザー指示: 2026-08-31。
      voxel_centerの座標値からworld_points_to_spatial_ids()でIDを
      再構成する、という「座標→ID復元」の往復依存を廃止した。positions.bin・
      colors.binは、SpatialVoxelCacheRepository.save()が1回のsave呼び出しで
      並列に書き出した同一のordered配列に、常に遡って対応する)。
    - position cacheのorder_fingerprintをcolor cacheのmetaにも複製し
      (`position_order_fingerprint`)、両キャッシュが同じinstance順序から
      生成されたことを、座標やIDを再計算せず値の突き合わせだけで検証
      できるようにする。
    - STRUCTURAL_LABELモードでは、finestのSpatialVoxelLabelRepository
      (このspace_id専用ファイル)を(space_id, local_spatial_id)でjoinする。
      上位levelはparent_local_spatial_id()でderived aggregationするだけで、
      新規に永続化しない(結果のcolor_codeだけをキャッシュする)。
    """
    tokutei_code, space_def = _load_space_def_for_space_id(space_id)
    finest = finest_zoom_level(space_def)
    zoom_level = _resolve_requested_zoom_level(space_def, zoom_level_raw, finest)

    mode_value = (mode_raw or VisualizationMode.DEFAULT.value).upper()
    try:
        mode = VisualizationMode(mode_value)
    except ValueError:
        raise ValueError(
            f"未対応のmodeです: {mode_value!r}(有効な値: {[m.value for m in VisualizationMode]})"
        )

    cached = voxel_color_cache_repo.load_meta(space_id, zoom_level, mode.value)
    if cached is not None:
        return cached

    # positions.binと同じ順序を得るため、position cacheを確保してから
    # そのids.bin(compact int32のLocal Spatial ID一覧)をそのまま読む
    # (座標からのID再構成はしない。instance順序保証の根拠。テストで検証済み)。
    position_meta = _get_or_build_spatial_voxel_cache(space_id, str(zoom_level))
    ordered_ids = spatial_voxel_cache_repo.load_local_spatial_ids(space_id, zoom_level)

    finest_labels = None
    if mode == VisualizationMode.STRUCTURAL_LABEL:
        finest_labels = voxel_label_repo.load_all(space_id)

    codes, _tallies = build_color_codes_for_mode(
        space_id, space_def, zoom_level, finest, ordered_ids, mode, finest_labels=finest_labels,
    )
    legend = build_legend()
    return voxel_color_cache_repo.save(
        space_id, zoom_level, mode.value, codes, legend,
        position_order_fingerprint=position_meta.get("order_fingerprint"),
    )


@app.route("/api/local-spaces/<space_id>/spatial-voxels/colors", methods=["GET"])
def get_spatial_voxel_colors_meta(space_id):
    """指定VisualizationMode(既定DEFAULT)のcolor_code meta(legend含む)を
    返す。?zoom_level=<int>・?mode=<DEFAULT|STRUCTURAL_LABEL> クエリ
    パラメータに対応する。"""
    try:
        meta = _get_or_build_voxel_colors(space_id, request.args.get("zoom_level"), request.args.get("mode"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(meta)


@app.route("/api/local-spaces/<space_id>/spatial-voxels/colors.bin", methods=["GET"])
def get_spatial_voxel_colors_codes(space_id):
    """color_codeをuint8で連結した生バイナリを返す(meta.voxel_count個、
    positions.binと同じinstance順序)。Viewer側はlegendでcode->RGBへ変換する。"""
    try:
        meta = _get_or_build_voxel_colors(space_id, request.args.get("zoom_level"), request.args.get("mode"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return send_file(
        voxel_color_cache_repo.codes_path(space_id, meta["zoom_level"], meta["mode"]),
        mimetype="application/octet-stream",
    )


if __name__ == "__main__":
    print(f"フロント配信元: {BASE_DIR}")
    print(f"データ保存先  : {DATA_DIR}")
    app.run(host="127.0.0.1", port=8000, debug=True)
