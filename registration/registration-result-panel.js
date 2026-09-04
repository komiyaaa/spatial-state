/**
 * registration/registration-result-panel.js
 *
 * 「Registration Result」画面(2026-09-02新規、2026-09-02検証機能拡張)。
 * backend/data/registration_results/{space_id}/{run_id}_{source_stem}/ に
 * 保存されたVGICP精密位置合わせ結果(precise_registered.ply、Spatial State
 * 更新に実際に投入された点群そのもの)を一覧・プレビューする。
 *
 * Registration(rigid-transform.js)・VGICP・Spatial State更新のロジックには
 * 一切触れない。既存API(GET /api/registration-results/<space_id>、
 * GET /api/registration-results/<space_id>/<run_dir>/<filename>、いずれも
 * backend/server.pyの既存パイプラインの出力をコピーして返すだけ)を読むだけの、
 * 表示専用モジュール。新規APIは追加していない。
 *
 * 点群の読み込みはregistration/pointcloud-io.jsのloadPointCloudFromURL()・
 * loadBaseMapManifest()を再利用する(パーサ・Base Map取得経路の重複を
 * 避けるため。registration-controller.jsのTarget読み込みと同じ関数)。
 * 表示座標変換もshared/display-coordinates.jsのtoDisplayCoordinatesのみを
 * 使い、新しい変換ロジックは追加しない。
 *
 * 【Base Map候補のspace_idスコープ(2026-09-02修正)】
 * Base Map候補は、現在開いているLocal Spaceのspace_idに厳密一致する
 * manifestエントリ(無ければ、このLocal Space自身のtokutei_codeへの
 * legacy fallback)だけを対象にする(resolveBaseMapEntryForSpace参照。
 * backend/server.pyの_find_base_map_path()と同じ規則)。他Local Space・
 * 他BuildingのBase Mapは候補にも出さない。0件の場合は「Base Mapなし」と
 * 明示し、他spaceのものへはfallbackしない。解決はrefresh()時に1回だけ
 * 行い、チェックボックスのクリックハンドラは解決済みの結果を使うだけにする
 * (クリック毎に再解決しない、solitaryな1候補のみを常に保証するため)。
 *
 * 【検証機能拡張(2026-09-02)の設計方針】
 * - Base Map / 個別Precise / 全Precise(Show All)は、いずれも「保存済みの
 *   点群をそのまま表示するだけ」であり、ここで再位置合わせ・再変換は
 *   一切行わない(rigid-transform.js等は使わない)。
 * - 複数の点群を同時に表示できるよう、ビューワを「名前付きレイヤー」管理に
 *   拡張した(createSimpleViewer参照)。base_map / precise_single /
 *   all_precise/{run_dir} の3種類。
 * - Show All(全run重ね表示)は、OFFにした瞬間・space_id切り替え時に
 *   必ずThree.jsのgeometry/materialをdispose()する(大量点群のメモリ解放)。
 *   Base Mapは比較的高コストな読み込みのため、OFF操作では非表示にする
 *   だけに留め、space_id切り替え時にのみdisposeする。
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { loadPointCloudFromURL, loadBaseMapManifest } from "./pointcloud-io.js";
import { toDisplayCoordinates } from "../shared/display-coordinates.js";
import { VIEWER_BACKGROUND_COLOR } from "../shared/viewer-theme.js";

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function fmtNumber(n, digits = 6) {
  return typeof n === "number" ? n.toFixed(digits) : "―";
}

function hex6(n) {
  return `#${n.toString(16).padStart(6, "0")}`;
}

function basename(p) {
  if (!p) return "";
  return String(p).replace(/\\/g, "/").split("/").pop();
}

// POST /api/registration-results/<space_id>/<run_dir>/export-global の
// error_codeを、ユーザーが原因を判断できる短い日本語文へ対応させる
// (このモジュールは表示専用のため、エラー分類自体は増やさずbackendの
// error_code値をそのまま鍵にする)。
const EXPORT_ERROR_MESSAGES = {
  NO_ANCHOR: "このLocal SpaceはまだGlobal座標に接続されていません(Nodal InformationでLocal↔GLOBAL接続の作成が必要です)",
  BLOCKED_BY_LOCAL_CONFLICT: "Local↔Local配置がCONFLICTのため、Global解決が行われていません",
  ANCHOR_UNRESOLVABLE: "Global接続はありますが、座標を解決できませんでした",
  ANCHOR_INSUFFICIENT: "Global接続の対応点が不足しており、向き(yaw)を確定できません(2点以上が必要です)",
  GLOBAL_CONFLICT: "複数のGlobal接続が矛盾しているため、Global座標を確定できません",
  PRECISE_REGISTERED_NOT_FOUND: "このrunのprecise_registered.plyが見つかりません",
  LOCAL_SPACE_NOT_FOUND: "このLocal Spaceが見つかりません",
  SPACE_DEFINITION_NOT_FOUND: "このLocal SpaceのCoordinateDefinitionが見つかりません",
  NO_RESOLUTION_RESULT: "Spatial Resolutionがまだ一度も実行されていません(Nodal Information画面で実行してください)",
  SPACE_NOT_IN_ANY_COMPONENT: "このLocal SpaceはどのNodal Connectionにも属していません",
  GLOBAL_TRANSFORM_UNAVAILABLE: "Global変換を適用できませんでした",
  EXPORT_FAILED: "Exportに失敗しました",
};

function exportErrorMessage(errorCode, fallback) {
  if (errorCode && EXPORT_ERROR_MESSAGES[errorCode]) {
    return `${EXPORT_ERROR_MESSAGES[errorCode]}(${errorCode})`;
  }
  return errorCode ? `${fallback || "Exportに失敗しました"}(${errorCode})` : (fallback || "Exportに失敗しました");
}

/**
 * manifestのエントリから、指定したspace_idに属するものだけを厳密一致で
 * 解決する(backend/server.pyの_find_base_map_path()、
 * registration-controller.jsのresolveBaseMapEntryForSpace()と同じ規則)。
 * 正式な永続化キーはspace_id完全一致。tokutei_code単独キーは、既存データ
 * 互換のためのread-onlyなlegacy fallbackとしてのみ使う。
 * substring/prefix/includesによる曖昧一致は行わない。他Local Space・
 * 他BuildingのBase Mapが候補に混ざることはない。
 */
