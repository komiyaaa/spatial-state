/**
 * nodal/nodal-panel.js
 *
 * Nodal Information GUI(ロードマップPhase 3.8、2026-09-03 UI再設計)。
 * backend/server.py が既に提供する Phase 3.6 API
 * (/api/nodal-endpoints, /api/nodal-connections, /api/spatial-resolution/*)を
 * そのまま呼ぶだけで、計算ロジック(transform推定・component解決・
 * Global解決)には一切手を加えない。
 *
 * 【位置づけ】
 * - Nodal Information(NodalEndpoint/NodalConnection)がsource of truth。
 *   このUIから直接編集できるのはendpoint/connection/correspondenceの
 *   作成・削除と、estimate/resolveの明示的な実行だけ。
 * - ConnectionSolution・Spatial Resolution結果(component/global resolution)は
 *   常にderived data(再計算可能)として「表示のみ」を行い、直接編集する
 *   フォームは用意しない(estimateボタン/resolveボタン経由でのみ更新される)。
 * - LOCAL↔LOCAL と LOCAL↔GLOBAL は、connectionカードの色付きバッジで
 *   常に区別して表示する(どちらのconnectionかをGUI上で見誤らないため)。
 * - conflict/unresolved/no-anchor等の状態は、バッジとしてそのまま表示し、
 *   隠したり自動で「解決済みらしく」丸めたりしない。
 * - 1 Local Space = 1 Connectionではない(同じLocal Spaceが複数Connection・
 *   複数Global anchorを持てるgraph構造が前提。backend側は既にこれを
 *   満たしており、このUIはその制約を追加で課さない)。
 *
 * 【2026-09-03 UI再設計: Local↔Local Connection作成のViewer化】
 * 従来、Local↔Local Connectionの作成は「NodalEndpointをlocal_spatial_id
 * 文字列の手入力で作り、correspondence追加フォームでdropdown選択して
 * 紐付ける」という、3D Viewerを介さない手順だった。今回、Local Space A/Bを
 * 左右のViewerに並べてクリックで対応点を取る方式(nodal-space-picker-viewer.js)
 * を主UIとして追加した。local_spatial_idの手入力フォーム(NodalEndpoint欄)は
 * 削除せず、折りたたみ式「詳細/手動入力」として残す(Local↔Global接続では
 * Global側をViewerでpickする手段が無いため、引き続きこちらを使う)。
 *
 * world座標→Local Spatial IDの変換は shared/local-spatial-id.js の
 * worldPointToLocalSpatialId()(backend/point_to_spatial_id.pyの順方向式を
 * そのまま移植したもの、backend/tests/test_local_spatial_id_js_port_matches_python.py
 * で一致を自動検証済み)を使う。Base Map取得・座標変換は既存の
 * registration/pointcloud-io.js・shared/display-coordinates.jsをそのまま
 * 再利用し、新しい実装は作らない。
 *
 * Spatial State・point-cloud registration・Integrated Viewには一切触れない
 * (registration-controller.jsと同様、このファイル単体で完結する)。
 */
import { loadBaseMapManifest, loadPointCloudFromURL } from "../registration/pointcloud-io.js";
import { worldPointToLocalSpatialId } from "../shared/local-spatial-id.js";
import { createSpacePickerViewer, pickColorForIndex } from "./nodal-space-picker-viewer.js";

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s == null ? '' : String(s);
  return div.innerHTML;
}

function shortId(id) {
  return id ? String(id).slice(0, 8) : '';
}

function hex6(n) {
  return `#${n.toString(16).padStart(6, '0')}`;
}

/** Base Map候補は、space_idに厳密一致するmanifest entryのみを対象とする
 * (無ければそのLocal Space自身のtokutei_codeへのlegacy fallbackのみ)。
 * substring/prefix/includesによる曖昧一致はしない(他space_idの候補は
 * 混ざらない、2026-09-03のBase Map選択スコープ修正と同じ規則)。 */
function resolveBaseMapEntryForSpace(manifest, spaceId, tokuteiCode) {
  if (!manifest) return null;
  const exact = manifest.find(m => m.id === spaceId);
  if (exact) return exact;
  return manifest.find(m => m.id === tokuteiCode) ?? null;
}

// SOLVED/WARNING/CONFLICT/ERRORを明確に区別する(2026-09-03、visual design基盤
// 整備。以前はCONFLICT系もUNSOLVABLE系も同じ'danger'色で区別できていなかった)。
// 値は判定ロジックではなく表示クラスの選択のみで、状態の意味・遷移は無変更。
const STATUS_BADGE_KIND = {
  UNSOLVED: 'pending',
  SOLVED: 'solved',
  WARNING_HIGH_RESIDUAL: 'warning',
  UNSOLVABLE: 'error',
  RESOLVED: 'solved',
  CONFLICT: 'conflict',
  NO_ANCHOR: 'pending',
  BLOCKED_BY_LOCAL_CONFLICT: 'conflict',
  ANCHOR_UNRESOLVABLE: 'warning',
  ANCHOR_INSUFFICIENT: 'warning',
  GLOBAL_CONFLICT: 'conflict',
};

function badge(text) {
  const kind = STATUS_BADGE_KIND[text] || 'pending';
  return `<span class="nodal-badge nodal-badge-${kind}">${escapeHtml(text)}</span>`;
}

function radToDeg(rad) {
  return rad == null ? null : (rad * 180) / Math.PI;
}

/**
 * @param {HTMLElement} container
 * @param {() => ({space_id: string, building_id: string, tokutei_code: string}|null)} getContext
 *   現在開いているLocal Space(local_space_prototype.htmlのcurrentSpace)を
 *   都度読みに行くgetter。呼び出し側でLocal Spaceを切り替えたら、
 *   返り値のhandle.refresh()を呼んでもらう想定(値を一度だけ捕まえない)。
 * @returns {{refresh: () => Promise<void>}}
 */
