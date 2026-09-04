/**
 * registration-controller.js
 *
 * 「追加モード」の画面全体(2つの3Dビューワ、クリックでの点選択、
 * 計算の実行、プレビュー、採用/やり直し)を統括する。
 * 数式(rigid-transform.js)・近傍探索(spatial-hash.js)・PCA(pca.js)・
 * 特徴点精緻化(edge-feature.js)・入出力(pointcloud-io.js)は、
 * いずれもこのファイルには実装を持たず、呼び出すだけにしている。
 *
 * 【保守性の方針】
 * このファイルが唯一「Three.js・DOM・アルゴリズムの3つを同時に知っている」
 * 場所になる。アルゴリズムを差し替えたい場合は rigid-transform.js や
 * edge-feature.js を直接差し替えれば、このファイルは触らずに済む設計。
 *
 * 【Source複数選択・Local Spaceごとの一覧管理(2026-09-02追加)】
 * Source(観測点群)は複数ファイルをまとめて選択し、一覧(未処理/作業中/完了)
 * から1件ずつ選んで処理する。一覧はLocal Spaceごとに独立して保持し
 * (sourceListsBySpaceId、Map<space_id, {items, activeKey}>)、A→B→Aと
 * 空間を切り替えても各空間の一覧・完了状態が保たれる。ファイル本体の
 * positions/colorsは全件キャッシュせず、File参照とstatusだけ保持し、
 * activateSourceListItem()で都度読み直す(既存のloadPointCloud経路を再利用)。
 *
 * 一括自動Registrationにはしない: ファイルを追加しても自動では何も処理せず、
 * 一覧のどれかを人間がクリックして初めてTarget/Source picking以降の
 * 既存フローに入る。「採用して保存」後も次のSourceへは自動遷移しない。
 *
 * 【space_id誤POST対策(2026-09-02修正)】
 * 従来はspaceIdを値として1回だけ受け取っており、タブ初期化後に別の
 * Local Spaceへ切り替えても追従しなかった(初期化時のspace_idにPOSTされ
 * 続ける不具合)。今回、options.spaceIdを関数(getSpaceId)で受け取り、
 * 呼び出し側(local_space_prototype.html)がLocal Space切り替えのたびに
 * refresh()を呼ぶことで追従させる。さらに、「採用して保存」の直前に
 * (a) 現在のgetSpaceId()と(b) 現在activeなSource一覧項目が記録している
 * spaceIdが一致するかを検証し、不一致ならfail-closedで送信そのものを
 * 中止する(UI側の追従漏れがあっても誤ったspace_idへは絶対にPOSTしない、
 * という最後の砦)。加えて、画面には常に「対象Local Space: xxx」を表示する。
 *
 * Registration(rigid-transform.js)・VGICP・Spatial State更新のロジックは
 * 一切変更していない。
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { loadPointCloud, loadPointCloudFromURL, loadBaseMapManifest, exportRegisteredPointCloud, downloadHandler } from "./pointcloud-io.js";
import { buildSpatialHash } from "./spatial-hash.js";
import { identifyFeature, OUTER_RADIUS } from "./edge-feature.js";
import { registerRigidZAxis } from "./rigid-transform.js";
import { toDisplayCoordinates, fromDisplayCoordinates } from "../shared/display-coordinates.js";
import { VIEWER_BACKGROUND_COLOR } from "../shared/viewer-theme.js";

const PHASE = {
  WAITING_FILES: "waiting_files",
  PICKING_TARGET: "picking_target",
  PICKING_SOURCE: "picking_source",
  COMPUTING: "computing",
  PREVIEW: "preview",
};

const PICK_COLORS = [0xf5c518, 0x22c3d6]; // 1番目=黄色, 2番目=水色(target/sourceどちらも共通)

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function fileSizeLabel(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
  return `${(bytes / 1024).toFixed(1)}KB`;
}

function keyForFile(file) {
  return `${file.name}::${file.size}::${file.lastModified}`;
}

/**
 * 追加モードの画面を、与えられたコンテナDOM要素の中に構築する。
 * @param {HTMLElement} container
 * @param {{ outputHandler?: Function, spaceId?: string|(() => string|null) }} options
 *   outputHandlerを渡せば、将来「別サーバーへの送信」に差し替えられる
 *   (既定はブラウザダウンロード)。spaceIdは関数(getter)で渡すことを推奨する
 *   (呼び出し側が現在開いているLocal Spaceを都度返せるようにするため)。
 *   後方互換のため、文字列を渡した場合は「常にその値を返す関数」として扱う。
 * @returns {{ dispose: () => void, refresh: () => void }}
 *   refresh()は、呼び出し側でLocal Spaceが切り替わったタイミングで呼ぶこと。
 *   getSpaceId()の戻り値が前回と変わっていれば、Source一覧・viewer・pick状態を
 *   その新しいLocal Space向けに切り替える。
 */