function resolveBaseMapEntryForSpace(manifest, spaceId) {
  if (!spaceId || !manifest) return null;
  const exact = manifest.find((m) => m.id === spaceId);
  if (exact) return exact;
  const tokuteiCode = spaceId.includes("-") ? spaceId.slice(spaceId.lastIndexOf("-") + 1) : spaceId;
  return manifest.find((m) => m.id === tokuteiCode) ?? null;
}

// Show All時にrunごとを見分けやすくするための固定パレット(既知の
// 「隣り合っても混同しにくい」24色セットから抜粋)。run数がこれを超えたら
// 先頭から繰り返す(実装負荷を抑えるための単純な割り当て)。
const RUN_COLOR_PALETTE = [
  0xe6194b, 0x3cb44b, 0x4363d8, 0xf58231, 0x911eb4,
  0x46f0f0, 0xf032e6, 0xbcf60c, 0xfabebe, 0x008080,
  0xe6beff, 0x9a6324, 0x800000, 0x808000, 0x000075,
];

/**
 * @param {HTMLElement} container
 * @param {() => (string|null)} getSpaceId 現在開いているLocal Spaceのspace_idを
 *   都度読みに行くgetter(呼び出し側がLocal Space切り替え時にrefresh()を呼ぶ想定)。
 * @returns {{refresh: () => Promise<void>}}
 */