export function initNodalPanel(container, getContext) {
  container.innerHTML = `
    <div class="nodal-layout">
      <div class="nodal-header">
        <div><b>Building</b>: <span id="nodalBuildingId"></span></div>
        <div><b>現在のLocal Space</b>: <span id="nodalSpaceId"></span></div>
      </div>

      <div class="nodal-section nodal-section--primary">
        <div class="nodal-section-title">Local↔Local Connection作成(Viewerでピック)</div>
        <div class="nodal-wizard-space-select">
          <div class="nodal-wizard-space-col">
            <label><span class="nodal-side-chip nodal-side-chip--a">A</span> Local Space A</label>
            <select id="nodalWizardSpaceA"><option value="">選択してください</option></select>
          </div>
          <div class="nodal-wizard-space-col">
            <label><span class="nodal-side-chip nodal-side-chip--b">B</span> Local Space B</label>
            <select id="nodalWizardSpaceB"><option value="">選択してください</option></select>
          </div>
        </div>
        <div class="nodal-wizard-turn-indicator" id="nodalWizardTurnIndicator">Local Space A・Bを両方選択してください</div>
        <div class="nodal-wizard-viewers">
          <div class="nodal-wizard-viewer-col nodal-wizard-viewer-col--a" id="nodalWizardColA">
            <div class="nodal-wizard-viewer-head">
              <div class="nodal-wizard-viewer-label">
                <span class="nodal-side-chip nodal-side-chip--a">A</span>
                <span id="nodalWizardLabelA">Local Space A</span>
              </div>
              <div class="nodal-wizard-viewer-toolbar">
                <label class="nodal-wizard-voxel-toggle"><input type="checkbox" id="nodalWizardShowVoxelA" disabled> Voxel</label>
                <button type="button" class="nodal-btn nodal-btn-small nodal-btn-ghost" id="nodalWizardFitA" disabled>Fit View</button>
              </div>
            </div>
            <div class="nodal-wizard-canvas" id="nodalWizardCanvasA"></div>
          </div>
          <div class="nodal-wizard-viewer-col nodal-wizard-viewer-col--b" id="nodalWizardColB">
            <div class="nodal-wizard-viewer-head">
              <div class="nodal-wizard-viewer-label">
                <span class="nodal-side-chip nodal-side-chip--b">B</span>
                <span id="nodalWizardLabelB">Local Space B</span>
              </div>
              <div class="nodal-wizard-viewer-toolbar">
                <label class="nodal-wizard-voxel-toggle"><input type="checkbox" id="nodalWizardShowVoxelB" disabled> Voxel</label>
                <button type="button" class="nodal-btn nodal-btn-small nodal-btn-ghost" id="nodalWizardFitB" disabled>Fit View</button>
              </div>
            </div>
            <div class="nodal-wizard-canvas" id="nodalWizardCanvasB"></div>
          </div>
        </div>
        <div class="nodal-wizard-correspondences">
          <div class="nodal-wizard-correspondences-title">
            Correspondence <span class="nodal-count" id="nodalWizardCorrCount"></span>
            <span class="nodal-wizard-min-badge" id="nodalWizardMinBadge"></span>
            <button type="button" class="nodal-btn nodal-btn-small nodal-btn-ghost" id="nodalWizardResetBtn">ピックをリセット</button>
          </div>
          <table class="nodal-table" id="nodalWizardCorrTable"></table>
        </div>
        <button id="nodalWizardCreateBtn" class="nodal-btn nodal-btn-primary" disabled>Connectionを作成</button>
        <p class="add-form-hint">
          クリック順は A1 → B1 → A2 → B2 → … で固定です(上の案内に従ってください)。
          correspondenceは最低2件必要です(2件でyaw+translation、3件以上で全点best-fit)。
          番号・marker色はA/B間で対応し、行削除後も崩れません。
        </p>
      </div>

      <details class="nodal-advanced">
        <summary>詳細/手動入力(NodalEndpoint直接作成、LOCAL↔GLOBAL接続等)</summary>

        <div class="nodal-section">
          <div class="nodal-section-title">NodalEndpoint <span class="nodal-count" id="nodalEndpointCount"></span></div>
          <div class="nodal-form">
            <select id="nodalEndpointType">
              <option value="LOCAL">LOCAL</option>
              <option value="GLOBAL">GLOBAL</option>
            </select>
            <input id="nodalEndpointSpaceId" placeholder="space_id">
            <input id="nodalEndpointLocalSpatialId" placeholder="local_spatial_id (例: 11/0/0/0)">
            <input id="nodalEndpointGlobalSpatialId" placeholder="global_spatial_id (例: 16/0/58000/25000)" style="display:none;">
            <input id="nodalEndpointLabel" placeholder="label(任意)">
            <button id="nodalEndpointCreateBtn" class="nodal-btn nodal-btn-primary">追加</button>
          </div>
          <div class="nodal-table-wrap"><table class="nodal-table" id="nodalEndpointTable"></table></div>
        </div>

        <div class="nodal-section">
          <div class="nodal-section-title">NodalConnection(空のConnectionを直接作成)</div>
          <div class="nodal-form">
            <div class="nodal-form-side">
              <label>Endpoint A</label>
              <select id="nodalConnATypeSel"><option value="LOCAL">LOCAL</option><option value="GLOBAL">GLOBAL</option></select>
              <input id="nodalConnASpaceId" placeholder="space_id">
            </div>
            <div class="nodal-form-side">
              <label>Endpoint B</label>
              <select id="nodalConnBTypeSel"><option value="LOCAL">LOCAL</option><option value="GLOBAL">GLOBAL</option></select>
              <input id="nodalConnBSpaceId" placeholder="space_id">
            </div>
            <button id="nodalConnectionCreateBtn" class="nodal-btn nodal-btn-primary">Connection作成</button>
          </div>
          <p class="add-form-hint">building_id は現在開いている建物(<span id="nodalConnBuildingIdHint"></span>)で作成されます。LOCAL↔LOCALはできるだけ上のViewer wizardを使ってください。</p>
        </div>
      </details>

      <div class="nodal-section">
        <div class="nodal-section-title">NodalConnection一覧 <span class="nodal-count" id="nodalConnectionCount"></span></div>
        <div id="nodalConnectionList"></div>
      </div>

      <div class="nodal-section nodal-resolution-section">
        <div class="nodal-section-title">Spatial Resolution</div>
        <p class="add-form-hint">Connection作成とは別の処理です。Building全体の接続グラフ(全Local Space・全Connection・全Global anchor)を辿って、各Local Spaceの配置を解決します。Nodal Information(endpoint/connection/correspondence)の変更後、自動では再計算されません。</p>
        <div class="nodal-form">
          <label style="font-size:11.5px; color:var(--text-dim);">target EPSG</label>
          <input id="nodalTargetEpsg" type="number" value="6677" style="width:100px;">
          <button id="nodalResolveBtn" class="nodal-btn nodal-btn-primary">Building全体を解決(Resolve実行)</button>
        </div>
        <div id="nodalResolutionResult"></div>
      </div>
    </div>
  `;
  injectNodalStyles();

  const state = {
    endpoints: [],
    connections: [],
    resolutionResult: null,
    localSpaces: [], // 現在のbuilding配下のLocal Space一覧(wizardのA/B選択肢)
  };

  // --- Local↔Local Connection作成 wizard state ---
  const wizard = {
    spaceA: null, spaceB: null, // 選択中のLocalSpaceオブジェクト全体(coordinate_definition込み)
    viewerA: null, viewerB: null, // createSpacePickerViewer() handle
    voxelLoadedA: false, voxelLoadedB: false,
    turn: 'A', // 次にクリックすべき側('A'|'B')。ピック数の突き合わせによる推測はしない。
    pending: null, // { index, aPoint, aSpatialId } — Aをクリック済み、Bのクリック待ち
    pairs: [], // 確定済みcorrespondence: [{ index, aPoint, aSpatialId, bPoint, bSpatialId }]
  };

  // 【並行refresh()対策】endpoint作成・connection作成・correspondence追加・
  // estimate・deleteは、いずれも成功後にawait refresh()を呼ぶ。ユーザー操作や
  // このファイルへのE2Eテストが短時間に複数の操作を行うと、複数のrefresh()
  // (それぞれ内部でGETを複数回逐次awaitする)が同時に実行中になり得る。
  // fetchの完了順序は開始順序と一致する保証が無いため、対策無しだと
  // 「後に開始したrefresh()の結果」を「先に開始したが完了が遅れたrefresh()」が
  // 後から上書きしてしまい、直前の操作結果がDOM上で消えて見える
  // (2026-09-01、実機E2Eで実際に発生を確認)。最後に開始したrefresh()の
  // 結果だけを描画に反映する、という単純な世代カウンタで防ぐ。
  let refreshGeneration = 0;

  function endpointById(id) {
    return state.endpoints.find(e => e.endpoint_id === id) || null;
  }

  function describeEndpoint(ep) {
    if (!ep) return '(見つかりません)';
    if (ep.type === 'GLOBAL') return `GLOBAL ${ep.global_spatial_id}`;
    return `LOCAL ${ep.space_id} / ${ep.local_spatial_id}`;
  }

  function refLabel(ref) {
    return ref.type === 'GLOBAL' ? 'GLOBAL' : `LOCAL(${ref.space_id})`;
  }

  function connectionKindLabel(c) {
    const isLocalLocal = c.endpoint_space_a.type === 'LOCAL' && c.endpoint_space_b.type === 'LOCAL';
    return isLocalLocal ? 'LOCAL ↔ LOCAL' : 'LOCAL ↔ GLOBAL';
  }
  function connectionKindClass(c) {
    const isLocalLocal = c.endpoint_space_a.type === 'LOCAL' && c.endpoint_space_b.type === 'LOCAL';
    return isLocalLocal ? 'nodal-kind-local' : 'nodal-kind-global';
  }

  // Correspondence追加フォームの選択肢は、そのconnectionの宣言(endpoint_space_a/b)に
  // 一致するendpointだけに絞る(バックエンドは強制しないが、GUI上でLOCAL↔LOCALと
  // LOCAL↔GLOBALの取り違えを防ぐため)。
  function filterEndpointsForSide(ref) {
    if (ref.type === 'GLOBAL') return state.endpoints.filter(e => e.type === 'GLOBAL');
    return state.endpoints.filter(e => e.type === 'LOCAL' && e.space_id === ref.space_id);
  }

  async function fetchEndpoints() {
    const res = await fetch('/api/nodal-endpoints');
    if (!res.ok) throw new Error(`endpoint一覧の取得に失敗しました(status=${res.status})`);
    const data = await res.json();
    state.endpoints = data.endpoints || [];
  }

  async function fetchConnections(buildingId) {
    if (!buildingId) { state.connections = []; return; }
    const res = await fetch(`/api/nodal-connections?building_id=${encodeURIComponent(buildingId)}`);
    if (!res.ok) throw new Error(`connection一覧の取得に失敗しました(status=${res.status})`);
    const data = await res.json();
    state.connections = data.connections || [];
  }

  async function fetchResolutionResult(buildingId) {
    if (!buildingId) { state.resolutionResult = null; return; }
    const res = await fetch(`/api/spatial-resolution/results/${encodeURIComponent(buildingId)}`);
    if (res.status === 404) { state.resolutionResult = null; return; }
    if (!res.ok) throw new Error(`resolution結果の取得に失敗しました(status=${res.status})`);
    const data = await res.json();
    state.resolutionResult = data.result;
  }

  async function fetchLocalSpaces(buildingId) {
    if (!buildingId) { state.localSpaces = []; return; }
    const res = await fetch(`/api/buildings/${encodeURIComponent(buildingId)}/local-spaces`);
    if (!res.ok) throw new Error(`Local Space一覧の取得に失敗しました(status=${res.status})`);
    const data = await res.json();
    state.localSpaces = data.local_spaces || [];
  }

  // ================================================================
  // Local↔Local Connection作成 wizard
  // ================================================================

  /** Local Space A/Bのdropdownを再構築する。互いに相手側の選択中space_idを
   * 選択肢から除外する(condition: 同一space_idを両側に選択不可)。 */
  function renderWizardSpaceOptions() {
    const selA = document.getElementById('nodalWizardSpaceA');
    const selB = document.getElementById('nodalWizardSpaceB');
    const currentA = wizard.spaceA?.space_id ?? '';
    const currentB = wizard.spaceB?.space_id ?? '';

    const optionsFor = (excludeSpaceId) => state.localSpaces
      .filter(s => s.space_id !== excludeSpaceId)
      .map(s => `<option value="${escapeHtml(s.space_id)}">${escapeHtml(s.tokutei_code)} (${escapeHtml(s.space_id)})</option>`)
      .join('');

    selA.innerHTML = '<option value="">選択してください</option>' + optionsFor(currentB);
    selB.innerHTML = '<option value="">選択してください</option>' + optionsFor(currentA);
    selA.value = currentA;
    selB.value = currentB;
  }

  async function setupWizardViewerSide(side) {
    const space = side === 'A' ? wizard.spaceA : wizard.spaceB;
    const canvasEl = document.getElementById(`nodalWizardCanvas${side}`);
    const labelEl = document.getElementById(`nodalWizardLabel${side}`);
    const voxelToggle = document.getElementById(`nodalWizardShowVoxel${side}`);
    const fitBtn = document.getElementById(`nodalWizardFit${side}`);

    const prevViewer = side === 'A' ? wizard.viewerA : wizard.viewerB;
    if (prevViewer) prevViewer.dispose();
    canvasEl.innerHTML = '';
    voxelToggle.checked = false;
    voxelToggle.disabled = !space;
    fitBtn.disabled = !space;
    if (side === 'A') wizard.voxelLoadedA = false; else wizard.voxelLoadedB = false;

    if (!space) {
      if (side === 'A') wizard.viewerA = null; else wizard.viewerB = null;
      labelEl.textContent = '未選択';
      return;
    }

    labelEl.textContent = `${space.tokutei_code} (${space.space_id})`;
    const viewer = createSpacePickerViewer(canvasEl);
    if (side === 'A') wizard.viewerA = viewer; else wizard.viewerB = viewer;

    viewer.onPick((point) => {
      let spatialId;
      try {
        spatialId = worldPointToLocalSpatialId(point, space.coordinate_definition, space.zoom_level);
      } catch (e) {
        alert(`Local Spatial IDへの変換に失敗しました: ${e.message}`);
        return;
      }
      if (side === 'A') handleWizardPickA(point, spatialId); else handleWizardPickB(point, spatialId);
    });

    try {
      const manifest = await loadBaseMapManifest();
      const entry = resolveBaseMapEntryForSpace(manifest, space.space_id, space.tokutei_code);
      if (!entry) {
        labelEl.textContent += '(Base Mapが見つかりません)';
        return;
      }
      const { positions, colors } = await loadPointCloudFromURL(`./base_maps/${entry.file}`);
      viewer.setLayerPoints('base_map', positions, colors, { defaultColor: 0x8fa6b8, size: 0.015 });
      viewer.setPickTargetLayer('base_map');
      viewer.fitCameraToVisibleLayers();
    } catch (e) {
      console.warn('[nodal-panel] Base Mapの読み込みに失敗しました', e);
      labelEl.textContent += '(Base Map読み込み失敗)';
    }
  }

  async function toggleWizardVoxel(side, show) {
    const viewer = side === 'A' ? wizard.viewerA : wizard.viewerB;
    const space = side === 'A' ? wizard.spaceA : wizard.spaceB;
    if (!viewer || !space) return;
    if (!show) { viewer.setLayerVisible('voxels', false); return; }

    const loaded = side === 'A' ? wizard.voxelLoadedA : wizard.voxelLoadedB;
    if (loaded) { viewer.setLayerVisible('voxels', true); return; }

    const toggleEl = document.getElementById(`nodalWizardShowVoxel${side}`);
    try {
      const zoom = space.zoom_level;
      const metaRes = await fetch(`/api/local-spaces/${encodeURIComponent(space.space_id)}/spatial-voxels?zoom_level=${zoom}`);
      if (!metaRes.ok) throw new Error(`status=${metaRes.status}`);
      const posRes = await fetch(`/api/local-spaces/${encodeURIComponent(space.space_id)}/spatial-voxels/positions.bin?zoom_level=${zoom}`);
      if (!posRes.ok) throw new Error(`status=${posRes.status}`);
      const positions = new Float32Array(await posRes.arrayBuffer());
      viewer.setLayerPoints('voxels', positions, null, { defaultColor: 0x0f9d8f, size: 0.02 });
      if (side === 'A') wizard.voxelLoadedA = true; else wizard.voxelLoadedB = true;
    } catch (e) {
      alert(`Voxelの読み込みに失敗しました: ${e.message}`);
      toggleEl.checked = false;
    }
  }

  /** wizard step(A1→B1→A2→B2…)の視覚化。表示クラス・文言を組み立てるだけで、
   * どちら側が次か・pair番号が何かというロジック自体(wizard.turn/wizard.pending/
   * wizard.pairs.length)は一切変更しない。 */
  function renderWizardTurnIndicator() {
    const el = document.getElementById('nodalWizardTurnIndicator');
    const colA = document.getElementById('nodalWizardColA');
    const colB = document.getElementById('nodalWizardColB');
    const viewersEl = document.querySelector('.nodal-wizard-viewers');
    colA.classList.remove('active-turn');
    colB.classList.remove('active-turn');

    if (!wizard.spaceA || !wizard.spaceB) {
      viewersEl.classList.remove('wizard-active');
      el.innerHTML = `<span class="nodal-wizard-step-msg">Local Space A・Bを両方選択してください</span>`;
      return;
    }
    viewersEl.classList.add('wizard-active');

    const nextIndex = wizard.pending ? wizard.pending.index : wizard.pairs.length + 1;
    const doneChips = wizard.pairs.map((p, i) => `
      <span class="nodal-wizard-step-chip" style="background:${hex6(pickColorForIndex(i))}" title="Pair ${p.index}: 完了(選択済み)">${p.index}</span>
    `).join('');
    const pendingChip = wizard.pending
      ? `<span class="nodal-wizard-step-chip nodal-wizard-step-chip--pending" style="border-color:${hex6(pickColorForIndex(nextIndex - 1))}" title="Pair ${nextIndex}: A側のみ選択済み(次に選択する側)">${nextIndex}</span>`
      : '';

    if (wizard.turn === 'A') {
      colA.classList.add('active-turn');
      el.innerHTML = `
        <div class="nodal-wizard-step-chips">${doneChips}${pendingChip}</div>
        <div class="nodal-wizard-step-next">次: <span class="nodal-side-chip nodal-side-chip--a">A</span> <b>左側(A)</b>をクリック(pair ${nextIndex})</div>
      `;
    } else {
      colB.classList.add('active-turn');
      el.innerHTML = `
        <div class="nodal-wizard-step-chips">${doneChips}${pendingChip}</div>
        <div class="nodal-wizard-step-next">次: <span class="nodal-side-chip nodal-side-chip--b">B</span> <b>右側(B)</b>をクリック(pair ${nextIndex}、Aは選択済み)</div>
      `;
    }
  }

  /** correspondenceが最低2件(yaw+translationに必要な最小数)を満たしているかを
   * 自然に表示するだけの補助表示。閾値(2件)自体はupdateWizardCreateButton()と
   * 完全に同じ既存条件を参照するだけで、新しい判定は導入しない。 */
  function renderWizardMinPairsBadge() {
    const el = document.getElementById('nodalWizardMinBadge');
    if (!el) return;
    const n = wizard.pairs.length;
    if (n >= 2) {
      el.className = 'nodal-wizard-min-badge nodal-wizard-min-badge--ok';
      el.textContent = '最低2件を満たしています';
    } else {
      el.className = 'nodal-wizard-min-badge nodal-wizard-min-badge--pending';
      el.textContent = `あと${2 - n}件必要(最低2件)`;
    }
  }

  function renderWizardCorrTable() {
    const el = document.getElementById('nodalWizardCorrTable');
    document.getElementById('nodalWizardCorrCount').textContent = `${wizard.pairs.length}件`;
    renderWizardMinPairsBadge();
    if (wizard.pairs.length === 0) {
      el.innerHTML = `<tbody><tr><td class="nodal-empty">correspondenceがまだありません(Viewerをクリックして追加してください)</td></tr></tbody>`;
      return;
    }
    el.innerHTML = `
      <thead><tr><th></th><th>A側 Local Spatial ID</th><th>B側 Local Spatial ID</th><th></th></tr></thead>
      <tbody>
        ${wizard.pairs.map((p, i) => `
          <tr data-pair-index="${p.index}">
            <td><span class="nodal-wizard-swatch" style="background:${hex6(pickColorForIndex(i))}">${p.index}</span></td>
            <td class="nodal-mono">A${p.index}: ${escapeHtml(p.aSpatialId)}</td>
            <td class="nodal-mono">B${p.index}: ${escapeHtml(p.bSpatialId)}</td>
            <td><button class="nodal-btn nodal-btn-small nodal-btn-danger nodal-wizard-delete-pair-btn">削除</button></td>
          </tr>
        `).join('')}
      </tbody>
    `;
    el.querySelectorAll('.nodal-wizard-delete-pair-btn').forEach(btn => {
      btn.onclick = () => {
        const idx = parseInt(btn.closest('tr').dataset.pairIndex, 10);
        deleteWizardPair(idx);
      };
    });
  }

  function updateWizardCreateButton() {
    document.getElementById('nodalWizardCreateBtn').disabled = wizard.pairs.length < 2;
  }

  function handleWizardPickA(point, spatialId) {
    if (wizard.turn !== 'A') return; // 順序外のクリックは無視する(推測ペアリングはしない)
    const index = wizard.pairs.length + 1;
    wizard.pending = { index, aPoint: point, aSpatialId: spatialId };
    // Bが選ばれるまでは「選択済みだがpair未完成」であることが分かるよう、
    // 確定marker(setMarker、塗りつぶし球体)ではなくpending用のring表示にする
    // (marker自体の色の意味・pick処理は変更しない、見た目の状態表現のみ追加)。
    wizard.viewerA.setPendingMarker(point, pickColorForIndex(index - 1));
    wizard.turn = 'B';
    renderWizardTurnIndicator();
  }

  function handleWizardPickB(point, spatialId) {
    if (wizard.turn !== 'B' || !wizard.pending) return; // 順序外のクリックは無視する
    const { index, aPoint, aSpatialId } = wizard.pending;
    wizard.pairs.push({ index, aPoint, aSpatialId, bPoint: point, bSpatialId: spatialId });
    // A側のpending ringを、確定markerへ昇格させる。
    wizard.viewerA.clearPendingMarker();
    wizard.viewerA.setMarker(index, aPoint, pickColorForIndex(index - 1));
    wizard.viewerB.setMarker(index, point, pickColorForIndex(index - 1));
    wizard.pending = null;
    wizard.turn = 'A';
    renderWizardCorrTable();
    renderWizardTurnIndicator();
    updateWizardCreateButton();
  }

  /** correspondenceを1件削除する。番号を1から詰め直し、A/B両方のmarkerを
   * 現在の配列から完全に作り直すことで、番号・marker色・対応関係が
   * 削除後も崩れないことを保証する(部分的なmarker操作はしない)。 */
  function deleteWizardPair(index) {
    wizard.pairs = wizard.pairs.filter(p => p.index !== index).map((p, i) => ({ ...p, index: i + 1 }));
    wizard.pending = null;
    wizard.turn = 'A';
    wizard.viewerA?.clearAllMarkers();
    wizard.viewerB?.clearAllMarkers();
    wizard.viewerA?.clearPendingMarker();
    wizard.viewerB?.clearPendingMarker();
    wizard.pairs.forEach((p, i) => {
      const color = pickColorForIndex(i);
      wizard.viewerA?.setMarker(p.index, p.aPoint, color);
      wizard.viewerB?.setMarker(p.index, p.bPoint, color);
    });
    renderWizardCorrTable();
    renderWizardTurnIndicator();
    updateWizardCreateButton();
  }

  function resetWizardPicking() {
    wizard.pairs = [];
    wizard.pending = null;
    wizard.turn = 'A';
    wizard.viewerA?.clearAllMarkers();
    wizard.viewerB?.clearAllMarkers();
    wizard.viewerA?.clearPendingMarker();
    wizard.viewerB?.clearPendingMarker();
    renderWizardCorrTable();
    renderWizardTurnIndicator();
    updateWizardCreateButton();
  }

  document.getElementById('nodalWizardSpaceA').addEventListener('change', async (ev) => {
    wizard.spaceA = ev.target.value ? state.localSpaces.find(s => s.space_id === ev.target.value) : null;
    renderWizardSpaceOptions();
    await setupWizardViewerSide('A');
    resetWizardPicking();
  });
  document.getElementById('nodalWizardSpaceB').addEventListener('change', async (ev) => {
    wizard.spaceB = ev.target.value ? state.localSpaces.find(s => s.space_id === ev.target.value) : null;
    renderWizardSpaceOptions();
    await setupWizardViewerSide('B');
    resetWizardPicking();
  });
  document.getElementById('nodalWizardShowVoxelA').addEventListener('change', (ev) => toggleWizardVoxel('A', ev.target.checked));
  document.getElementById('nodalWizardShowVoxelB').addEventListener('change', (ev) => toggleWizardVoxel('B', ev.target.checked));
  // 「Fit View」は既存のfitCameraToVisibleLayers()を呼ぶだけ(新しいカメラ制御は追加しない)。
  document.getElementById('nodalWizardFitA').addEventListener('click', () => wizard.viewerA?.fitCameraToVisibleLayers());
  document.getElementById('nodalWizardFitB').addEventListener('click', () => wizard.viewerB?.fitCameraToVisibleLayers());
  // 「ピックをリセット」は既存のresetWizardPicking()(Local Space切替時と同じ処理)を
  // 手動で呼べるようにするだけで、wizard順序ロジック・correspondence配列の
  // source of truthは変更しない。
  document.getElementById('nodalWizardResetBtn').addEventListener('click', () => resetWizardPicking());

  document.getElementById('nodalWizardCreateBtn').onclick = async () => {
    const ctx = getContext();
    if (!ctx?.building_id || !wizard.spaceA || !wizard.spaceB) { alert('Local Space A・Bを選択してください'); return; }
    if (wizard.pairs.length < 2) { alert('correspondenceが最低2件必要です'); return; }

    const btn = document.getElementById('nodalWizardCreateBtn');
    const originalLabel = btn.textContent;
    btn.disabled = true; btn.textContent = '作成中…';
    try {
      const connRes = await fetch('/api/nodal-connections', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          building_id: ctx.building_id,
          endpoint_space_a: { type: 'LOCAL', space_id: wizard.spaceA.space_id },
          endpoint_space_b: { type: 'LOCAL', space_id: wizard.spaceB.space_id },
        }),
      });
      const connData = await connRes.json();
      if (!connRes.ok) throw new Error(connData.error || `status=${connRes.status}`);
      const connectionId = connData.connection.connection_id;

      for (const pair of wizard.pairs) {
        const epARes = await fetch('/api/nodal-endpoints', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: 'LOCAL', space_id: wizard.spaceA.space_id, local_spatial_id: pair.aSpatialId,
            label: `wizard A${pair.index}`,
          }),
        });
        const epAData = await epARes.json();
        if (!epARes.ok) throw new Error(epAData.error || `status=${epARes.status}`);

        const epBRes = await fetch('/api/nodal-endpoints', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: 'LOCAL', space_id: wizard.spaceB.space_id, local_spatial_id: pair.bSpatialId,
            label: `wizard B${pair.index}`,
          }),
        });
        const epBData = await epBRes.json();
        if (!epBRes.ok) throw new Error(epBData.error || `status=${epBRes.status}`);

        const corrRes = await fetch(`/api/nodal-connections/${encodeURIComponent(connectionId)}/correspondences`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ node_a_id: epAData.endpoint.endpoint_id, node_b_id: epBData.endpoint.endpoint_id }),
        });
        const corrData = await corrRes.json();
        if (!corrRes.ok) throw new Error(corrData.error || `status=${corrRes.status}`);
      }

      const estRes = await fetch(`/api/nodal-connections/${encodeURIComponent(connectionId)}/estimate`, { method: 'POST' });
      const estData = await estRes.json();
      if (!estRes.ok) throw new Error(estData.error || `status=${estRes.status}`);

      resetWizardPicking();
      await refresh();
      alert(`Connectionを作成しました(solution status: ${estData.connection?.solution?.status ?? '不明'})`);
    } catch (e) {
      alert(`Connectionの作成に失敗しました: ${e.message}`);
    } finally {
      btn.textContent = originalLabel;
      updateWizardCreateButton();
    }
  };

  // ================================================================
  // 既存: NodalEndpoint table / NodalConnection一覧 / Spatial Resolution
  // ================================================================

  function renderEndpointTable() {
    const el = document.getElementById('nodalEndpointTable');
    document.getElementById('nodalEndpointCount').textContent = `${state.endpoints.length}件`;
    if (state.endpoints.length === 0) {
      el.innerHTML = `<tbody><tr><td class="nodal-empty">endpointがまだありません</td></tr></tbody>`;
      return;
    }
    el.innerHTML = `
      <thead><tr><th>type</th><th>詳細</th><th>label</th><th>id</th><th></th></tr></thead>
      <tbody>
        ${state.endpoints.map(e => `
          <tr data-endpoint-id="${e.endpoint_id}">
            <td><span class="nodal-badge ${e.type === 'LOCAL' ? 'nodal-badge-local' : 'nodal-badge-global'}">${e.type}</span></td>
            <td class="nodal-mono">${escapeHtml(e.type === 'LOCAL' ? `${e.space_id} / ${e.local_spatial_id}` : e.global_spatial_id)}</td>
            <td>${escapeHtml(e.label || '')}</td>
            <td class="nodal-mono" title="${escapeHtml(e.endpoint_id)}">${shortId(e.endpoint_id)}</td>
            <td><button class="nodal-btn nodal-btn-small nodal-btn-danger nodal-delete-endpoint-btn">削除</button></td>
          </tr>
        `).join('')}
      </tbody>
    `;
    el.querySelectorAll('.nodal-delete-endpoint-btn').forEach(btn => {
      btn.onclick = async () => {
        const id = btn.closest('tr').dataset.endpointId;
        if (!window.confirm('このNodalEndpointを削除しますか?(参照しているcorrespondenceは残ったままになります)')) return;
        try {
          const res = await fetch(`/api/nodal-endpoints/${encodeURIComponent(id)}`, { method: 'DELETE' });
          if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `status=${res.status}`); }
          await refresh();
        } catch (e) {
          alert(`NodalEndpointの削除に失敗しました: ${e.message}`);
        }
      };
    });
  }

  function renderConnectionCard(c) {
    const sol = c.solution;
    const yawDeg = radToDeg(sol.yaw_rad);
    const translation = sol.translation ? sol.translation.map(v => v.toFixed(3)).join(', ') : '-';
    const corrRows = (c.correspondences || []).map(p => `
      <tr>
        <td class="nodal-mono">${shortId(p.pair_id)}</td>
        <td>${escapeHtml(describeEndpoint(endpointById(p.node_a_id)))}</td>
        <td>${escapeHtml(describeEndpoint(endpointById(p.node_b_id)))}</td>
      </tr>`).join('') || `<tr><td colspan="3" class="nodal-empty">correspondenceがまだありません</td></tr>`;

    const optionsA = filterEndpointsForSide(c.endpoint_space_a)
      .map(e => `<option value="${e.endpoint_id}">${escapeHtml(describeEndpoint(e))}</option>`).join('');
    const optionsB = filterEndpointsForSide(c.endpoint_space_b)
      .map(e => `<option value="${e.endpoint_id}">${escapeHtml(describeEndpoint(e))}</option>`).join('');

    return `
      <div class="nodal-connection-card" data-connection-id="${c.connection_id}">
        <div class="nodal-connection-head">
          <span class="nodal-kind-badge ${connectionKindClass(c)}">${connectionKindLabel(c)}</span>
          <span class="nodal-mono" title="${escapeHtml(c.connection_id)}">${shortId(c.connection_id)}</span>
          ${badge(sol.status)}
          <button class="nodal-btn nodal-btn-small nodal-estimate-btn">Estimate</button>
          <button class="nodal-btn nodal-btn-small nodal-btn-danger nodal-delete-conn-btn">削除</button>
        </div>
        <div class="nodal-connection-endpoints">A: ${escapeHtml(refLabel(c.endpoint_space_a))} &nbsp;↔&nbsp; B: ${escapeHtml(refLabel(c.endpoint_space_b))}</div>
        <div class="nodal-connection-solution">
          n_correspondences: ${sol.n_correspondences} / yaw: ${yawDeg != null ? yawDeg.toFixed(2) + '°' : '-'} /
          translation: ${translation} / rmse: ${sol.rmse_m != null ? sol.rmse_m.toFixed(4) + 'm' : '-'} /
          max_residual: ${sol.max_residual_m != null ? sol.max_residual_m.toFixed(4) + 'm' : '-'}
        </div>
        <table class="nodal-table nodal-subtable">
          <thead><tr><th>pair</th><th>A側</th><th>B側</th></tr></thead>
          <tbody>${corrRows}</tbody>
        </table>
        <div class="nodal-add-corr">
          <select class="nodal-corr-a">${optionsA || '<option value="">(対象endpointなし)</option>'}</select>
          <select class="nodal-corr-b">${optionsB || '<option value="">(対象endpointなし)</option>'}</select>
          <button class="nodal-btn nodal-btn-small nodal-add-corr-btn">correspondence追加</button>
        </div>
      </div>
    `;
  }

  function renderConnectionList() {
    document.getElementById('nodalConnectionCount').textContent = `${state.connections.length}件`;
    const el = document.getElementById('nodalConnectionList');
    if (state.connections.length === 0) {
      el.innerHTML = `<div class="nodal-empty">connectionがまだありません</div>`;
      return;
    }
    el.innerHTML = state.connections.map(renderConnectionCard).join('');

    el.querySelectorAll('.nodal-connection-card').forEach(card => {
      const connectionId = card.dataset.connectionId;

      card.querySelector('.nodal-estimate-btn').onclick = async () => {
        try {
          const res = await fetch(`/api/nodal-connections/${encodeURIComponent(connectionId)}/estimate`, { method: 'POST' });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || `status=${res.status}`);
          await refresh();
        } catch (e) {
          alert(`transform推定に失敗しました: ${e.message}`);
        }
      };

      card.querySelector('.nodal-delete-conn-btn').onclick = async () => {
        if (!window.confirm('このNodalConnectionを削除しますか?')) return;
        try {
          const res = await fetch(`/api/nodal-connections/${encodeURIComponent(connectionId)}`, { method: 'DELETE' });
          if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `status=${res.status}`); }
          await refresh();
        } catch (e) {
          alert(`NodalConnectionの削除に失敗しました: ${e.message}`);
        }
      };

      const addBtn = card.querySelector('.nodal-add-corr-btn');
      addBtn.onclick = async () => {
        const nodeA = card.querySelector('.nodal-corr-a').value;
        const nodeB = card.querySelector('.nodal-corr-b').value;
        if (!nodeA || !nodeB) { alert('A側・B側それぞれendpointを選択してください(候補が無ければ先にNodalEndpointを作成してください)'); return; }
        try {
          const res = await fetch(`/api/nodal-connections/${encodeURIComponent(connectionId)}/correspondences`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ node_a_id: nodeA, node_b_id: nodeB }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || `status=${res.status}`);
          await refresh();
        } catch (e) {
          alert(`correspondenceの追加に失敗しました: ${e.message}`);
        }
      };
    });
  }

  function renderComponentCard(c) {
    const lp = c.local_placement;
    const gr = c.global_resolution;
    const conflictsLp = (lp.conflicts || []).map(cf => `
      <li>space_id=${escapeHtml(cf.space_id)}: yaw差=${radToDeg(cf.yaw_diff_rad).toFixed(2)}°, 並進差=${cf.translation_diff_m.toFixed(3)}m</li>
    `).join('');
    const anchorEstimates = (gr.anchor_estimates || []).map(a => `
      <li>${shortId(a.connection_id)} (${escapeHtml(a.local_space_id)}): ${badge(a.fit_status)}${a.unresolvable_reason ? ` [${escapeHtml(a.unresolvable_reason)}]` : ''}</li>
    `).join('');
    const conflictsGr = (gr.conflicts || []).map(cf => `
      <li>${shortId(cf.connection_id_a)} vs ${shortId(cf.connection_id_b)}: yaw差=${radToDeg(cf.yaw_diff_rad).toFixed(2)}°, 並進差=${cf.translation_diff_m.toFixed(3)}m</li>
    `).join('');
    const globalTransform = gr.transform_root_to_global
      ? `yaw=${radToDeg(gr.transform_root_to_global.yaw_rad).toFixed(2)}°, translation=${gr.transform_root_to_global.translation.map(v => v.toFixed(3)).join(', ')}`
      : null;

    return `
      <div class="nodal-component-card">
        <div class="nodal-component-head">Component <span class="nodal-mono">${escapeHtml(c.component_id)}</span></div>
        <div>members: ${lp.member_space_ids.map(escapeHtml).join(', ')} / root: ${escapeHtml(lp.root_space_id)}</div>
        <div>Local placement: ${badge(lp.status)}</div>
        ${conflictsLp ? `<ul class="nodal-conflict-list">${conflictsLp}</ul>` : ''}
        <div>Global resolution: ${badge(gr.status)}${gr.target_epsg ? ` (EPSG:${gr.target_epsg})` : ''}</div>
        ${globalTransform ? `<div>root→global: ${globalTransform}</div>` : ''}
        ${anchorEstimates ? `<div>anchor推定:</div><ul class="nodal-conflict-list">${anchorEstimates}</ul>` : ''}
        ${conflictsGr ? `<div style="color:var(--status-conflict); font-weight:600;">Global conflict:</div><ul class="nodal-conflict-list">${conflictsGr}</ul>` : ''}
      </div>
    `;
  }

  function renderResolutionResult() {
    const el = document.getElementById('nodalResolutionResult');
    const result = state.resolutionResult;
    if (!result) {
      el.innerHTML = `<div class="nodal-empty">まだこのbuildingはresolveされていません。</div>`;
      return;
    }
    if (!result.components || result.components.length === 0) {
      el.innerHTML = `<div class="nodal-empty">component(LOCAL↔LOCAL connectionで繋がったLocal Space群)が1件もありません。` +
        `LOCAL↔GLOBALのconnection(anchor)は、既存のcomponentに付随して初めて解決されるため、` +
        `LOCAL↔LOCALのconnectionが1本も無い場合はここには何も表示されません(anchor自体のsolutionは上のNodalConnection一覧で確認できます)。</div>`;
      return;
    }
    el.innerHTML = `
      <p class="add-form-hint">resolved_at: ${escapeHtml(result.resolved_at)} / target_epsg: ${escapeHtml(result.target_epsg)}</p>
      ${result.components.map(renderComponentCard).join('')}
    `;
  }

  function updateEndpointFormVisibility() {
    const type = document.getElementById('nodalEndpointType').value;
    document.getElementById('nodalEndpointSpaceId').style.display = type === 'LOCAL' ? 'inline-block' : 'none';
    document.getElementById('nodalEndpointLocalSpatialId').style.display = type === 'LOCAL' ? 'inline-block' : 'none';
    document.getElementById('nodalEndpointGlobalSpatialId').style.display = type === 'GLOBAL' ? 'inline-block' : 'none';
  }
  document.getElementById('nodalEndpointType').onchange = updateEndpointFormVisibility;
  updateEndpointFormVisibility();

  document.getElementById('nodalEndpointCreateBtn').onclick = async () => {
    const type = document.getElementById('nodalEndpointType').value;
    const spaceId = document.getElementById('nodalEndpointSpaceId').value.trim();
    const localSpatialId = document.getElementById('nodalEndpointLocalSpatialId').value.trim();
    const globalSpatialId = document.getElementById('nodalEndpointGlobalSpatialId').value.trim();
    const label = document.getElementById('nodalEndpointLabel').value.trim();

    const body = { type };
    if (label) body.label = label;
    if (type === 'LOCAL') {
      if (!spaceId || !localSpatialId) { alert('LOCALエンドポイントには space_id と local_spatial_id が必須です'); return; }
      body.space_id = spaceId;
      body.local_spatial_id = localSpatialId;
    } else {
      if (!globalSpatialId) { alert('GLOBALエンドポイントには global_spatial_id が必須です'); return; }
      body.global_spatial_id = globalSpatialId;
    }

    const btn = document.getElementById('nodalEndpointCreateBtn');
    btn.disabled = true;
    try {
      const res = await fetch('/api/nodal-endpoints', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `status=${res.status}`);
      document.getElementById('nodalEndpointLocalSpatialId').value = '';
      document.getElementById('nodalEndpointGlobalSpatialId').value = '';
      document.getElementById('nodalEndpointLabel').value = '';
      await refresh();
    } catch (e) {
      alert(`NodalEndpointの作成に失敗しました: ${e.message}`);
    } finally {
      btn.disabled = false;
    }
  };

  function updateConnSideVisibility(side) {
    const typeSel = document.getElementById(`nodalConn${side}TypeSel`);
    const spaceInput = document.getElementById(`nodalConn${side}SpaceId`);
    spaceInput.style.display = typeSel.value === 'LOCAL' ? 'inline-block' : 'none';
  }
  document.getElementById('nodalConnATypeSel').onchange = () => updateConnSideVisibility('A');
  document.getElementById('nodalConnBTypeSel').onchange = () => updateConnSideVisibility('B');
  updateConnSideVisibility('A');
  updateConnSideVisibility('B');

  document.getElementById('nodalConnectionCreateBtn').onclick = async () => {
    const ctx = getContext();
    if (!ctx || !ctx.building_id) { alert('先にLocal Spaceを開いてください(building_idが必要です)'); return; }
    const aType = document.getElementById('nodalConnATypeSel').value;
    const bType = document.getElementById('nodalConnBTypeSel').value;
    const aSpaceId = document.getElementById('nodalConnASpaceId').value.trim();
    const bSpaceId = document.getElementById('nodalConnBSpaceId').value.trim();
    if (aType === 'LOCAL' && !aSpaceId) { alert('Endpoint AがLOCALの場合、space_idが必須です'); return; }
    if (bType === 'LOCAL' && !bSpaceId) { alert('Endpoint BがLOCALの場合、space_idが必須です'); return; }

    const body = {
      building_id: ctx.building_id,
      endpoint_space_a: { type: aType, space_id: aType === 'LOCAL' ? aSpaceId : null },
      endpoint_space_b: { type: bType, space_id: bType === 'LOCAL' ? bSpaceId : null },
    };
    const btn = document.getElementById('nodalConnectionCreateBtn');
    btn.disabled = true;
    try {
      const res = await fetch('/api/nodal-connections', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `status=${res.status}`);
      document.getElementById('nodalConnASpaceId').value = '';
      document.getElementById('nodalConnBSpaceId').value = '';
      await refresh();
    } catch (e) {
      alert(`NodalConnectionの作成に失敗しました: ${e.message}`);
    } finally {
      btn.disabled = false;
    }
  };

  document.getElementById('nodalResolveBtn').onclick = async () => {
    const ctx = getContext();
    if (!ctx || !ctx.building_id) { alert('先にLocal Spaceを開いてください(building_idが必要です)'); return; }
    const targetEpsgRaw = document.getElementById('nodalTargetEpsg').value.trim();
    const targetEpsg = targetEpsgRaw ? parseInt(targetEpsgRaw, 10) : undefined;

    const btn = document.getElementById('nodalResolveBtn');
    const originalLabel = btn.textContent;
    btn.disabled = true; btn.textContent = '実行中…';
    try {
      const body = { building_id: ctx.building_id };
      if (targetEpsg) body.target_epsg = targetEpsg;
      const res = await fetch('/api/spatial-resolution/resolve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `status=${res.status}`);
      state.resolutionResult = data.result;
      renderResolutionResult();
    } catch (e) {
      alert(`Spatial Resolutionの実行に失敗しました: ${e.message}`);
    } finally {
      btn.disabled = false; btn.textContent = originalLabel;
    }
  };

  async function refresh() {
    const myGeneration = ++refreshGeneration;
    const ctx = getContext();
    document.getElementById('nodalBuildingId').textContent = ctx?.building_id || '(未選択)';
    document.getElementById('nodalSpaceId').textContent = ctx?.space_id || '(未選択)';
    document.getElementById('nodalConnBuildingIdHint').textContent = ctx?.building_id || '(未選択)';

    const spaceIdInput = document.getElementById('nodalEndpointSpaceId');
    if (ctx?.space_id && !spaceIdInput.value) spaceIdInput.value = ctx.space_id;

    try { await fetchEndpoints(); } catch (e) { console.error('[nodal-panel]', e); }
    try { await fetchConnections(ctx?.building_id); } catch (e) { console.error('[nodal-panel]', e); }
    try { await fetchResolutionResult(ctx?.building_id); } catch (e) { console.error('[nodal-panel]', e); }
    try { await fetchLocalSpaces(ctx?.building_id); } catch (e) { console.error('[nodal-panel]', e); }

    // このrefresh()より後に開始した別のrefresh()が既にあれば、この呼び出しは
    // 古い(取得に時間がかかった)ものなので描画には反映しない(前述の対策)。
    if (myGeneration !== refreshGeneration) return;

    renderEndpointTable();
    renderConnectionList();
    renderResolutionResult();
    renderWizardSpaceOptions();
  }

  /** Integrated ViewのConnect Spaces(Stage 2、2026-09-05)から、
   * Local↔Local wizardのSpace A/Bを外部からpresetするための最小bridge。
   * 既存の#nodalWizardSpaceA/Bのchangeハンドラ(:572-583)と全く同じ
   * 手順(renderWizardSpaceOptions→setupWizardViewerSide→
   * resetWizardPicking)を呼ぶだけで、新しいconnection作成ロジックは
   * 一切追加しない。呼び出し側は、state.localSpacesが確実に埋まって
   * いる状態(refresh()のawait完了後)でこれを呼ぶこと。
   * 該当space_idがstate.localSpacesに無ければ(building不一致等)、
   * 既存のdropdown changeハンドラと同じくnullとして扱う(クラッシュしない)。 */
  async function presetWizardSpaces(spaceIdA, spaceIdB) {
    document.getElementById('nodalWizardSpaceA').value = spaceIdA || '';
    wizard.spaceA = spaceIdA ? state.localSpaces.find(s => s.space_id === spaceIdA) ?? null : null;
    document.getElementById('nodalWizardSpaceB').value = spaceIdB || '';
    wizard.spaceB = spaceIdB ? state.localSpaces.find(s => s.space_id === spaceIdB) ?? null : null;
    renderWizardSpaceOptions();
    await setupWizardViewerSide('A');
    await setupWizardViewerSide('B');
    resetWizardPicking();
  }

  refresh();
  return { refresh, presetWizardSpaces };
}

