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
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { loadPointCloud, loadPointCloudFromURL, loadBaseMapManifest, exportRegisteredPointCloud, downloadHandler } from "./pointcloud-io.js";
import { buildSpatialHash } from "./spatial-hash.js";
import { identifyFeature, OUTER_RADIUS } from "./edge-feature.js";
import { registerRigidZAxis } from "./rigid-transform.js";

const PHASE = {
  WAITING_FILES: "waiting_files",
  PICKING_TARGET: "picking_target",
  PICKING_SOURCE: "picking_source",
  COMPUTING: "computing",
  PREVIEW: "preview",
};

const PICK_COLORS = [0xf5c518, 0x22c3d6]; // 1番目=黄色, 2番目=水色(target/sourceどちらも共通)

/**
 * 追加モードの画面を、与えられたコンテナDOM要素の中に構築する。
 * @param {HTMLElement} container
 * @param {{ outputHandler?: Function, spaceId?: string }} options
 *   outputHandlerを渡せば、将来「別サーバーへの送信」に差し替えられる
 *   (既定はブラウザダウンロード)。spaceIdは、どのローカル空間向けの
 *   ラフレジ結果かをサーバー側へ伝えるために使う。
 */
export function initRegistrationMode(container, options = {}) {
  const outputHandler = options.outputHandler ?? downloadHandler;
  const spaceId = options.spaceId ?? null;

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
          <label>Source(計測データ)</label>
          <input type="file" id="regSourceFile" accept=".las,.ply,.xyz">
        </div>
        <div class="reg-status" id="regStatus">ファイルを2つ選択してください</div>
        <div class="reg-progress" id="regProgress" style="display:none;">
          <div class="reg-progress-bar" id="regProgressBar"></div>
        </div>
        <button class="reg-btn" id="regComputeBtn" disabled>ラフレジストレーション実行</button>
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
    source: null,
    targetPicks: [], // クリックした生の座標(最大2)
    sourcePicks: [],
    previewPositions: null,
  };

  const targetViewer = createViewer(document.getElementById("regTargetCanvas"));
  const sourceViewer = createViewer(document.getElementById("regSourceCanvas"));

  const statusEl = document.getElementById("regStatus");
  const computeBtn = document.getElementById("regComputeBtn");
  const previewBar = document.getElementById("regPreviewBar");

  function setStatus(text) { statusEl.textContent = text; }

  function updateComputeButton() {
    computeBtn.disabled = !(state.targetPicks.length === 2 && state.sourcePicks.length === 2);
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

  // ベースマップ一覧(base_maps/manifest.json)を取得し、ドロップダウンを構築する
  (async () => {
    const selectEl = document.getElementById("regTargetSelect");
    try {
      const manifest = await loadBaseMapManifest();
      selectEl.innerHTML = '<option value="">選択してください</option>' +
        manifest.map(m => `<option value="./base_maps/${m.file}">${m.label ?? m.id}</option>`).join("");
    } catch (err) {
      selectEl.innerHTML = '<option value="">一覧の取得に失敗</option>';
      setStatus(`ベースマップ一覧を取得できませんでした(${err.message})。base_maps/manifest.json を配置してください`);
    }
  })();

  document.getElementById("regSourceFile").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setStatus("Sourceを読み込み中...");
    const { positions, colors } = await loadPointCloud(file);
    state.source = { positions, colors, outerHash: buildSpatialHash(positions, OUTER_RADIUS) };
    sourceViewer.setPoints(positions, colors);
    targetViewer.clearOverlay(); sourceViewer.clearMarkers(); // 前回のマーカー・プレビューが残らないようにする
    state.sourcePicks = [];
    previewBar.style.display = "none";
    setStatus("Source読み込み完了。Target/Source両方で2点ずつクリックしてください");
    updatePickStatus();
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
   * すぐ始められるようにする(採用後・やり直し、どちらからも呼ぶ)。
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

  document.getElementById("regAcceptBtn").addEventListener("click", async () => {
    const filename = `source_registered_${Date.now()}.ply`;
    setStatus("サーバーへ送信しています...");
    try {
      await exportRegisteredPointCloud(state.previewPositions, state.source.colors ?? null, filename, outputHandler, spaceId);
      resetPicking(); // 保存できたら、次の位置合わせにすぐ移れるようリセットする
      setStatus(`送信しました: ${filename}(サーバー側でVGICP精密位置合わせ→JSON化が実行されます)。続けて次の点を選択できます`);
    } catch (err) {
      // 送信失敗時はリセットしない(選んだ点・プレビューを保ったまま、再送信を試せるようにする)
      setStatus(`送信に失敗しました: ${err.message}`);
    }
  });

  return {
    dispose() { targetViewer.dispose(); sourceViewer.dispose(); },
  };
}