export function initRegistrationMode(container, options = {}) {
  const outputHandler = options.outputHandler ?? downloadHandler;
  const getSpaceId = typeof options.spaceId === "function" ? options.spaceId : () => options.spaceId ?? null;

  container.innerHTML = `
    <div class="reg-layout">
      <div class="reg-toolbar">
        <div class="reg-file-group">
          <label>Target(ベースマップ)</label>
          <select id="regTargetSelect">
            <option value="">読み込み中...</option>
          </select>
        </div>
        <div class="reg-file-group">
          <label>Source(計測データ、複数選択可)</label>
          <input type="file" id="regSourceFile" accept=".las,.ply,.xyz" multiple>
        </div>
        <div class="reg-space-label" id="regSpaceLabel"></div>
        <div class="reg-status" id="regStatus">ファイルを2つ選択してください</div>
        <div class="reg-progress" id="regProgress" style="display:none;">
          <div class="reg-progress-bar" id="regProgressBar"></div>
        </div>
        <button class="reg-btn" id="regComputeBtn" disabled>ラフレジストレーション実行</button>
      </div>
      <div class="reg-source-list-wrap">
        <div class="reg-source-list-label">Source一覧(この画面で開いているLocal Space専用)</div>
        <div class="reg-source-list" id="regSourceList"></div>
      </div>
      <div class="reg-viewports">
        <div class="reg-viewport">
          <div class="reg-viewport-label">
            <span>Target(クリックで2点選択)</span>
          </div>
          <div class="reg-picks-table" id="regTargetPicks"></div>
          <div class="reg-canvas-holder" id="regTargetCanvas"></div>
        </div>
        <div class="reg-viewport">
          <div class="reg-viewport-label">
            <span>Source(クリックで2点選択)</span>
          </div>
          <div class="reg-picks-table" id="regSourcePicks"></div>
          <div class="reg-canvas-holder" id="regSourceCanvas"></div>
        </div>
      </div>
      <div class="reg-preview-bar" id="regPreviewBar" style="display:none;">
        <span>プレビュー: 変換後のsourceをtargetに重ねて表示しています</span>
        <button class="reg-btn reg-btn-primary" id="regAcceptBtn">採用して保存</button>
        <button class="reg-btn" id="regRetryBtn">やり直す</button>
      </div>
    </div>
  `;
  injectStyles();

  const state = {
    phase: PHASE.WAITING_FILES,
    target: null,   // { positions, colors, outerHash }
    source: null,   // { positions, colors, outerHash }(現在activeなSource一覧項目の内容)
    targetPicks: [], // クリックした生の座標(最大2)
    sourcePicks: [],
    previewPositions: null,
  };

  // Local Spaceごとに独立したSource一覧(space_id -> {items: [...], activeKey}) 。
  // 「作業中」はitem自体のstatusではなく、activeKeyとの一致で都度判定する
  // (離脱すると自動的に「未処理」表示へ戻る。完了は item.status='done' として
  // 永続的に保持する)。
  const sourceListsBySpaceId = new Map();
  let currentTrackedSpaceId = undefined; // 初回syncを必ず発生させるため、nullとは区別する

  // 「採用して保存」を押したitemのstatus。pending: 未処理 / processing: サーバーへ
  // 送信中(まだ結果が返っていない) / done: 成功 / failed: 失敗(再送信可能)。
  // 「作業中(WORKING)」はstatusではなく、従来通りactiveKeyとの一致で導出する
  // (離脱すると自動的に未処理表示へ戻る、既存の挙動を維持)。
  //
  // 【非同期queue化(2026-09-02追加)】
  // 「採用して保存」はPOST /api/registration-results(VGICP・Spatial State更新を
  // 含む重い処理)の完了を待たず、即座にリターンして次のSourceを操作可能にする。
  // 実際の送信はspace_id別の直列queue(sendQueueTailBySpaceId)へ積むだけにし、
  // 同一Local Space宛の送信は必ず発行順に1件ずつ実行されるようにする
  // (Spatial State更新順序を壊さないため。VGICP/Spatial Stateのロジック自体は
  // 無変更、送信タイミングの制御のみ)。
  const sendQueueTailBySpaceId = new Map();

  function getOrCreateSpaceList(spaceId) {
    if (!sourceListsBySpaceId.has(spaceId)) {
      sourceListsBySpaceId.set(spaceId, { items: [], activeKey: null });
    }
    return sourceListsBySpaceId.get(spaceId);
  }
  function currentSpaceList() {
    return getOrCreateSpaceList(currentTrackedSpaceId);
  }

  /** 表示中のLocal Spaceが送信元と同じ場合のみ一覧を再描画する(バックグラウンド
   * 送信の完了時に、別のLocal Spaceを見ている画面のDOMを誤って書き換えないため)。
   * 見ていない間の更新はitem.statusに残るため、そのSpaceへ戻ればsyncToCurrentSpace()
   * のrenderSourceList()で自然に最新状態が反映される。 */
  function renderSourceListIfCurrentSpace(spaceId) {
    if (spaceId === currentTrackedSpaceId) {
      renderSourceList();
    }
  }

  /** space_id別の直列queueへ、1件の送信を積む。同一spaceId宛の送信は、
   * 前の送信が成功・失敗いずれで終わっても、必ずそれが終わってから
   * 次が実行される(fetch発行順=Spatial State反映順を保証する)。 */
  function enqueueSubmission(item) {
    const spaceId = item.pendingSubmission.spaceId;
    const prevTail = sendQueueTailBySpaceId.get(spaceId) ?? Promise.resolve();
    const thisSend = prevTail.then(() => sendOne(item));
    // tail自体はここで失敗を握りつぶし、1件の失敗が後続の送信を止めないようにする
    // (個々のitemの成否はitem.status/lastErrorで管理する)。
    sendQueueTailBySpaceId.set(spaceId, thisSend.catch(() => {}));
    return thisSend;
  }

  /** 実際に1件をサーバーへ送信する。成功したらpendingSubmission(大きな
   * positions/colors)を解放し、失敗したら再送信用にそのまま保持する。 */
  async function sendOne(item) {
    const { positions, colors, filename, spaceId, sourceFilename } = item.pendingSubmission;
    try {
      await exportRegisteredPointCloud(positions, colors, filename, outputHandler, spaceId, sourceFilename);
      item.status = "done";
      item.pendingSubmission = null; // 成功時は大きなpositions/colorsを必ず解放する
      item.lastError = null;
    } catch (err) {
      item.status = "failed";
      item.lastError = err.message;
      // pendingSubmissionはFAILED時のみ再送用に保持したまま残す
    }
    renderSourceListIfCurrentSpace(spaceId);
  }

  const targetViewer = createViewer(document.getElementById("regTargetCanvas"));
  const sourceViewer = createViewer(document.getElementById("regSourceCanvas"));

  const statusEl = document.getElementById("regStatus");
  const computeBtn = document.getElementById("regComputeBtn");
  const previewBar = document.getElementById("regPreviewBar");
  const spaceLabelEl = document.getElementById("regSpaceLabel");
  const sourceListEl = document.getElementById("regSourceList");

  function setStatus(text) { statusEl.textContent = text; }

  function updateComputeButton() {
    computeBtn.disabled = !(state.targetPicks.length === 2 && state.sourcePicks.length === 2);
  }

  function updateSpaceLabel() {
    spaceLabelEl.textContent = `対象Local Space: ${currentTrackedSpaceId ?? "(未選択)"}`;
  }

  function renderSourceList() {
    const list = currentSpaceList();
    if (list.items.length === 0) {
      sourceListEl.innerHTML = `<div class="reg-source-empty">まだSourceが選択されていません(上の「Source」からファイルを選択してください)</div>`;
      return;
    }
    sourceListEl.innerHTML = list.items.map((item) => {
      const isActive = list.activeKey === item.key; // 作業中(WORKING)はstatusではなくactiveKeyとの一致で導出する
      let badgeClass, badgeText;
      if (item.status === "processing") { badgeClass = "reg-badge-processing"; badgeText = "送信中"; }
      else if (item.status === "done") { badgeClass = "reg-badge-done"; badgeText = "完了"; }
      else if (item.status === "failed") { badgeClass = "reg-badge-failed"; badgeText = "失敗"; }
      else if (isActive) { badgeClass = "reg-badge-active"; badgeText = "作業中"; }
      else { badgeClass = "reg-badge-pending"; badgeText = "未処理"; }
      return `
        <div class="reg-source-row${isActive ? " reg-source-row-active" : ""}" data-key="${escapeHtml(item.key)}">
          <span class="reg-source-name">${escapeHtml(item.file.name)}</span>
          <span class="reg-source-size">${item.sizeLabel}</span>
          <span class="reg-badge ${badgeClass}">${badgeText}</span>
        </div>
      `;
    }).join("");
    sourceListEl.querySelectorAll(".reg-source-row").forEach((row) => {
      row.addEventListener("click", () => activateSourceListItem(row.dataset.key));
    });
  }

  /** 一覧の1件を選び、Sourceビューワへ読み込む(既存のSource読込ロジックそのもの、
   * 呼び出し元がfile inputの変更イベントから一覧のクリックへ変わっただけ)。 */
  async function activateSourceListItem(key) {
    const list = currentSpaceList();
    const item = list.items.find((i) => i.key === key);
    if (!item) return;

    // 送信処理中のitemはUI操作の対象にしない(二重送信防止・PROCESSING中は
    // 他のSourceを自由に操作できるが、このitem自体はクリックしても何もしない)。
    if (item.status === "processing") {
      setStatus(`${item.file.name} は送信処理中です。完了まで少しお待ちください(他のSourceは続けて操作できます)。`);
      return;
    }

    if (item.status === "failed") {
      const retry = window.confirm(
        "送信に失敗したSourceです。計算済みの結果をそのまま再送信しますか?" +
        "(キャンセルすると何もしません。OKで再送信キューに積みます)",
      );
      if (retry && item.pendingSubmission) {
        item.status = "processing";
        renderSourceList();
        setStatus(`${item.file.name} を再送信しています(バックグラウンド)。続けて次のSourceを選択できます。`);
        enqueueSubmission(item);
      } else if (retry && !item.pendingSubmission) {
        // 理論上は起きない(FAILED化と同時にpendingSubmissionを保持するため)が、
        // 万一無い場合はやり直しを促すだけにする(データを推測して送らない)。
        setStatus(`${item.file.name} の再送信データが見つかりません。一覧から改めて選び直してください。`);
      }
      return;
    }

    if (item.status === "done") {
      const proceed = window.confirm("既に完了済みのSourceです。再度読み込んで処理しますか?(重複してVGICP・Spatial State更新が実行されます)");
      if (!proceed) return;
    }

    list.activeKey = key; // 他の項目は「作業中」表示から自動的に外れる(活きているのは1件だけ)
    renderSourceList();

    setStatus(`${item.file.name} を読み込み中...`);
    try {
      const { positions, colors } = await loadPointCloud(item.file);
      state.source = { positions, colors, outerHash: buildSpatialHash(positions, OUTER_RADIUS) };
      sourceViewer.setPoints(positions, colors);
      targetViewer.clearOverlay(); sourceViewer.clearMarkers();
      state.sourcePicks = [];
      state.previewPositions = null;
      previewBar.style.display = "none";
      setStatus(`${item.file.name} 読み込み完了(${positions.length / 3}点)。Target/Source両方で2点ずつクリックしてください`);
      updatePickStatus();
    } catch (err) {
      setStatus(`Sourceの読み込みに失敗しました: ${err.message}`);
    }
  }

  /**
   * Local Spaceの切り替えを検知し、Source一覧・viewer・pick状態を
   * 「現在開いているLocal Space」向けに同期する。呼び出し側
   * (local_space_prototype.html)が、Local Spaceを切り替えるたびに呼ぶ想定
   * (画面が「追加モード」タブでなくても安全に呼べる)。
   */
  function syncToCurrentSpace() {
    const liveSpaceId = getSpaceId();
    if (liveSpaceId === currentTrackedSpaceId) {
      updateSpaceLabel();
      return;
    }

    // 直前の空間で「作業中」だった項目は、viewerの中身(pick・プレビュー)を
    // 失うため、activeKeyを外して「未処理」表示に戻す(完了済みのstatusは
    // item自体に残るため消えない)。
    if (currentTrackedSpaceId !== undefined && currentTrackedSpaceId !== null) {
      getOrCreateSpaceList(currentTrackedSpaceId).activeKey = null;
    }
    currentTrackedSpaceId = liveSpaceId;

    // 別Local SpaceのSourceが画面に残ったまま操作されることがないよう、
    // viewer・pick・previewを明示的にクリアする。
    state.source = null;
    state.sourcePicks = [];
    state.previewPositions = null;
    sourceViewer.clearPoints();
    sourceViewer.clearMarkers();
    targetViewer.clearOverlay();
    previewBar.style.display = "none";
    state.phase = PHASE.WAITING_FILES;

    // 【2026-09-02修正】別Local SpaceのTarget(Base Map)が残ったまま次の
    // Local Spaceで位置合わせ・送信されることがないよう、Targetも明示的に
    // クリアする(従来はSourceのみクリアしており、Targetは切り替え後も
    // 前のLocal Space分が残り続ける不具合があった)。
    state.target = null;
    state.targetPicks = [];
    targetViewer.clearPoints();
    targetViewer.clearMarkers();
    document.getElementById("regTargetSelect").value = "";
    populateTargetOptionsForSpace(currentTrackedSpaceId);

    updateSpaceLabel();
    renderSourceList();
    updatePickStatus();
    setStatus(
      currentTrackedSpaceId
        ? `対象Local Spaceを ${currentTrackedSpaceId} に切り替えました。Source一覧から選択してください`
        : "Local Spaceが選択されていません",
    );
  }

  document.getElementById("regTargetSelect").addEventListener("change", async (e) => {
    const url = e.target.value;
    if (!url) return;
    setStatus("Targetを読み込み中...");
    try {
      const { positions, colors } = await loadPointCloudFromURL(url);
      state.target = { positions, colors, outerHash: buildSpatialHash(positions, OUTER_RADIUS) };
      targetViewer.setPoints(positions, colors);
      targetViewer.clearOverlay(); targetViewer.clearMarkers(); // 前回のマーカー・プレビューが残らないようにする
      state.targetPicks = [];
      previewBar.style.display = "none";
      setStatus(`Target読み込み完了(${positions.length / 3}点)。Target/Source両方で2点ずつクリックしてください`);
      updatePickStatus();
    } catch (err) {
      setStatus(`Targetの読み込みに失敗しました: ${err.message}`);
    }
  });

  // ベースマップ一覧(base_maps/manifest.json)は1回だけ取得してキャッシュし、
  // ドロップダウンへの反映は現在のLocal Spaceに属するものだけに絞って行う
  // (populateTargetOptionsForSpace参照)。
  const baseMapManifestPromise = loadBaseMapManifest().catch((err) => {
    setStatus(`ベースマップ一覧を取得できませんでした(${err.message})。base_maps/manifest.json を配置してください`);
    return null;
  });

  /**
   * manifestのエントリから、指定したspace_idに属するものだけを厳密一致で
   * 解決する(backend/server.pyの_find_base_map_path()と同じ規則)。
   * 正式な永続化キーはspace_id完全一致。tokutei_code単独キーは、既存データ
   * 互換のためのread-onlyなlegacy fallbackとしてのみ使う。
   * substring/prefix/includesによる曖昧一致は行わない。他Local Space・
   * 他Buildingのエントリが候補に混ざることはない。
   */
  function resolveBaseMapEntryForSpace(manifest, spaceId) {
    if (!spaceId || !manifest) return null;
    const exact = manifest.find((m) => m.id === spaceId);
    if (exact) return exact;
    const tokuteiCode = spaceId.includes("-") ? spaceId.slice(spaceId.lastIndexOf("-") + 1) : spaceId;
    return manifest.find((m) => m.id === tokuteiCode) ?? null;
  }

  let targetOptionsGeneration = 0; // space切り替え中の取得競合を無効化するための世代カウンタ

  /** Targetドロップダウンを、現在のLocal Spaceに属する候補(0件または1件)
   * だけで再構築する。他space_idのBase Mapは候補にも出さない。 */
  async function populateTargetOptionsForSpace(spaceId) {
    const myGeneration = ++targetOptionsGeneration;
    const selectEl = document.getElementById("regTargetSelect");
    selectEl.disabled = true;
    selectEl.innerHTML = '<option value="">読み込み中...</option>';

    const manifest = await baseMapManifestPromise;
    if (myGeneration !== targetOptionsGeneration) return; // 別のspaceへ切り替わっていたら破棄

    if (!manifest) {
      selectEl.innerHTML = '<option value="">一覧の取得に失敗</option>';
      return;
    }
    if (!spaceId) {
      selectEl.innerHTML = '<option value="">Local Spaceが選択されていません</option>';
      return;
    }
    const entry = resolveBaseMapEntryForSpace(manifest, spaceId);
    if (!entry) {
      selectEl.innerHTML = '<option value="">Base Mapなし</option>';
      return;
    }
    selectEl.disabled = false;
    selectEl.innerHTML = '<option value="">選択してください</option>' +
      `<option value="./base_maps/${entry.file}">${escapeHtml(entry.label ?? entry.id)}</option>`;
  }

  // Sourceファイルの追加(複数選択可)。ここでは読み込み・表示は行わず、
  // 現在のLocal Space向けの一覧へ追記するだけ(一括自動処理にしない)。
  document.getElementById("regSourceFile").addEventListener("change", (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = ""; // 同じファイルを続けて選び直せるようにする
    if (files.length === 0) return;

    const list = currentSpaceList();
    const spaceId = currentTrackedSpaceId;
    let added = 0, skipped = 0;
    for (const file of files) {
      const key = keyForFile(file);
      if (list.items.some((i) => i.key === key)) { skipped++; continue; } // 同一ファイルの重複追加を防ぐ
      list.items.push({ key, file, status: "pending", spaceId, sizeLabel: fileSizeLabel(file.size) });
      added++;
    }
    renderSourceList();
    setStatus(`${added}件追加しました(合計${list.items.length}件${skipped > 0 ? `、重複${skipped}件はスキップ` : ""})。一覧からSourceを選択してください`);
  });

  const targetPicksEl = document.getElementById("regTargetPicks");
  const sourcePicksEl = document.getElementById("regSourcePicks");

  /**
   * 選択済みの点を、色スウォッチ・座標・削除ボタンつきの表として描画する。
   * 任意の行を削除できるようにし(「最後の1点だけ」ではなく)、削除後は
   * 残った点を詰めて1点目=黄色・2点目=水色になるよう色を振り直す。
   */
  function renderPicksTable(el, picks, onRemove) {
    if (picks.length === 0) { el.innerHTML = ""; el.style.display = "none"; return; }
    el.style.display = "flex";
    el.innerHTML = picks.map((p, i) => `
      <div class="reg-pick-row">
        <span class="reg-pick-swatch" style="background:#${PICK_COLORS[i].toString(16).padStart(6, "0")}"></span>
        <span class="reg-pick-coord">${p[0].toFixed(2)}, ${p[1].toFixed(2)}, ${p[2].toFixed(2)}</span>
        <button class="reg-pick-remove" data-idx="${i}" title="この点を削除">×</button>
      </div>
    `).join("");
    el.querySelectorAll(".reg-pick-remove").forEach(btn => {
      btn.addEventListener("click", () => onRemove(parseInt(btn.dataset.idx, 10)));
    });
  }

  /** targetViewer/sourceViewer上のマーカーを、現在のpicks配列と色の対応が
   * 崩れないよう、全部作り直す(途中の点を削除した場合、残りの点の色を
   * 1点目=黄色・2点目=水色に振り直す必要があるため)。 */
  function rebuildTargetMarkers() {
    targetViewer.clearMarkers();
    state.targetPicks.forEach((p, i) => targetViewer.addMarker(p, PICK_COLORS[i]));
  }
  function rebuildSourceMarkers() {
    sourceViewer.clearMarkers();
    state.sourcePicks.forEach((p, i) => sourceViewer.addMarker(p, PICK_COLORS[i]));
  }

  function removeTargetPick(idx) {
    state.targetPicks.splice(idx, 1);
    rebuildTargetMarkers();
    updatePickStatus();
  }
  function removeSourcePick(idx) {
    state.sourcePicks.splice(idx, 1);
    rebuildSourceMarkers();
    updatePickStatus();
  }

  function updatePickStatus() {
    setStatus(`Target: ${state.targetPicks.length}/2点、Source: ${state.sourcePicks.length}/2点`);
    updateComputeButton();
    renderPicksTable(targetPicksEl, state.targetPicks, removeTargetPick);
    renderPicksTable(sourcePicksEl, state.sourcePicks, removeSourcePick);
  }

  targetViewer.onPick((point) => {
    if (!state.target || state.targetPicks.length >= 2) return;
    const pickIndex = state.targetPicks.length; // 0番目=1点目、1番目=2点目
    state.targetPicks.push(point);
    targetViewer.addMarker(point, PICK_COLORS[pickIndex]);
    updatePickStatus();
  });
  sourceViewer.onPick((point) => {
    if (!state.source || state.sourcePicks.length >= 2) return;
    const pickIndex = state.sourcePicks.length;
    state.sourcePicks.push(point);
    sourceViewer.addMarker(point, PICK_COLORS[pickIndex]);
    updatePickStatus();
  });

  const progressWrap = document.getElementById('regProgress');
  const progressBar = document.getElementById('regProgressBar');

  /**
   * 点の選択・プレビュー・進捗表示を初期状態に戻す。Target/Sourceの
   * 点群自体(読み込み済みファイル)は保持したまま、次の位置合わせを
   * すぐ始められるようにする(「やり直す」・「採用して保存」成功、どちらからも呼ぶ)。
   */
  function resetPicking() {
    targetViewer.clearOverlay();
    targetViewer.clearMarkers(); sourceViewer.clearMarkers();
    state.targetPicks = []; state.sourcePicks = [];
    state.previewPositions = null;
    previewBar.style.display = "none";
    state.phase = state.target && state.source ? PHASE.PICKING_TARGET : PHASE.WAITING_FILES;
    updatePickStatus();
  }

  computeBtn.addEventListener("click", async () => {
    state.phase = PHASE.COMPUTING;
    computeBtn.disabled = true;
    progressWrap.style.display = 'block';
    progressBar.style.width = '0%';

    const totalSteps = 4; // target2点 + source2点
    let stepsDone = 0;
    function onSubProgress(done, total) {
      const subFraction = total > 0 ? done / total : 1;
      const overall = (stepsDone + subFraction) / totalSteps;
      progressBar.style.width = `${Math.round(overall * 100)}%`;
    }
    setStatus("エッジ特徴を精緻化しています...");

    const targetFeat = [];
    for (const p of state.targetPicks) {
      targetFeat.push(await identifyFeature(state.target.positions, p, state.target.outerHash, onSubProgress));
      stepsDone++;
    }
    const sourceFeat = [];
    for (const p of state.sourcePicks) {
      sourceFeat.push(await identifyFeature(state.source.positions, p, state.source.outerHash, onSubProgress));
      stepsDone++;
    }

    progressWrap.style.display = 'none';

    if (targetFeat.some(p => !p) || sourceFeat.some(p => !p)) {
      setStatus("エッジ特徴の検出に失敗しました。クリックする位置を変えて再試行してください");
      computeBtn.disabled = false;
      return;
    }

    setStatus("変換行列を計算しています...");
    const transformed = registerRigidZAxis(state.source.positions, sourceFeat, targetFeat);
    state.previewPositions = transformed;

    targetViewer.showOverlay(transformed, state.source.colors ?? null, 0x0f9d8f);
    previewBar.style.display = "flex";
    state.phase = PHASE.PREVIEW;
    setStatus("プレビュー表示中。Target側のビューワで、緑がsourceの変換結果です。この結果でよければ「採用して保存」、やり直す場合は「やり直す」を押してください");
  });

  document.getElementById("regRetryBtn").addEventListener("click", () => {
    resetPicking();
    setStatus("再選択してください: Target/Sourceで2点ずつクリック");
  });

  document.getElementById("regAcceptBtn").addEventListener("click", () => {
    // 【fail-closed】現在のgetSpaceId()と、現在activeなSource一覧項目が
    // 記録しているspaceIdが一致しない場合は、絶対に送信しない。
    // (UI側の追従(syncToCurrentSpace)が何らかの理由で漏れていた場合の
    // 最後の砦。ここを通過しない限りPOSTは一切発生しない。このチェックは
    // 送信キューへ積む前、ユーザー操作の瞬間に一度だけ行う。)
    const liveSpaceId = getSpaceId();
    const list = currentSpaceList();
    const activeItem = list.items.find((i) => i.key === list.activeKey);

    if (!activeItem) {
      setStatus("有効なSourceが選択されていません。一覧からSourceを選んでください。");
      return;
    }
    if (activeItem.spaceId !== liveSpaceId || currentTrackedSpaceId !== liveSpaceId) {
      setStatus(
        "対象Local Spaceが一致しないため、送信を中止しました(fail-closed)。" +
        "画面を開き直すか、一覧からSourceを選び直してください。",
      );
      return;
    }
    if (activeItem.status === "processing") {
      // 通常はUI上activeになり得ないが、二重クリック等への保険として。
      setStatus("既に送信処理中です。");
      return;
    }

    // 【非同期queue化】ここではネットワーク送信を待たない。送信内容を
    // itemへスナップショットしてPROCESSINGにし、即座にpicking状態をリセットして
    // 次のSourceへ進めるようにする。実際の送信はspace_id別の直列queueへ積むだけ。
    const filename = `source_registered_${Date.now()}.ply`;
    activeItem.pendingSubmission = {
      positions: state.previewPositions,
      colors: state.source.colors ?? null,
      filename,
      spaceId: liveSpaceId,
      // 元のSourceファイル名(サーバー側でregistration_results/のアーカイブが
      // 元Sourceを追跡できるようにするためだけの情報、2026-09-02追加)。
      sourceFilename: activeItem.file.name,
    };
    activeItem.status = "processing";
    list.activeKey = null; // 「作業中」から外す(このitemはもうpicking対象ではない)
    renderSourceList();
    resetPicking(); // 保存を待たず、次の位置合わせにすぐ移れるようリセットする
    setStatus(
      `${filename} をバックグラウンドで送信しています(サーバー側でVGICP精密位置合わせ→` +
      `Spatial State更新が実行されます)。続けて次のSourceを選択できます。`,
    );

    enqueueSubmission(activeItem);
  });

  syncToCurrentSpace(); // 初期化時点のLocal Space向けに一覧・ラベルを構築する

  return {
    dispose() { targetViewer.dispose(); sourceViewer.dispose(); },
    refresh: syncToCurrentSpace,
  };
}

