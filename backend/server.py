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
import os
import threading
import time
from pathlib import Path

from flask import Flask, request, send_from_directory, jsonify

BASE_DIR = Path(__file__).resolve().parent.parent  # ui_proto/ (フロントの置き場所)
DATA_DIR = Path(__file__).resolve().parent / "data"
ROUGH_DIR = DATA_DIR / "rough_registered"
PRECISE_DIR = DATA_DIR / "precise_registered"
SCAN_JSON_DIR = DATA_DIR / "scan_json"
BASE_MAPS_DIR = BASE_DIR / "base_maps"
VGICP_LOG_DIR = DATA_DIR / "vgicp_logs"

for d in (ROUGH_DIR, PRECISE_DIR, SCAN_JSON_DIR, VGICP_LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")

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
    のエントリ(例: id="G002")を探し、そのファイルパスを返す。

    今は「manifest.jsonのidがspace_idの一部に含まれるか」という単純な
    マッチングにしている。LOCAL_SPACEとベースマップの対応関係をきちんと
    管理するようになったら、ここをDBの参照に差し替える。
    """
    manifest_path = BASE_MAPS_DIR / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest:
        if entry.get("id") and entry["id"] in space_id:
            return BASE_MAPS_DIR / entry["file"]
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


def run_vgicp(rough_ply_path: Path, output_path: Path, space_id: str = "unknown") -> Path | None:
    """
    VGICP_jissho_fast.py のアルゴリズムそのもの(複数ボクセルサイズを試し、
    fitness_scoreが最良のものを採用、アーリーストッピング付き)を、
    「1組のtarget・sourceを精密位置合わせする」形に絞って実行する。

    target(ベースマップ)は、space_idからbase_maps/manifest.jsonを引いて特定する。
    見つからない場合は、精密位置合わせをスキップし、ラフレジ結果をそのまま返す
    (パイプライン全体の配線を止めないためのフォールバック)。
    """
    target_path = _find_base_map_path(space_id)
    if target_path is None or not target_path.exists():
        print(f"[run_vgicp] ベースマップが見つかりません(space_id={space_id})。"
              f"ラフレジ結果をそのまま使います。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rough_ply_path.read_bytes())
        return output_path

    try:
        import open3d as o3d
        import numpy as np
        import pygicp
    except ImportError as e:
        print(f"[run_vgicp] 依存ライブラリが未インストールです({e})。"
              f"pip install open3d pygicp を実行してください。ラフレジ結果をそのまま使います。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rough_ply_path.read_bytes())
        return output_path

    target_pcd_full = _import_pcd(target_path)
    source_pcd_full = _import_pcd(rough_ply_path)

    # --- クロップ(sourceのバウンディングボックス+マージンでtargetを絞る) ---
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
        return output_path

    # --- ダウンサンプリング ---
    target_ds = cropped_target.voxel_down_sample(DEFAULT_DS_RESOLUTION)
    source_ds = source_pcd_full.voxel_down_sample(DEFAULT_DS_RESOLUTION)
    target_pts_ds = np.asarray(target_ds.points)
    source_pts_ds = np.asarray(source_ds.points)
    source_pts_full = np.asarray(source_pcd_full.points)

    # --- 複数ボクセルサイズを試し、fitness_scoreが最良のものを採用 ---
    log_path = VGICP_LOG_DIR / f"{space_id}_{rough_ply_path.stem}.txt"
    best_score = float("inf")
    best_points = None
    best_vsize = None
    start = time.perf_counter()

    for vsize in DEFAULT_VOX_SIZES:
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
            continue

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"------------ {vsize} ------------\nfitness_score: {score:.5g}\n")

        if score < best_score:
            best_score = score
            best_points = _transform(source_pts_full, matrix)
            best_vsize = vsize

        if best_score < DEFAULT_EARLY_STOP_THRESHOLD:
            break  # アーリーストッピング(VGICP_jissho_fast.pyと同じ)

    elapsed = time.perf_counter() - start

    if best_points is None:
        print(f"[run_vgicp] すべてのボクセルサイズで失敗しました。ラフレジ結果をそのまま使います。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rough_ply_path.read_bytes())
        return output_path

    result_pcd = o3d.geometry.PointCloud()
    result_pcd.points = o3d.utility.Vector3dVector(best_points)
    result_pcd.colors = source_pcd_full.colors

    output_path.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(output_path), result_pcd)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"----------------------------\nBest size: {best_vsize} m\n"
                f"Best score: {best_score}\nprocess_time: {elapsed:.5g}s\n")
    print(f"[run_vgicp] 完了: best_vsize={best_vsize} score={best_score:.5g} ({elapsed:.1f}s)")

    return output_path


# ============================================================
# 空間ID格子でのボクセル化(JSON化)
# ============================================================

DEFAULT_ZOOM_LEVEL = 9  # LOCAL_SPACEごとの基準ズームレベル(今は固定値、将来はDB等から取得)


def _find_space_definition(space_id: str) -> dict | None:
    """
    space_id(例: "ichigaya_tamachi-G002")から、対応する座標定義JSON
    (backend/space_definitions/*.json、中の"id"フィールドで照合)を探す。

    照合ルール: space_idの末尾(最後の"-"以降、例: "G002")を取り出し、
    座標定義側の"id"がその文字列で始まっているか(例: "G002v3"は"G002"で
    始まる)を見る。バージョン付きのID("G002v3"等)にも対応するため、
    単純な部分文字列一致ではなく「前方一致」にしている。
    (将来DBの参照に差し替える想定の、暫定的なマッチングルール。)
    """
    space_def_dir = Path(__file__).resolve().parent / "space_definitions"
    if not space_def_dir.exists():
        return None
    tokutei_part = space_id.rsplit("-", 1)[-1]  # 例: "ichigaya_tamachi-G002" -> "G002"

    candidates = []
    for path in space_def_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if data.get("id") and data["id"].startswith(tokutei_part):
            candidates.append((path, data))

    if not candidates:
        return None
    # 複数見つかった場合(例: G002v2, G002v3)は、ファイル名の辞書順で最後
    # (＝バージョン番号が一番大きいもの)を採用する暫定ルール
    candidates.sort(key=lambda pair: pair[0].name)
    return candidates[-1][1]


def _derive_vertical_unit_size(z_extent: float, base_resolution: float = 0.1) -> dict[str, float]:
    """
    鉛直方向のズームレベル別ボクセルサイズを、LocalSpacialIDGenerater.py の
    setting_ID() と同じロジック(一番細かい基準サイズから倍々にしていき、
    実際の範囲を超えた時点の値をズームレベル0の起点にする)で導出する。

    水平方向(space_def["unit-size"])は、既にこのロジックで生成された値が
    メタデータJSONに入っているが、鉛直方向はまだこの分離が無いため、
    bounds から計算した実際のZ範囲を使って、同じ考え方でここに作る
    (spatial_id_design_memo.md §5、2026-08-10決定)。
    """
    sizes = [base_resolution]
    while sizes[-1] <= z_extent:
        sizes.append(sizes[-1] * 2)
    sizes = sorted(sizes, reverse=True)
    return {str(i): s for i, s in enumerate(sizes)}


def _world_to_spatial_ids(points, space_def: dict, zoom_level: int):
    """
    ワールド座標(N, 3)を、space_defの座標定義(origin・回転角)を使って、
    空間ID(z/f/x/y文字列)の配列に変換する。

    元実装(gen_local_spatialid.py の IdGenerator.calc_id)と数学的に
    同一の回転式を、1点ずつのPythonループではなくnumpyでベクトル化して
    適用している(大規模点群での高速化のため)。

    水平(x, y)と鉛直(f)で、別々のボクセルサイズ系列を使う
    (spatial_id_design_memo.md §5 の決定: L・Hを別の等比数列にする)。
    水平は既存のunit-size、鉛直はboundsのZ範囲から自動導出した系列を使う。
    """
    import numpy as np

    origin = np.array(space_def["origin"], dtype=np.float64)
    theta = space_def["rad"]
    voxel_size_xy = space_def["unit-size"][str(zoom_level)]

    bounds = np.array(space_def["bounds"], dtype=np.float64)
    z_extent = float(bounds[:, 2].max() - bounds[:, 2].min())
    vertical_unit_size = _derive_vertical_unit_size(z_extent)
    # 鉛直系列は水平よりズームレベルの段数が少ない場合があるので、
    # 範囲外のズームレベルは最も細かい段(最後の要素)を使う
    max_vertical_zoom = len(vertical_unit_size) - 1
    voxel_size_z = vertical_unit_size[str(min(zoom_level, max_vertical_zoom))]

    rel = points - origin
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    # 元実装(calc_id)と数学的に同一の回転式。local_yの符号に注意
    # (r・cos(θ-φ), r・sin(θ-φ) を直交座標の差分から直接計算した形)
    local_x = rel[:, 0] * cos_t + rel[:, 1] * sin_t
    local_y = rel[:, 0] * sin_t - rel[:, 1] * cos_t
    local_z = rel[:, 2]

    x_idx = np.floor(local_x / voxel_size_xy).astype(int)
    y_idx = np.floor(local_y / voxel_size_xy).astype(int)
    f_idx = np.floor(local_z / voxel_size_z).astype(int)

    return [f"{zoom_level}/{f}/{x}/{y}" for f, x, y in zip(f_idx, x_idx, y_idx)]


def convert_to_scan_json(precise_ply_path: Path, out_dir: Path, space_id: str,
                          zoom_level: int = DEFAULT_ZOOM_LEVEL) -> Path | None:
    """
    精密位置合わせ済みの点群を読み込み、対応するローカル空間の座標定義
    (backend/space_definitions/)を使って空間ID格子でボクセル化し、
    「空間ID → ヒット点数」のJSONとして書き出す。

    座標定義が見つからない場合は、プレースホルダーのJSONにフォールバックする
    (パイプライン全体を止めないため)。
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
        spatial_ids = _world_to_spatial_ids(points, space_def, zoom_level)

    # 空間IDごとのヒット数を集計する(Step1: ヒットした空間ID集合、に対応)
    hits: dict[str, int] = {}
    for sid in spatial_ids:
        hits[sid] = hits.get(sid, 0) + 1

    result = {
        "space_id": space_id,
        "zoom_level": zoom_level,
        "source_ply": str(precise_ply_path),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "point_count": len(points),
        "voxel_count": len(hits),
        "hits": hits,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[convert_to_scan_json] 完了: {len(points):,}点 → {len(hits):,}ボクセル "
          f"(zoom={zoom_level}, voxel_size={space_def['unit-size'][str(zoom_level)]}m)")
    return out_path


# ============================================================
# APIエンドポイント
# ============================================================

@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "local_space_prototype.html")


@app.route("/api/registration-results", methods=["POST"])
def receive_registration_result():
    filename = request.headers.get("X-Filename") or f"source_{int(time.time())}.ply"
    space_id = request.headers.get("X-Space-Id") or "unknown"
    ply_text = request.get_data(as_text=True)

    if not ply_text:
        return jsonify({"error": "本文が空です"}), 400

    # --- Step1: ラフレジ結果を保存する ---
    session_dir = ROUGH_DIR / space_id
    session_dir.mkdir(parents=True, exist_ok=True)
    rough_path = session_dir / filename
    rough_path.write_text(ply_text, encoding="utf-8")
    print(f"[受信] {space_id} / {filename} ({len(ply_text):,} bytes) を保存しました: {rough_path}")

    # --- Step2: VGICP(精密位置合わせ) ---
    precise_dir = PRECISE_DIR / space_id
    precise_path = run_vgicp(rough_path, precise_dir / filename, space_id=space_id)

    # --- Step3: JSON化(更新エンジンへの入力形式) ---
    scan_json_path = None
    if precise_path is not None:
        scan_json_dir = SCAN_JSON_DIR / space_id
        scan_json_path = convert_to_scan_json(precise_path, scan_json_dir, space_id)

    return jsonify({
        "status": "ok",
        "space_id": space_id,
        "rough_registered_path": str(rough_path),
        "precise_registered_path": str(precise_path) if precise_path else None,
        "scan_json_path": str(scan_json_path) if scan_json_path else None,
    })


if __name__ == "__main__":
    print(f"フロント配信元: {BASE_DIR}")
    print(f"データ保存先  : {DATA_DIR}")
    app.run(host="127.0.0.1", port=8000, debug=True)