/* ============================================================
   3Dビューワ(点群表示 + クリックでの点選択 + マーカー/プレビュー表示)
   ============================================================ */

/**
 * 点群データはZが鉛直上向き(測量・LiDAR一般の慣習)だが、Three.jsはYを
 * 鉛直上向きとして扱う。表示のためにY・Zを入れ替える。
 * この変換は「Y・Zの入れ替え」という自己逆変換(2回適用すると元に戻る)なので、
 * 表示座標→元データ座標への逆変換にも同じ関数をそのまま使える。
 */
function swapYZ(x, y, z) {
  return [x, z, y];
}

function createViewer(holder) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf5f4f1);
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
      const [dx, dy, dz] = swapYZ(rawPositions[i * 3], rawPositions[i * 3 + 1], rawPositions[i * 3 + 2]);
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
    const [dx, dy, dz] = swapYZ(point[0], point[1], point[2]);
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
      // ヒット点は表示座標系(Y-Z入れ替え後)なので、元のデータ座標系に戻してから通知する
      const [rx, ry, rz] = swapYZ(hit[0].point.x, hit[0].point.y, hit[0].point.z);
      pickCallbacks.forEach(cb => cb([rx, ry, rz]));
    }
  });

  return {
    setPoints, showOverlay, clearOverlay, addMarker, clearMarkers,
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
    .reg-toolbar { display: flex; align-items: center; gap: 16px; padding: 12px 20px; border-bottom: 1px solid #e5e3de; background: #fff; flex-wrap: wrap; flex-shrink: 0; }
    .reg-file-group { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: #7a786f; }
    .reg-file-group input, .reg-file-group select {
      font-size: 12.5px; font-family: inherit; padding: 5px 8px; border: 1px solid #e5e3de; border-radius: 5px;
      background: #fff; color: #2b2a27; min-width: 160px;
    }
    .reg-file-group select:focus, .reg-file-group input:focus { outline: none; border-color: #0f9d8f; }
    .reg-status { font-size: 12.5px; color: #2b2a27; flex: 1; min-width: 160px; }
    .reg-progress { width: 140px; height: 6px; background: #eeece7; border-radius: 4px; overflow: hidden; }
    .reg-progress-bar { height: 100%; background: #0f9d8f; width: 0%; transition: width 0.15s ease-out; }
    .reg-btn {
      border: 1px solid #e5e3de; background: #fff; padding: 7px 14px; border-radius: 6px;
      font-size: 13px; cursor: pointer; font-family: inherit; transition: background 0.12s, border-color 0.12s;
    }
    .reg-btn:hover:not(:disabled) { background: #e6f5f3; border-color: #0f9d8f; }
    .reg-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .reg-btn-primary { background: #0f9d8f; color: white; border-color: #0f9d8f; font-weight: 600; }
    .reg-btn-primary:hover:not(:disabled) { background: #0c8377; }
    .reg-viewports { display: flex; flex: 1; min-height: 0; }
    .reg-viewport { flex: 1; min-height: 0; display: flex; flex-direction: column; border-right: 1px solid #e5e3de; }
    .reg-viewport:last-child { border-right: none; }
    .reg-viewport-label {
      font-size: 11.5px; color: #7a786f; padding: 8px 12px; background: #fafaf9;
      border-bottom: 1px solid #f0efe9; font-weight: 500;
      display: flex; align-items: center; justify-content: space-between;
    }
    .reg-picks-table {
      display: none; flex-direction: column; gap: 4px; padding: 8px 12px;
      background: #fafaf9; border-bottom: 1px solid #f0efe9;
    }
    .reg-pick-row {
      display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: #2b2a27;
    }
    .reg-pick-swatch { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; box-shadow: inset 0 0 0 1px rgba(0,0,0,0.15); }
    .reg-pick-coord { flex: 1; font-variant-numeric: tabular-nums; color: #7a786f; }
    .reg-pick-remove {
      border: none; background: none; color: #a7a59c; font-size: 14px; line-height: 1;
      cursor: pointer; padding: 2px 5px; border-radius: 4px; transition: background 0.12s, color 0.12s;
    }
    .reg-pick-remove:hover { background: #fbeaea; color: #d64545; }
    .reg-canvas-holder { flex: 1; min-height: 0; position: relative; }
    .reg-canvas-holder canvas { display: block; }
    .reg-preview-bar {
      position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%);
      z-index: 200; display: flex; align-items: center; gap: 14px;
      padding: 12px 20px; border-radius: 10px; border: 1px solid #e5e3de;
      background: #ffffff; font-size: 12.5px; color: #2b2a27;
      box-shadow: 0 8px 24px rgba(30,29,26,0.18); max-width: calc(100vw - 48px);
    }
  `;
  document.head.appendChild(style);
}