/* ============================================================
   3Dビューワ(点群表示 + クリックでの点選択 + マーカー/プレビュー表示)
   ============================================================ */

function createViewer(holder) {
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

  let pointsObject = null;
  let overlayObject = null;
  const markers = [];
  const pickCallbacks = [];

  function resize() {
    const w = holder.clientWidth, h = holder.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  // 【重要】これまでresizeはウィンドウのリサイズイベントでしか呼ばれておらず、
  // 初期化直後には一度も実行されていなかった。そのためThree.jsの既定サイズ
  // (300x150相当)のまま固定されてしまっていた。レイアウトが確定してから
  // 改めてリサイズを実行する。
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

  function setPoints(positions, colors) {
    if (pointsObject) { scene.remove(pointsObject); pointsObject.geometry.dispose(); pointsObject.material.dispose(); }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(toDisplayBuffer(positions), 3));
    let mat;
    if (colors) {
      geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      mat = new THREE.PointsMaterial({ size: 0.02, vertexColors: true });
    } else {
      mat = new THREE.PointsMaterial({ size: 0.02, color: 0x888888 });
    }
    pointsObject = new THREE.Points(geo, mat);
    scene.add(pointsObject);

    geo.computeBoundingSphere();
    if (geo.boundingSphere) {
      const c = geo.boundingSphere.center;
      const r = geo.boundingSphere.radius || 1;
      camera.position.set(c.x + r, c.y + r, c.z + r);
      controls.target.copy(c);
    }
    resize();
  }

  /** 表示中の点群を消す(Local Space切り替え時、別空間のSourceが画面に
   * 残らないようにするため。2026-09-02追加)。 */
  function clearPoints() {
    if (pointsObject) { scene.remove(pointsObject); pointsObject.geometry.dispose(); pointsObject.material.dispose(); pointsObject = null; }
  }

  function showOverlay(positions, colors, fallbackHex) {
    clearOverlay();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(toDisplayBuffer(positions), 3));
    const mat = new THREE.PointsMaterial({ size: 0.025, color: fallbackHex });
    overlayObject = new THREE.Points(geo, mat);
    scene.add(overlayObject);
  }
  function clearOverlay() {
    if (overlayObject) { scene.remove(overlayObject); overlayObject.geometry.dispose(); overlayObject.material.dispose(); overlayObject = null; }
  }

  function addMarker(point, hex) {
    const geo = new THREE.SphereGeometry(0.03, 12, 12);
    const mat = new THREE.MeshBasicMaterial({ color: hex });
    const marker = new THREE.Mesh(geo, mat);
    const [dx, dy, dz] = toDisplayCoordinates(point[0], point[1], point[2]);
    marker.position.set(dx, dy, dz);
    scene.add(marker);
    markers.push(marker);
  }
  function clearMarkers() {
    markers.forEach(m => { scene.remove(m); m.geometry.dispose(); m.material.dispose(); });
    markers.length = 0;
  }

  const raycaster = new THREE.Raycaster();
  raycaster.params.Points.threshold = 0.03;
  const mouse = new THREE.Vector2();

  // 【重要】素朴な"click"イベントだけで判定すると、OrbitControlsによる
  // わずかなドラッグ(カメラ回転)がクリックと誤認され、選択が意図せず
  // 発生したり、逆にOrbitControls側にイベントを奪われて選択が拾えなく
  // なったりする。pointerdown/pointerupの座標差が小さい(=ドラッグして
  // いない)場合だけを「クリックによる選択」とみなすことで、カメラ回転と
  // 選択操作を確実に区別する。
  let pointerDownPos = null;
  const CLICK_MOVE_THRESHOLD_PX = 5;

  renderer.domElement.addEventListener("pointerdown", (ev) => {
    pointerDownPos = [ev.clientX, ev.clientY];
  });

  renderer.domElement.addEventListener("pointerup", (ev) => {
    if (!pointerDownPos) return;
    const [dx0, dy0] = pointerDownPos;
    pointerDownPos = null;
    const moved = Math.hypot(ev.clientX - dx0, ev.clientY - dy0);
    if (moved > CLICK_MOVE_THRESHOLD_PX) return; // ドラッグ(カメラ回転)とみなし、選択はしない

    if (!pointsObject) return;
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const hit = raycaster.intersectObject(pointsObject);
    if (hit.length > 0) {
      // ヒット点は表示座標系なので、元のraw座標系に戻してから通知する
      const [rx, ry, rz] = fromDisplayCoordinates(hit[0].point.x, hit[0].point.y, hit[0].point.z);
      pickCallbacks.forEach(cb => cb([rx, ry, rz]));
    }
  });

  return {
    setPoints, clearPoints, showOverlay, clearOverlay, addMarker, clearMarkers,
    onPick(cb) { pickCallbacks.push(cb); },
    dispose() { window.removeEventListener("resize", resize); renderer.dispose(); },
  };
}