function injectNodalStyles() {
  if (document.getElementById('nodal-styles')) return;
  const style = document.createElement('style');
  style.id = 'nodal-styles';
  style.textContent = `
    .nodal-layout { display: flex; flex-direction: column; gap: 20px; padding: 20px; overflow-y: auto; flex: 1; min-height: 0; background: var(--bg); }
    .nodal-header { display: flex; gap: 24px; font-size: 12.5px; color: var(--text-dim); padding-bottom: 12px; border-bottom: 1px solid var(--border); }
    .nodal-header b { color: var(--text); font-weight: 600; }
    .nodal-section { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; }
    .nodal-section-title { font-size: 13.5px; font-weight: 700; color: var(--text); margin: 0 0 12px; display: flex; align-items: center; gap: 8px; }
    .nodal-count { font-size: 11px; color: var(--text-faint); font-weight: 500; }
    .nodal-form { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; margin-bottom: 10px; }
    .nodal-form input, .nodal-form select {
      font-size: 12.5px; font-family: inherit; padding: 6px 9px; border: 1px solid var(--border); border-radius: 6px;
      background: var(--panel); color: var(--text);
    }
    .nodal-form input:focus, .nodal-form select:focus { outline: none; border-color: var(--accent); }
    .nodal-form-side { display: flex; flex-direction: column; gap: 4px; }
    .nodal-form-side label { font-size: 10.5px; color: var(--text-faint); font-weight: 700; }
    .nodal-btn {
      border: 1px solid var(--border); background: var(--panel); color: var(--text);
      padding: 6px 13px; border-radius: 6px; font-size: 12.5px; cursor: pointer; font-family: inherit;
      transition: background 0.12s, border-color 0.12s;
    }
    .nodal-btn:hover:not(:disabled) { background: var(--accent-soft); border-color: var(--accent); }
    .nodal-btn:disabled { opacity: 0.5; cursor: default; }
    .nodal-btn-primary { background: var(--accent); color: white; border-color: var(--accent); font-weight: 600; }
    .nodal-btn-primary:hover:not(:disabled) { background: var(--accent-dark); }
    .nodal-btn-small { padding: 4px 10px; font-size: 11.5px; }
    .nodal-btn-danger { color: var(--danger); border-color: var(--danger-soft); }
    .nodal-btn-danger:hover:not(:disabled) { background: var(--danger-soft); border-color: var(--danger); }
    .nodal-table { width: 100%; border-collapse: collapse; font-size: 12px; }
    .nodal-table th { text-align: left; font-size: 10.5px; color: var(--text-faint); font-weight: 700; padding: 4px 8px; border-bottom: 1px solid var(--border); }
    .nodal-table td { padding: 6px 8px; border-bottom: 1px solid var(--border-soft); color: var(--text); vertical-align: middle; }
    .nodal-table-wrap { max-height: 240px; overflow-y: auto; border: 1px solid var(--border-soft); border-radius: 6px; }
    .nodal-mono { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px; color: var(--text-dim); }
    .nodal-empty { padding: 14px; text-align: center; color: var(--text-faint); font-size: 12px; }
    .nodal-badge { display: inline-block; font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 20px; letter-spacing: 0.02em; white-space: nowrap; }
    .nodal-badge-solved { background: var(--status-solved-soft); color: var(--status-solved); }
    .nodal-badge-warning { background: var(--status-warning-soft); color: var(--status-warning); }
    .nodal-badge-conflict { background: var(--status-conflict-soft); color: var(--status-conflict); }
    .nodal-badge-error { background: var(--status-error-soft); color: var(--status-error); }
    .nodal-badge-pending { background: var(--panel-raised); color: var(--text-dim); border: 1px solid var(--border); }
    .nodal-badge-local { background: var(--accent-soft); color: var(--accent-dark); }
    .nodal-badge-global { background: var(--status-info-soft); color: var(--status-info); }
    .nodal-kind-badge { font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 5px; letter-spacing: 0.01em; }
    .nodal-kind-local { background: var(--accent-soft); color: var(--accent-dark); }
    .nodal-kind-global { background: var(--status-info-soft); color: var(--status-info); }
    .nodal-connection-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; background: var(--bg); }
    .nodal-connection-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; flex-wrap: wrap; }
    .nodal-connection-endpoints { font-size: 12px; color: var(--text-dim); margin-bottom: 4px; }
    .nodal-connection-solution { font-size: 11.5px; color: var(--text-faint); margin-bottom: 8px; font-variant-numeric: tabular-nums; }
    .nodal-subtable { margin-bottom: 8px; background: var(--panel); border-radius: 6px; overflow: hidden; }
    .nodal-add-corr { display: flex; gap: 6px; align-items: center; }
    .nodal-component-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-top: 10px; font-size: 12.5px; color: var(--text-dim); line-height: 1.7; }
    .nodal-component-head { font-weight: 700; color: var(--text); margin-bottom: 4px; }
    .nodal-conflict-list { margin: 4px 0 4px 18px; padding: 0; font-size: 11.5px; color: var(--status-conflict); }

    /* 主操作(wizard)より視覚的に弱くする: 破線・淡色・通常ウェイトの見出し
       (2026-09-03、visual design改善)。折りたたみを開いても中身は既存の
       .nodal-sectionスタイルのまま(手順・機能は無変更)。 */
    .nodal-advanced { border: 1px dashed var(--border-soft); border-radius: var(--radius); padding: 4px 12px; background: transparent; }
    .nodal-advanced summary { cursor: pointer; padding: 8px 4px; font-size: 11.5px; font-weight: 500; color: var(--text-faint); }
    .nodal-advanced summary:hover { color: var(--text-dim); }
    .nodal-advanced[open] summary { margin-bottom: 4px; }
    .nodal-advanced .nodal-section { border: none; padding: 10px 4px 16px; }

    .nodal-resolution-section { border-color: var(--accent); }

    /* Local↔Local Connection作成wizardを、このタブの主操作として視覚的に
       強調する(2026-09-03)。 */
    .nodal-section--primary { border-left: 3px solid var(--accent); }

    /* Local Space A/Bの識別色(このwizard専用。共通design tokenの
       SOLVED/WARNING/CONFLICT/ERROR系とは別の「識別用」の2色で、
       どちらの状態色とも重ならない値を使う)。 */
    .nodal-layout { --nodal-side-a: var(--accent); --nodal-side-b: #9b6fd6; }

    .nodal-side-chip {
      display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px;
      border-radius: 4px; font-size: 10.5px; font-weight: 800; color: var(--text-inverse); flex-shrink: 0;
      vertical-align: middle;
    }
    .nodal-side-chip--a { background: var(--nodal-side-a); }
    .nodal-side-chip--b { background: var(--nodal-side-b); }

    .nodal-wizard-space-select { display: flex; gap: 20px; margin-bottom: 12px; }
    .nodal-wizard-space-col { display: flex; flex-direction: column; gap: 5px; flex: 1; }
    .nodal-wizard-space-col label { font-size: 10.5px; color: var(--text-faint); font-weight: 700; display: flex; align-items: center; gap: 6px; }
    .nodal-wizard-space-col select { font-size: 12.5px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel); color: var(--text); }

    /* wizard step(A1→B1→A2→B2…)の視覚化: 完了pairのchip列 + 次に選択する側 */
    .nodal-wizard-turn-indicator {
      display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px 16px;
      font-size: 12.5px; color: var(--text); background: var(--bg);
      border: 1px solid var(--border-soft); border-radius: 6px; padding: 9px 14px; margin-bottom: 12px;
    }
    .nodal-wizard-step-msg { color: var(--text-dim); }
    .nodal-wizard-step-chips { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
    .nodal-wizard-step-chip {
      display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px;
      border-radius: 50%; color: white; font-size: 10.5px; font-weight: 700; flex-shrink: 0;
      box-shadow: inset 0 0 0 1px var(--swatch-outline);
    }
    .nodal-wizard-step-chip--pending {
      background: transparent; border: 2px solid; color: var(--text); box-shadow: none;
    }
    .nodal-wizard-step-next { font-weight: 600; color: var(--text); white-space: nowrap; }
    .nodal-wizard-step-next b { font-weight: 700; }

    .nodal-wizard-viewers { display: flex; gap: 14px; margin-bottom: 14px; }
    .nodal-wizard-viewer-col {
      flex: 1; min-width: 0; position: relative; border: 2px solid transparent; border-radius: 8px;
      padding: 9px 8px 8px; transition: border-color 0.15s, opacity 0.15s;
    }
    .nodal-wizard-viewer-col::before {
      content: ""; position: absolute; top: 0; left: 8px; right: 8px; height: 3px; border-radius: 2px 2px 0 0;
    }
    .nodal-wizard-viewer-col--a::before { background: var(--nodal-side-a); }
    .nodal-wizard-viewer-col--b::before { background: var(--nodal-side-b); }
    .nodal-wizard-viewer-col--a.active-turn { border-color: var(--nodal-side-a); }
    .nodal-wizard-viewer-col--b.active-turn { border-color: var(--nodal-side-b); }
    /* 「次に選択する側」以外は少し沈める(選択済み/次に選択する側/未選択の区別)。
       両Local Spaceが選ばれるまでは適用しない(選択前に両方が薄く見えるのを防ぐ)。 */
    .nodal-wizard-viewers.wizard-active .nodal-wizard-viewer-col:not(.active-turn) { opacity: 0.7; }
    .nodal-wizard-viewer-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
    .nodal-wizard-viewer-label { font-size: 12px; font-weight: 700; color: var(--text); display: flex; align-items: center; gap: 6px; }
    .nodal-wizard-viewer-toolbar { display: flex; align-items: center; gap: 10px; }
    .nodal-wizard-voxel-toggle { font-size: 11px; color: var(--text-dim); white-space: nowrap; }
    .nodal-btn-ghost { background: transparent; border-color: var(--border-soft); color: var(--text-dim); }
    .nodal-btn-ghost:hover:not(:disabled) { background: var(--panel-raised); border-color: var(--border); color: var(--text); }
    .nodal-wizard-canvas { height: 320px; background: var(--bg-canvas); border-radius: 6px; position: relative; overflow: hidden; }
    .nodal-wizard-canvas canvas { display: block; }
    .nodal-wizard-canvas:empty::after {
      content: "Local Spaceを選択してください"; position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center; text-align: center; padding: 0 20px;
      color: var(--text-faint); font-size: 12px;
    }
    .nodal-wizard-correspondences-title {
      font-size: 12px; font-weight: 700; color: var(--text); margin-bottom: 8px;
      display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    }
    .nodal-wizard-correspondences-title .nodal-wizard-resetbtn-spacer { margin-left: auto; }
    #nodalWizardResetBtn { margin-left: auto; }
    .nodal-wizard-swatch {
      display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px;
      border-radius: 50%; color: white; font-size: 10px; font-weight: 700; box-shadow: inset 0 0 0 1px var(--swatch-outline);
    }
    .nodal-wizard-min-badge {
      font-size: 10.5px; font-weight: 600; padding: 2px 9px; border-radius: 20px; letter-spacing: 0.01em;
    }
    .nodal-wizard-min-badge--ok { background: var(--status-solved-soft); color: var(--status-solved); }
    .nodal-wizard-min-badge--pending { background: var(--status-pending-soft); color: var(--status-pending); border: 1px solid var(--border); }
  `;
  document.head.appendChild(style);
}