export function initRegistrationResultPanel(container, getSpaceId) {
  container.innerHTML = `
    <div class="regresult-layout">
      <div class="regresult-list-wrap">
        <div class="regresult-list-label">保存済みRegistration Result(このLocal Space専用)</div>
        <div class="regresult-list" id="regResultList"></div>
      </div>
      <div class="regresult-preview">
        <div class="regresult-toggles">
          <label><input type="checkbox" id="regResultShowBaseMap"> Base Map表示</label>
          <span class="regresult-basemap-status" id="regResultBaseMapStatus"></span>
          <label><input type="checkbox" id="regResultShowPrecise"> Precise Registered表示</label>
          <label><input type="checkbox" id="regResultShowAll"> Show All Precise Results</label>
          <span class="regresult-progress" id="regResultProgress"></span>
        </div>
        <div class="regresult-meta" id="regResultMeta">左の一覧からrunを選択してください</div>
        <div class="regresult-all-legend" id="regResultAllLegend" style="display:none;"></div>
        <div class="regresult-canvas-holder" id="regResultCanvas"></div>
      </div>
    </div>
  `;
  injectStyles();

  const viewer = createSimpleViewer(document.getElementById("regResultCanvas"));
  const showBaseMapEl = document.getElementById("regResultShowBaseMap");
  const baseMapStatusEl = document.getElementById("regResultBaseMapStatus");
  const showPreciseEl = document.getElementById("regResultShowPrecise");
  const showAllEl = document.getElementById("regResultShowAll");
  const progressEl = document.getElementById("regResultProgress");
  const legendEl = document.getElementById("regResultAllLegend");
  const metaEl = document.getElementById("regResultMeta");

  let currentResults = [];
  let currentSpaceId = null;
  let selectedRunDir = null;
  const exportState = new Map(); // run_dir -> {status: "loading"|"success"|"error", ...} (このpanelインスタンス内のみの表示状態)
  let baseMapLoaded = false; // 現在のspace_idについて既にBase Mapを読み込み済みか
  let resolvedBaseMapEntry = null; // 現在のspace_idに属するBase Map候補(0件ならnull、他spaceのものは入らない)
  let baseMapResolutionGeneration = 0; // Base Map候補解決の競合(space切替中の取得)を無効化する世代カウンタ
  let showAllGeneration = 0; // Show Allの多重起動(ON連打・space切替)を無効化するための世代カウンタ

  /** space_id切り替え・タブ再訪問のたびに呼ばれる。表示中の全レイヤーを
   * 破棄し、トグル状態も初期化してから最新の一覧を取得し直す。 */
  async function refresh() {
    currentSpaceId = getSpaceId();
    showAllGeneration++; // 実行中のShow Allループがあれば、ここで無効化する
    selectedRunDir = null;
    exportState.clear(); // space切り替え時は、前のspaceのexport状態表示を持ち越さない
    baseMapLoaded = false;
    viewer.clearAllLayers(); // space切り替え時は、Base Mapも含め確実に全てdisposeする
    showBaseMapEl.checked = false;
    showBaseMapEl.disabled = true; // 候補解決が終わるまでは、fail-closedでチェック不可にする
    resolvedBaseMapEntry = null;
    showPreciseEl.checked = false;
    showPreciseEl.disabled = false;
    showAllEl.checked = false;
    progressEl.textContent = "";
    legendEl.style.display = "none";
    legendEl.innerHTML = "";
    metaEl.textContent = "左の一覧からrunを選択してください";

    // Base Map候補(現在のspace_idに属するものだけ、0件または1件)を解決する。
    // substring/prefix/includesによる曖昧一致はせず、他space_idの候補は
    // 一切表示・選択できないようにする(結果一覧取得とは独立に行う)。
    const myBaseMapGen = ++baseMapResolutionGeneration;
    if (currentSpaceId) {
      baseMapStatusEl.textContent = "";
      loadBaseMapManifest()
        .then((manifest) => {
          if (myBaseMapGen !== baseMapResolutionGeneration) return; // 別spaceへ切り替わっていたら破棄
          resolvedBaseMapEntry = resolveBaseMapEntryForSpace(manifest, currentSpaceId);
          showBaseMapEl.disabled = !resolvedBaseMapEntry;
          baseMapStatusEl.textContent = resolvedBaseMapEntry ? "" : "(Base Mapなし)";
        })
        .catch((err) => {
          if (myBaseMapGen !== baseMapResolutionGeneration) return;
          baseMapStatusEl.textContent = `(Base Map一覧の取得に失敗: ${err.message})`;
        });
    } else {
      baseMapStatusEl.textContent = "(Base Mapなし)";
    }

    const listEl = document.getElementById("regResultList");
    if (!currentSpaceId) {
      listEl.innerHTML = `<div class="regresult-empty">Local Spaceが選択されていません</div>`;
      currentResults = [];
      return;
    }

    listEl.innerHTML = `<div class="regresult-empty">読み込み中...</div>`;
    try {
      const res = await fetch(`/api/registration-results/${encodeURIComponent(currentSpaceId)}`);
      if (!res.ok) throw new Error(`一覧の取得に失敗しました(status=${res.status})`);
      const data = await res.json();
      currentResults = data.results || [];
      renderList();
    } catch (err) {
      currentResults = [];
      listEl.innerHTML = `<div class="regresult-empty">取得に失敗しました: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderList() {
    const listEl = document.getElementById("regResultList");
    if (currentResults.length === 0) {
      listEl.innerHTML = `<div class="regresult-empty">このLocal Spaceにはまだ保存済みのRegistration Resultがありません(「追加モード」で採用すると、ここに追加されます)</div>`;
      return;
    }
    listEl.innerHTML = currentResults.map((r) => `
      <div class="regresult-row" data-run-dir="${escapeHtml(r.run_dir)}">
        <div class="regresult-row-title">
          ${escapeHtml(r.source_filename || r.uploaded_filename || r.run_dir)}
          ${r.migrated_from_legacy ? '<span class="regresult-legacy-badge">migrated</span>' : ""}
        </div>
        <div class="regresult-row-sub">fitness: ${r.fitness_score != null ? r.fitness_score.toExponential(3) : "―"} / voxel_size: ${r.voxel_size ?? "―"}m</div>
        <div class="regresult-row-time">${escapeHtml(r.generated_at || "")}</div>
        <div class="regresult-export-row">
          <button type="button" class="regresult-export-btn" data-run-dir="${escapeHtml(r.run_dir)}">Export Global PLY</button>
          <span class="regresult-export-status" data-run-dir="${escapeHtml(r.run_dir)}">${exportStatusHtml(r.run_dir)}</span>
        </div>
      </div>
    `).join("");
    listEl.querySelectorAll(".regresult-row").forEach((row) => {
      row.addEventListener("click", () => selectRun(row.dataset.runDir));
    });
    listEl.querySelectorAll(".regresult-export-btn").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation(); // row選択(selectRun)を誘発しない
        handleExportClick(btn.dataset.runDir);
      });
    });
    applyExportButtonState();
  }

  /** exportState(このpanelインスタンス内の表示専用状態)に基づく、1行分の
   * export状態表示HTML。exportStateに何も無ければ、一覧取得時点の
   * has_precise_global_ply(既存precise_registered_global.plyの有無)を
   * 「Global Export済み」バッジとして表示する。 */
  function exportStatusHtml(runDir) {
    const state = exportState.get(runDir);
    if (state?.status === "loading") {
      return `<span class="regresult-export-msg regresult-export-loading">Exportしています...</span>`;
    }
    if (state?.status === "success") {
      return `<span class="regresult-export-msg regresult-export-success">Global Export済み(EPSG:${escapeHtml(String(state.targetEpsg ?? "―"))} / ${escapeHtml(basename(state.outputArtifact))})</span>`;
    }
    if (state?.status === "error") {
      return `<span class="regresult-export-msg regresult-export-error">${escapeHtml(state.message)}</span>`;
    }
    const result = currentResults.find((r) => r.run_dir === runDir);
    if (result?.has_precise_global_ply) {
      return `<span class="regresult-export-msg regresult-export-done-badge">Global Export済み</span>`;
    }
    return "";
  }

  /** loading中はボタンをdisabledにし、それ以外は常に押せる状態に戻す
   * (成功後の再export、失敗後の再試行を妨げないため)。 */
  function applyExportButtonState() {
    const listEl = document.getElementById("regResultList");
    listEl.querySelectorAll(".regresult-export-btn").forEach((btn) => {
      const state = exportState.get(btn.dataset.runDir);
      const loading = state?.status === "loading";
      btn.disabled = loading;
      btn.textContent = loading ? "Export中..." : "Export Global PLY";
    });
  }

  /** 個別rowのボタン/状態表示だけをDOM更新する(一覧全体を再描画すると
   * 選択中rowのハイライト等が失われるため)。 */
  function updateExportRowUI(runDir) {
    const btn = document.querySelector(`.regresult-export-btn[data-run-dir="${CSS.escape(runDir)}"]`);
    const statusEl = document.querySelector(`.regresult-export-status[data-run-dir="${CSS.escape(runDir)}"]`);
    if (btn) {
      const state = exportState.get(runDir);
      const loading = state?.status === "loading";
      btn.disabled = loading;
      btn.textContent = loading ? "Export中..." : "Export Global PLY";
    }
    if (statusEl) statusEl.innerHTML = exportStatusHtml(runDir);
  }

  /** 「Export Global PLY」ボタンのクリック処理。既存の
   * POST /api/registration-results/<space_id>/<run_dir>/export-global を
   * そのまま呼ぶだけで、Spatial Resolutionの自動実行やCoordinateDefinition/
   * Nodal Informationの変更、precise_registered.plyの書き換えは一切行わない
   * (このボタンはexport結果を要求するだけ、それらは全てbackend側の既存
   * fail-closed実装に委ねる)。 */
  async function handleExportClick(runDir) {
    if (exportState.get(runDir)?.status === "loading") return;
    exportState.set(runDir, { status: "loading" });
    updateExportRowUI(runDir);
    try {
      const res = await fetch(
        `/api/registration-results/${encodeURIComponent(currentSpaceId)}/${encodeURIComponent(runDir)}/export-global`,
        { method: "POST" },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        exportState.set(runDir, {
          status: "error",
          message: exportErrorMessage(data.error_code, data.error),
        });
      } else {
        exportState.set(runDir, {
          status: "success",
          targetEpsg: data.target_epsg,
          outputArtifact: data.output_artifact,
        });
        const result = currentResults.find((r) => r.run_dir === runDir);
        if (result) result.has_precise_global_ply = true; // 再fetchせずとも次回描画でバッジ表示できるようにする
      }
    } catch (err) {
      exportState.set(runDir, { status: "error", message: `通信に失敗しました: ${err.message}` });
    }
    updateExportRowUI(runDir);
  }

  async function selectRun(runDir) {
    selectedRunDir = runDir;
    container.querySelectorAll(".regresult-row").forEach((r) => r.classList.remove("regresult-row-active"));
    const row = container.querySelector(`.regresult-row[data-run-dir="${CSS.escape(runDir)}"]`);
    if (row) row.classList.add("regresult-row-active");

    const result = currentResults.find((r) => r.run_dir === runDir);
    metaEl.textContent = "precise_registered.ply を読み込み中...";

    try {
      const url = `/api/registration-results/${encodeURIComponent(currentSpaceId)}/${encodeURIComponent(runDir)}/precise_registered.ply`;
      const { positions, colors } = await loadPointCloudFromURL(url);
      // Show All中は個別Precise表示と二重に描画しないよう、レイヤー自体は
      // 作るがチェックボックスの状態に従って可視性を決める(下のsync参照)。
      viewer.setLayerPoints("precise_single", positions, colors, { defaultColor: 0x4a7c74 });
      showPreciseEl.checked = !showAllEl.checked;
      syncLayerVisibility();
      viewer.fitCameraToVisibleLayers();
      metaEl.innerHTML = `
        <b>${escapeHtml(result?.source_filename || result?.uploaded_filename || runDir)}</b>
        (${(positions.length / 3).toLocaleString()}点)
        ${result?.migrated_from_legacy ? '<span class="regresult-legacy-badge">migrated(旧データからの移行、rotation/translation未記録)</span>' : ""}<br>
        run_id: ${escapeHtml(result?.run_id ?? "―")}<br>
        fitness_score: ${result?.fitness_score ?? "―"} / voxel_size: ${result?.voxel_size ?? "―"}m<br>
        rotation: ${result?.rotation ? `[${result.rotation.map((row2) => row2.map((v) => fmtNumber(v, 4)).join(", ")).join(" / ")}]` : "未記録"}<br>
        translation: ${result?.translation ? `[${result.translation.map((v) => fmtNumber(v, 4)).join(", ")}]` : "未記録"}<br>
        生成日時: ${escapeHtml(result?.generated_at || "―")}
      `;
    } catch (err) {
      metaEl.textContent = `precise_registered.ply の読み込みに失敗しました: ${err.message}`;
    }
  }

  function syncLayerVisibility() {
    viewer.setLayerVisible("base_map", showBaseMapEl.checked);
    viewer.setLayerVisible("precise_single", showPreciseEl.checked && !!selectedRunDir);
  }

  // --- Base Map ON/OFF ---
  // 候補の解決(space_idスコープの厳密一致)はrefresh()側で既に完了しており、
  // ここでは resolvedBaseMapEntry を使って読み込むだけ(loadPointCloudFromURLは
  // registration-controller.jsのTarget読み込みと同一関数を再利用)。
  showBaseMapEl.addEventListener("change", async () => {
    if (!showBaseMapEl.checked) {
      syncLayerVisibility();
      viewer.fitCameraToVisibleLayers();
      return;
    }
    if (baseMapLoaded) {
      syncLayerVisibility();
      viewer.fitCameraToVisibleLayers();
      return;
    }
    const spaceIdAtRequest = currentSpaceId;
    const entry = resolvedBaseMapEntry; // refresh()時点で、このspace_idに属するものだけを解決済み
    if (!entry) {
      showBaseMapEl.checked = false;
      metaEl.textContent = "このLocal SpaceにはBase Mapがありません";
      return;
    }
    metaEl.textContent = "Base Mapを読み込み中...(実データでは数十秒かかる場合があります)";
    try {
      const { positions, colors } = await loadPointCloudFromURL(`./base_maps/${entry.file}`);
      if (spaceIdAtRequest !== currentSpaceId) return; // 読み込み中にspaceが切り替わっていたら破棄する
      viewer.setLayerPoints("base_map", positions, colors, { defaultColor: 0x8fa6b8, size: 0.015 });
      baseMapLoaded = true;
      syncLayerVisibility();
      viewer.fitCameraToVisibleLayers();
      metaEl.textContent = selectedRunDir ? metaEl.textContent : "Base Mapを表示しています。左の一覧からrunを選択するとPreciseと重ねて比較できます";
    } catch (err) {
      showBaseMapEl.checked = false;
      metaEl.textContent = `Base Mapの読み込みに失敗しました: ${err.message}`;
    }
  });

  // --- 個別Precise ON/OFF ---
  showPreciseEl.addEventListener("change", () => {
    syncLayerVisibility();
    viewer.fitCameraToVisibleLayers();
  });

  // --- Show All Precise Results ---
  showAllEl.addEventListener("change", async () => {
    if (!showAllEl.checked) {
      showAllGeneration++; // 実行中の読み込みループがあれば打ち切る
      viewer.clearLayersWithPrefix("all_precise/"); // OFF時に確実にdispose
      legendEl.style.display = "none";
      legendEl.innerHTML = "";
      progressEl.textContent = "";
      showPreciseEl.disabled = false;
      syncLayerVisibility();
      viewer.fitCameraToVisibleLayers();
      return;
    }

    // Show All中は個別Preciseと二重表示にならないよう隠す(disposeはしない、
    // OFFに戻せばすぐ復帰できるようにするため)。
    showPreciseEl.disabled = true;
    syncLayerVisibility();

    if (currentResults.length === 0) {
      metaEl.textContent = "表示できるRegistration Resultがありません";
      showAllEl.checked = false;
      showPreciseEl.disabled = false;
      return;
    }

    const myGeneration = ++showAllGeneration;
    const spaceIdAtRequest = currentSpaceId;
    const total = currentResults.length;
    let doneCount = 0;
    legendEl.innerHTML = "";
    legendEl.style.display = "flex";

    for (let i = 0; i < currentResults.length; i++) {
      if (myGeneration !== showAllGeneration || spaceIdAtRequest !== currentSpaceId) return; // OFF/space切替で打ち切り
      const r = currentResults[i];
      progressEl.textContent = `Show All読み込み中... (${doneCount}/${total})`;
      const color = RUN_COLOR_PALETTE[i % RUN_COLOR_PALETTE.length];
      try {
        const url = `/api/registration-results/${encodeURIComponent(currentSpaceId)}/${encodeURIComponent(r.run_dir)}/precise_registered.ply`;
        const { positions } = await loadPointCloudFromURL(url);
        if (myGeneration !== showAllGeneration || spaceIdAtRequest !== currentSpaceId) return;
        viewer.setLayerPoints(`all_precise/${r.run_dir}`, positions, null, { flatColor: color, size: 0.015 });
        const label = r.source_filename || r.uploaded_filename || r.run_dir;
        legendEl.insertAdjacentHTML("beforeend", `
          <span class="regresult-legend-item">
            <span class="regresult-legend-swatch" style="background:${hex6(color)}"></span>${escapeHtml(label)}
          </span>
        `);
      } catch (err) {
        console.warn("[registration-result-panel] Show All: 読み込みに失敗しました", r.run_dir, err);
      }
      doneCount++;
    }

    if (myGeneration !== showAllGeneration || spaceIdAtRequest !== currentSpaceId) return;
    progressEl.textContent = `Show All: ${doneCount}/${total}件を表示中`;
    viewer.fitCameraToVisibleLayers();
  });

  return { refresh };
}

/**
 * 名前付きレイヤーで複数の点群を同時に表示できる、表示専用(pick/marker/
 * overlay無し)の最小Three.jsビューワ。registration-controller.js の
 * createViewer() と同じ考え方(raw→display変換にshared/display-coordinates.js
 * のtoDisplayCoordinatesのみを使う、新しい変換ロジックは作らない)。
 */
function createSimpleViewer(holder) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(VIEWER_BACKGROUND_COLOR);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 5000);
  camera.position.set(3, 3, 3);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  holder.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  scene.add(new THREE.AmbientLight(0xffffff, 0.9));
  const dir = new THREE.DirectionalLight(0xffffff, 0.5);
  dir.position.set(5, 10, 5);
  scene.add(dir);

  const layers = new Map(); // name -> THREE.Points

  function resize() {
    const w = holder.clientWidth, h = holder.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  requestAnimationFrame(() => requestAnimationFrame(resize));

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  function toDisplayBuffer(rawPositions) {
    const n = rawPositions.length / 3;
    const out = new Float32Array(rawPositions.length);
    for (let i = 0; i < n; i++) {
      const [dx, dy, dz] = toDisplayCoordinates(rawPositions[i * 3], rawPositions[i * 3 + 1], rawPositions[i * 3 + 2]);
      out[i * 3] = dx; out[i * 3 + 1] = dy; out[i * 3 + 2] = dz;
    }
    return out;
  }

  /**
   * @param {string} name レイヤー名(同名が既にあれば入れ替える=disposeしてから作り直す)
   * @param {Float32Array} positions raw座標(サーバー保存済みの点群そのもの、再変換しない)
   * @param {Float32Array|null} colors 頂点カラー(無ければflatColor/defaultColorを使う)
   * @param {{flatColor?: number, defaultColor?: number, size?: number, visible?: boolean}} [options]
   */
  function setLayerPoints(name, positions, colors, options = {}) {
    clearLayer(name);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(toDisplayBuffer(positions), 3));
    let mat;
    if (options.flatColor != null) {
      // Show All時: runごとに識別しやすいよう、元の頂点カラーより優先して単色で塗る。
      mat = new THREE.PointsMaterial({ size: options.size ?? 0.02, color: options.flatColor });
    } else if (colors) {
      geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      mat = new THREE.PointsMaterial({ size: options.size ?? 0.02, vertexColors: true });
    } else {
      mat = new THREE.PointsMaterial({ size: options.size ?? 0.02, color: options.defaultColor ?? 0x4a7c74 });
    }
    const points = new THREE.Points(geo, mat);
    points.visible = options.visible !== false;
    scene.add(points);
    layers.set(name, points);
    return points;
  }

  function clearLayer(name) {
    const obj = layers.get(name);
    if (!obj) return;
    scene.remove(obj);
    obj.geometry.dispose();
    obj.material.dispose();
    layers.delete(name);
  }

  function clearLayersWithPrefix(prefix) {
    for (const name of Array.from(layers.keys())) {
      if (name.startsWith(prefix)) clearLayer(name);
    }
  }

  function clearAllLayers() {
    for (const name of Array.from(layers.keys())) clearLayer(name);
  }

  function setLayerVisible(name, visible) {
    const obj = layers.get(name);
    if (obj) obj.visible = visible;
  }

  /** 現在visible=trueな全レイヤーの合成バウンディングボックスにカメラを合わせる。
   * 個々のsetLayerPoints呼び出しの都度ではなく、呼び出し側が「このタイミングで
   * フィットしたい」時に明示的に呼ぶ(Show Allの逐次読み込み中に視点が
   * 毎回飛ぶのを防ぐため)。 */
  function fitCameraToVisibleLayers() {
    const box = new THREE.Box3();
    let any = false;
    for (const obj of layers.values()) {
      if (!obj.visible) continue;
      obj.geometry.computeBoundingBox();
      if (obj.geometry.boundingBox) { box.union(obj.geometry.boundingBox); any = true; }
    }
    if (!any) return;
    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    box.getCenter(center);
    box.getSize(size);
    const r = Math.max(size.length() / 2, 0.5);
    camera.position.set(center.x + r, center.y + r, center.z + r);
    controls.target.copy(center);
    camera.near = Math.max(0.01, r * 0.005);
    camera.far = r * 20;
    camera.updateProjectionMatrix();
    controls.update();
    resize();
  }

  return { setLayerPoints, clearLayer, clearLayersWithPrefix, clearAllLayers, setLayerVisible, fitCameraToVisibleLayers };
}

function injectStyles() {
  if (document.getElementById("regresult-styles")) return;
  const style = document.createElement("style");
  style.id = "regresult-styles";
  style.textContent = `
    .regresult-layout { display: flex; flex: 1; min-height: 0; }
    .regresult-list-wrap { width: 320px; flex-shrink: 0; border-right: 1px solid var(--border); background: var(--bg); padding: 12px; overflow-y: auto; }
    .regresult-list-label { font-size: 11px; color: var(--text-faint); font-weight: 700; letter-spacing: 0.03em; margin-bottom: 8px; }
    .regresult-list { display: flex; flex-direction: column; gap: 6px; }
    .regresult-empty { font-size: 12px; color: var(--text-faint); padding: 8px 2px; }
    .regresult-row {
      border: 1px solid var(--border-soft); border-radius: 6px; padding: 8px 10px; background: var(--panel);
      cursor: pointer; font-size: 12px; transition: border-color 0.12s, background 0.12s;
    }
    .regresult-row:hover { border-color: var(--accent); background: var(--panel-raised); }
    .regresult-row-active { border-color: var(--accent); background: var(--accent-soft); }
    .regresult-row-title { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .regresult-row-sub { color: var(--text-dim); font-size: 11px; margin-top: 2px; }
    .regresult-row-time { color: var(--text-faint); font-size: 10.5px; margin-top: 2px; font-variant-numeric: tabular-nums; }
    .regresult-legacy-badge {
      display: inline-block; margin-left: 6px; font-size: 9.5px; font-weight: 700;
      background: var(--status-warning-soft); color: var(--status-warning); padding: 1px 6px; border-radius: 10px; vertical-align: middle;
    }
    .regresult-export-row { margin-top: 6px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .regresult-export-btn {
      font-size: 11px; padding: 3px 9px; border-radius: 5px; border: 1px solid var(--accent); background: var(--panel);
      color: var(--accent); cursor: pointer; white-space: nowrap; font-weight: 600;
    }
    .regresult-export-btn:hover:not(:disabled) { background: var(--accent-soft); }
    .regresult-export-btn:disabled { opacity: 0.55; cursor: not-allowed; }
    .regresult-export-status { font-size: 10.5px; }
    .regresult-export-msg { display: inline-block; }
    .regresult-export-loading { color: var(--accent-dark); }
    .regresult-export-success { color: var(--status-solved); }
    .regresult-export-error { color: var(--status-error); }
    .regresult-export-done-badge {
      display: inline-block; font-size: 9.5px; font-weight: 700; background: var(--status-solved-soft); color: var(--status-solved);
      padding: 1px 6px; border-radius: 10px;
    }
    .regresult-preview { flex: 1; min-height: 0; display: flex; flex-direction: column; }
    .regresult-toggles {
      display: flex; align-items: center; gap: 16px; padding: 8px 14px; background: var(--panel);
      border-bottom: 1px solid var(--border-soft); font-size: 12px; color: var(--text); flex-wrap: wrap;
    }
    .regresult-toggles label { display: flex; align-items: center; gap: 5px; cursor: pointer; white-space: nowrap; }
    .regresult-toggles label:has(input:disabled) { opacity: 0.5; cursor: not-allowed; }
    .regresult-basemap-status { font-size: 11px; color: var(--text-faint); }
    .regresult-progress { font-size: 11.5px; color: var(--accent-dark); margin-left: auto; }
    .regresult-meta { font-size: 12px; color: var(--text); padding: 10px 14px; background: var(--bg); border-bottom: 1px solid var(--border-soft); line-height: 1.7; }
    .regresult-all-legend {
      display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 8px 14px; background: var(--bg);
      border-bottom: 1px solid var(--border-soft); max-height: 90px; overflow-y: auto;
    }
    .regresult-legend-item { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--text-dim); white-space: nowrap; }
    .regresult-legend-swatch { width: 9px; height: 9px; border-radius: 2px; flex-shrink: 0; box-shadow: inset 0 0 0 1px var(--swatch-outline); }
    .regresult-canvas-holder { flex: 1; min-height: 0; position: relative; }
    .regresult-canvas-holder canvas { display: block; }
  `;
  document.head.appendChild(style);
}