function injectStyles() {
  if (document.getElementById("reg-styles")) return;
  const style = document.createElement("style");
  style.id = "reg-styles";
  style.textContent = `
    .reg-layout { display: flex; flex-direction: column; flex: 1; min-height: 0; }
    .reg-toolbar { display: flex; align-items: center; gap: 16px; padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel); flex-wrap: wrap; flex-shrink: 0; }
    .reg-file-group { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--text-dim); }
    .reg-file-group input, .reg-file-group select {
      font-size: 12.5px; font-family: inherit; padding: 5px 8px; border: 1px solid var(--border); border-radius: 5px;
      background: var(--panel); color: var(--text); min-width: 160px;
    }
    .reg-file-group select:focus, .reg-file-group input:focus { outline: none; border-color: var(--accent); }
    .reg-space-label { font-size: 12px; font-weight: 700; color: var(--accent-dark); background: var(--accent-soft); padding: 5px 10px; border-radius: 5px; white-space: nowrap; }
    .reg-status { font-size: 12.5px; color: var(--text); flex: 1; min-width: 160px; }
    .reg-progress { width: 140px; height: 6px; background: var(--border-soft); border-radius: 4px; overflow: hidden; }
    .reg-progress-bar { height: 100%; background: var(--accent); width: 0%; transition: width 0.15s ease-out; }
    .reg-btn {
      border: 1px solid var(--border); background: var(--panel); padding: 7px 14px; border-radius: 6px;
      font-size: 13px; cursor: pointer; font-family: inherit; color: var(--text); transition: background 0.12s, border-color 0.12s;
    }
    .reg-btn:hover:not(:disabled) { background: var(--accent-soft); border-color: var(--accent); }
    .reg-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .reg-btn-primary { background: var(--accent); color: var(--text-inverse); border-color: var(--accent); font-weight: 600; }
    .reg-btn-primary:hover:not(:disabled) { background: var(--accent-dark); }
    .reg-source-list-wrap { border-bottom: 1px solid var(--border); background: var(--bg); padding: 8px 20px; flex-shrink: 0; }
    .reg-source-list-label { font-size: 11px; color: var(--text-faint); font-weight: 700; letter-spacing: 0.03em; margin-bottom: 6px; }
    .reg-source-list { display: flex; flex-direction: column; gap: 4px; max-height: 140px; overflow-y: auto; }
    .reg-source-empty { font-size: 12px; color: var(--text-faint); padding: 6px 2px; }
    .reg-source-row {
      display: flex; align-items: center; gap: 10px; padding: 6px 10px; border: 1px solid var(--border-soft);
      border-radius: 6px; background: var(--panel); cursor: pointer; font-size: 12.5px; color: var(--text); transition: border-color 0.12s, background 0.12s;
    }
    .reg-source-row:hover { border-color: var(--accent); background: var(--panel-raised); }
    .reg-source-row-active { border-color: var(--accent); background: var(--accent-soft); }
    .reg-source-name { flex: 1; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .reg-source-size { color: var(--text-faint); font-variant-numeric: tabular-nums; font-size: 11px; }
    .reg-badge { font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 20px; white-space: nowrap; }
    .reg-badge-pending { background: var(--panel-raised); color: var(--text-dim); }
    .reg-badge-active { background: var(--status-warning-soft); color: var(--status-warning); }
    .reg-badge-processing { background: var(--status-info-soft); color: var(--status-info); }
    .reg-badge-done { background: var(--status-solved-soft); color: var(--status-solved); }
    .reg-badge-failed { background: var(--status-error-soft); color: var(--status-error); }
    .reg-viewports { display: flex; flex: 1; min-height: 0; }
    .reg-viewport { flex: 1; min-height: 0; display: flex; flex-direction: column; border-right: 1px solid var(--border); }
    .reg-viewport:last-child { border-right: none; }
    .reg-viewport-label {
      font-size: 11.5px; color: var(--text-dim); padding: 8px 12px; background: var(--bg);
      border-bottom: 1px solid var(--border-soft); font-weight: 500;
      display: flex; align-items: center; justify-content: space-between;
    }
    .reg-picks-table {
      display: none; flex-direction: column; gap: 4px; padding: 8px 12px;
      background: var(--bg); border-bottom: 1px solid var(--border-soft);
    }
    .reg-pick-row {
      display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: var(--text);
    }
    .reg-pick-swatch { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; box-shadow: inset 0 0 0 1px var(--swatch-outline); }
    .reg-pick-coord { flex: 1; font-variant-numeric: tabular-nums; color: var(--text-dim); }
    .reg-pick-remove {
      border: none; background: none; color: var(--text-faint); font-size: 14px; line-height: 1;
      cursor: pointer; padding: 2px 5px; border-radius: 4px; transition: background 0.12s, color 0.12s;
    }
    .reg-pick-remove:hover { background: var(--status-error-soft); color: var(--status-error); }
    .reg-canvas-holder { flex: 1; min-height: 0; position: relative; }
    .reg-canvas-holder canvas { display: block; }
    .reg-preview-bar {
      position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%);
      z-index: 200; display: flex; align-items: center; gap: 14px;
      padding: 12px 20px; border-radius: 10px; border: 1px solid var(--border);
      background: var(--panel); font-size: 12.5px; color: var(--text);
      box-shadow: var(--shadow-md); max-width: calc(100vw - 48px);
    }
  `;
  document.head.appendChild(style);
}
