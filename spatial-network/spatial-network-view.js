/**
 * spatial-network/spatial-network-view.js
 *
 * Spatial Network View(2026-09-25新規)。Nodal Information
 * (NodalEndpoint/NodalConnection、source of truth)とSpatial Resolution
 * Result(derived data)を読み、Local Space・Nodal Connection・Global anchor
 * の関係を、Obsidian風のforce-directed「Network View」と、論文の図に
 * そのまま使える決定的な「Layout View」の2モードで可視化する。
 *
 * Integrated Viewと同格の、建物単位・表示専用画面(local_space_prototype.html
 * の`screenIntegrated`/`btnOpenIntegratedView`と同じ入口パターンで開く)。
 *
 * 【read-only】
 * このモジュールはGETのみを行う。POST /api/spatial-resolution/resolve や
 * NodalEndpoint/NodalConnectionの作成・削除・estimate等は一切呼ばない
 * (Nodal Information/Spatial Resolutionへの書き戻しは無い、Integrated View
 * と同じ姿勢)。
 *
 * 【データ取得】
 * - GET /api/buildings/<id>/local-spaces … 全Local Space(孤立ノードも含む)
 * - GET /api/nodal-connections?building_id=X … 建物内の全connection
 * - GET /api/nodal-endpoints … 全endpoint(building_idで絞れないため全件
 *   取得するが、実際に使うのはconnectionsのcorrespondencesが参照している
 *   ものだけ。graph-model.js内で解決する)
 * - GET /api/spatial-resolution/results/<id> … component配置・Global解決
 *   状況(404 = 未resolve、Integrated Viewと同じくnullとして許容する)
 *
 * グラフ構築は`graph-model.js`(GLOBAL anchorはendpoint_id単位で個別ノード、
 * 集約しない)、色分類は`graph-status.js`、Network Viewの物理演算は
 * `force-layout.js`が担当する。
 *
 * 【2026-09-25: Network Viewの3D化】
 * Network Viewのみ、暗い3次元空間にnodeが浮遊するconstellation的な
 * 表現(`three-network-renderer.js`、Three.js)へ変更した。Layout Viewは
 * 引き続き`svg-renderer.js`(SVG、論文用途のベクター書き出しに必要)の
 * まま。両方のrendererを`#snCanvasHolder`に最初から用意しておき、mode
 * 切替時はCSSのdisplay切替のみで済ませる(3Dシーンの再構築コストは
 * Network View再入場のたびに払うが、position自体に永続的な意味は無い
 * ため許容する)。Export SVGはLayout View選択中のみ有効(3D WebGLシーンを
 * SVGとして書き出すことは意味を持たないため)。
 */
import { buildGraphModel } from "./graph-model.js";
import { classifyNodes, classifyEdge } from "./graph-status.js";
import { createForceSimulation } from "./force-layout.js";
import { computeSchematicLayout } from "./schematic-layout.js";
import { createGraphRenderer } from "./svg-renderer.js";
import { createThreeNetworkRenderer } from "./three-network-renderer.js";

const DTW_BADGE_KIND = { global: "solved", local: "info", conflict: "conflict", pending: "pending" };

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (res.status === 404) return { notFound: true };
  if (!res.ok) throw new Error(`${url} (status=${res.status})`);
  return { data: await res.json() };
}

/**
 * @param {HTMLElement} container
 * @returns {{open: (buildingId: string) => Promise<void>}}
 */
export function initSpatialNetworkView(container) {
  container.innerHTML = `
    <div class="sn-workbench">
      <div class="sn-header">
        <div class="sn-header-main">
          <span class="sn-header-label">Spatial Network View</span>
          <span class="sn-header-building" id="snHeaderBuilding"></span>
        </div>
        <div class="dtw-tabs" id="snModeTabs">
          <button type="button" class="dtw-tab dtw-tab--active" data-mode="network">Network View</button>
          <button type="button" class="dtw-tab" data-mode="layout">Layout View</button>
        </div>
        <button type="button" class="dtw-btn dtw-btn--small" id="snExportBtn">Export SVG</button>
      </div>
      <div class="sn-body">
        <div class="sn-canvas-wrap" id="snCanvasHolder">
          <div class="sn-status-chip sn-status-chip--empty" id="snStatus">building未選択</div>
          <div class="sn-topology-notice" id="snTopologyNotice" style="display:none;">Topology layout only — not a resolved physical placement</div>
          <div class="sn-legend" id="snLegend"></div>
        </div>
        <div class="sn-side">
          <div class="sn-side-section">
            <div class="sn-side-section-title">Components<span class="sn-count" id="snComponentCount"></span></div>
            <div id="snComponentList"></div>
          </div>
          <div class="sn-side-section">
            <div class="sn-side-section-title">未表示connection<span class="sn-count" id="snSkippedCount"></span></div>
            <div id="snSkippedList" class="sn-hint"></div>
          </div>
        </div>
      </div>
    </div>
  `;
  injectStyles();
  renderLegend();

  const canvasHolder = document.getElementById("snCanvasHolder");
  const statusEl = document.getElementById("snStatus");
  const topologyNoticeEl = document.getElementById("snTopologyNotice");
  const exportBtn = document.getElementById("snExportBtn");

  // 両方のrendererを最初から用意し、mode切替はCSSのdisplay切替のみで行う
  // (3Dシーンの再構築は許容するが、DOM要素自体の付け外しは繰り返さない)。
  const svgRenderer = createGraphRenderer(canvasHolder);
  const threeRenderer = createThreeNetworkRenderer(canvasHolder);
  svgRenderer.svgElement.style.display = "none";
  threeRenderer.domElement.style.display = "none";
  exportBtn.disabled = true;

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = `sn-status-chip sn-status-chip--${kind}`;
  }

  // E2E検証用のデバッグフック(integrated/integrated-view.jsの
  // window.__integratedViewLastResultと同じ位置づけ。編集機能ではない)。
  // identity色とstatus色が別チャンネルであることを外部から確認できるようにする。
  window.__spatialNetworkViewDebug = {
    getNodeDebugInfo: () => threeRenderer.getDebugSnapshot(),
  };

  let currentGraph = null;
  let currentResolutionResult = null;
  let currentNodeStatus = new Map();
  let currentEdgeStatus = new Map();
  let currentBuildingId = null;
  let currentMode = "network";
  let simulation = null;
  let animationFrameId = null;
  let openGeneration = 0;

  function stopLoop() {
    if (animationFrameId != null) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }
  }

  /** simulationが停まっていれば、settledになるまでtick()し続ける
   * requestAnimationFrameループを(再)開始する。Network View選択時の
   * 初回起動と、node dragの解放後の再収束(releaseNode()がsettledを
   * falseに戻す)の両方から呼ばれる。 */
  function runLoopUntilSettled() {
    if (animationFrameId != null || !simulation) return;
    function step() {
      const { positions, settled } = simulation.tick();
      threeRenderer.update(currentGraph, positions, currentNodeStatus, currentEdgeStatus);
      if (!settled) {
        animationFrameId = requestAnimationFrame(step);
      } else {
        animationFrameId = null;
      }
    }
    animationFrameId = requestAnimationFrame(step);
  }

  function startNetworkView() {
    stopLoop();
    svgRenderer.svgElement.style.display = "none";
    threeRenderer.domElement.style.display = "block";
    topologyNoticeEl.style.display = "block";
    exportBtn.disabled = true;
    exportBtn.title = "Export SVGはLayout Viewでのみ利用できます(3DシーンはSVGとして書き出せません)";

    simulation = createForceSimulation(currentGraph.nodes, currentGraph.edges, {
      width: canvasHolder.clientWidth || 800,
      height: canvasHolder.clientHeight || 600,
    });
    const initialPositions = simulation.getPositions();
    threeRenderer.update(currentGraph, initialPositions, currentNodeStatus, currentEdgeStatus);
    threeRenderer.fitView(initialPositions);
    runLoopUntilSettled();
  }

  function showLayoutView() {
    stopLoop();
    threeRenderer.domElement.style.display = "none";
    svgRenderer.svgElement.style.display = "block";
    topologyNoticeEl.style.display = "none";
    exportBtn.disabled = false;
    exportBtn.title = "";

    const positions = computeSchematicLayout(currentGraph.nodes, currentGraph.edges, currentResolutionResult);
    svgRenderer.update(currentGraph, positions, currentNodeStatus, currentEdgeStatus);
    svgRenderer.fitView(positions);
  }

  function applyMode(mode) {
    currentMode = mode;
    container.querySelectorAll("#snModeTabs .dtw-tab").forEach((btn) => {
      btn.classList.toggle("dtw-tab--active", btn.dataset.mode === mode);
    });
    if (!currentGraph) return;
    if (mode === "network") startNetworkView();
    else showLayoutView();
  }

  container.querySelectorAll("#snModeTabs .dtw-tab").forEach((btn) => {
    btn.addEventListener("click", () => applyMode(btn.dataset.mode));
  });

  // node drag: Network View(3D)の時だけforce simulationへ反映する
  // (Layout Viewは決定的レイアウトのため、dragの効果を持たせない。
  // svg-renderer.jsのonNodeDrag/onNodeDragEndは意図的に配線しない)。
  threeRenderer.onNodeDrag((id, x, y, z) => {
    if (currentMode !== "network" || !simulation) return;
    simulation.setNodePosition(id, x, y, z);
  });
  threeRenderer.onNodeDragEnd((id) => {
    if (currentMode !== "network" || !simulation) return;
    simulation.releaseNode(id);
    runLoopUntilSettled();
  });

  exportBtn.addEventListener("click", () => {
    if (currentMode !== "layout") return; // Network View中はdisabledのため通常到達しない
    const filename = `spatial-network_${currentBuildingId || "building"}_layout.svg`;
    svgRenderer.exportSvg(filename);
  });

  function renderComponentList() {
    const el = document.getElementById("snComponentList");
    const countEl = document.getElementById("snComponentCount");
    const components = (currentResolutionResult && currentResolutionResult.components) || [];
    countEl.textContent = components.length > 0 ? `(${components.length})` : "";
    if (components.length === 0) {
      el.innerHTML = `<div class="sn-empty">${currentResolutionResult ? "componentがありません" : "まだSpatial Resolutionが実行されていません"}</div>`;
      return;
    }
    el.innerHTML = components.map((c) => {
      const memberIds = (c.local_placement && c.local_placement.member_space_ids) || [];
      const statusEntry = memberIds.length > 0 ? currentNodeStatus.get(memberIds[0]) : null;
      const kind = statusEntry ? statusEntry.kind : "pending";
      const dtwKind = DTW_BADGE_KIND[kind] || "pending";
      return `
        <div class="sn-component-row sn-component-row--${kind}">
          <div class="sn-component-row-head">
            <span class="sn-mono">${escapeHtml(c.component_id)}</span>
            <span class="dtw-badge dtw-badge--${dtwKind}">${escapeHtml(kind.toUpperCase())}</span>
          </div>
          <div class="sn-component-members">members: ${memberIds.map(escapeHtml).join(", ") || "―"}</div>
        </div>
      `;
    }).join("");
  }

  function renderSkippedList() {
    const el = document.getElementById("snSkippedList");
    const countEl = document.getElementById("snSkippedCount");
    const skipped = (currentGraph && currentGraph.skippedConnections) || [];
    countEl.textContent = skipped.length > 0 ? `(${skipped.length})` : "";
    el.textContent = skipped.length > 0
      ? `${skipped.length}件のLOCAL↔GLOBAL connectionが対応点未設定等のため未表示です。`
      : "";
  }

  async function open(buildingId) {
    const myGeneration = ++openGeneration;
    currentBuildingId = buildingId;
    setStatus("読み込み中…", "loading");
    document.getElementById("snHeaderBuilding").textContent = `Building: ${buildingId}`;
    stopLoop();
    simulation = null;

    try {
      const [spacesRes, connectionsRes, endpointsRes] = await Promise.all([
        fetchJson(`/api/buildings/${encodeURIComponent(buildingId)}/local-spaces`),
        fetchJson(`/api/nodal-connections?building_id=${encodeURIComponent(buildingId)}`),
        fetchJson(`/api/nodal-endpoints`),
      ]);
      if (myGeneration !== openGeneration) return;

      const localSpaces = spacesRes.data ? spacesRes.data.local_spaces : [];
      const connections = connectionsRes.data ? connectionsRes.data.connections : [];
      const endpoints = endpointsRes.data ? endpointsRes.data.endpoints : [];

      const resultRes = await fetchJson(`/api/spatial-resolution/results/${encodeURIComponent(buildingId)}`);
      if (myGeneration !== openGeneration) return;
      const resolutionResult = resultRes.notFound ? null : resultRes.data.result;

      const graph = buildGraphModel({ localSpaces, connections, endpoints });
      currentGraph = graph;
      currentResolutionResult = resolutionResult;
      currentNodeStatus = classifyNodes(graph.nodes, graph.edges, resolutionResult);
      currentEdgeStatus = new Map(graph.edges.map((e) => [e.id, classifyEdge(e)]));

      renderComponentList();
      renderSkippedList();

      if (graph.nodes.length === 0) {
        setStatus("表示できるLocal Spaceがありません。", "empty");
      } else {
        setStatus(`${graph.nodes.length}ノード / ${graph.edges.length}エッジを表示中`, "ok");
      }

      applyMode(currentMode);
    } catch (e) {
      if (myGeneration !== openGeneration) return;
      console.error("[spatial-network-view]", e);
      setStatus(`読み込みに失敗しました: ${e.message}`, "empty");
    }
  }

  return { open };
}

function renderLegend() {
  const el = document.getElementById("snLegend");
  el.innerHTML = `
    <div class="sn-legend-group">
      <div class="sn-legend-item"><span class="sn-legend-dot sn-legend-dot--global"></span>Global frame(RESOLVED)</div>
      <div class="sn-legend-item"><span class="sn-legend-dot sn-legend-dot--local"></span>Component-local frame</div>
      <div class="sn-legend-item"><span class="sn-legend-dot sn-legend-dot--conflict"></span>Conflict</div>
      <div class="sn-legend-item"><span class="sn-legend-dot sn-legend-dot--pending"></span>Unresolved</div>
      <div class="sn-legend-item"><span class="sn-legend-marker"></span>Global anchor</div>
    </div>
    <div class="sn-legend-group">
      <div class="sn-legend-item"><span class="sn-legend-line sn-legend-line--solved"></span>SOLVED</div>
      <div class="sn-legend-item"><span class="sn-legend-line sn-legend-line--warning"></span>WARNING</div>
      <div class="sn-legend-item"><span class="sn-legend-line sn-legend-line--error"></span>UNSOLVABLE</div>
      <div class="sn-legend-item"><span class="sn-legend-line sn-legend-line--pending"></span>UNSOLVED</div>
    </div>
  `;
}

function injectStyles() {
  if (document.getElementById("sn-styles")) return;
  const style = document.createElement("style");
  style.id = "sn-styles";
  style.textContent = `
    .sn-workbench { display: flex; flex-direction: column; flex: 1; min-height: 0; background: var(--bg); }
    .sn-header {
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
      padding: 10px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
      flex-shrink: 0; flex-wrap: wrap; row-gap: 6px;
    }
    .sn-header-main { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
    .sn-header-label { font-size: 12.5px; font-weight: 700; color: var(--text); letter-spacing: 0.01em; white-space: nowrap; }
    .sn-header-building { font-size: 11.5px; color: var(--text-dim); font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .sn-body { display: flex; flex: 1; min-height: 0; }

    .sn-canvas-wrap { flex: 1; position: relative; min-width: 0; margin: 12px 0 12px 12px; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; background: var(--bg-canvas); }
    .sn-svg { display: block; touch-action: none; cursor: grab; }
    .sn-node { cursor: grab; }

    .sn-status-chip {
      position: absolute; top: 12px; left: 12px; background: var(--panel-overlay);
      border: 1px solid var(--border); border-left: 3px solid var(--border-strong); border-radius: 7px;
      padding: 7px 13px; font-size: 11.5px; color: var(--text-dim); box-shadow: var(--shadow-sm);
      max-width: calc(100% - 24px); z-index: 2; pointer-events: none;
    }
    .sn-status-chip--loading { border-left-color: var(--text-faint); color: var(--text-dim); }
    .sn-status-chip--empty { border-left-color: var(--border-strong); color: var(--text-faint); }
    .sn-status-chip--ok { border-left-color: var(--status-solved); color: var(--status-solved); }

    /* Network View(3D)がresolved global placementではないことの明示。
       Integrated Viewの物理空間表示とは明確に区別する(2026-09-25)。 */
    .sn-topology-notice {
      position: absolute; top: 12px; right: 12px; background: var(--panel-overlay);
      border: 1px solid var(--border); border-radius: 7px; padding: 6px 12px;
      font-size: 10.5px; color: var(--text-faint); letter-spacing: 0.01em;
      max-width: 340px; text-align: right; z-index: 2; pointer-events: none;
    }

    .sn-legend {
      position: absolute; bottom: 12px; left: 12px; display: flex; gap: 18px;
      background: var(--panel-overlay); border: 1px solid var(--border); border-radius: 7px;
      padding: 8px 12px; font-size: 10.5px; color: var(--text-dim); box-shadow: var(--shadow-sm);
      z-index: 2; pointer-events: none; flex-wrap: wrap;
    }
    .sn-legend-group { display: flex; flex-direction: column; gap: 4px; }
    .sn-legend-item { display: flex; align-items: center; gap: 7px; white-space: nowrap; }
    .sn-legend-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; box-shadow: inset 0 0 0 1px var(--swatch-outline); }
    .sn-legend-dot--global { background: var(--status-solved); }
    .sn-legend-dot--local { background: var(--status-info); }
    .sn-legend-dot--conflict { background: var(--status-conflict); }
    .sn-legend-dot--pending { background: var(--status-pending); }
    .sn-legend-marker { width: 9px; height: 9px; background: var(--text-dim); transform: rotate(45deg); flex-shrink: 0; }
    .sn-legend-line { width: 20px; height: 0; border-top-width: 2px; border-top-style: solid; flex-shrink: 0; }
    .sn-legend-line--solved { border-color: var(--status-solved); }
    .sn-legend-line--warning { border-color: var(--status-warning); border-top-width: 3px; }
    .sn-legend-line--error { border-color: var(--status-error); border-top-style: dashed; }
    .sn-legend-line--pending { border-color: var(--status-pending); border-top-style: dotted; }

    .sn-side { width: 300px; flex-shrink: 0; padding: 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
    .sn-side-section { border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); padding: 12px 14px; }
    .sn-side-section-title {
      font-size: 11px; font-weight: 700; color: var(--text-faint); letter-spacing: 0.05em; text-transform: uppercase;
      margin-bottom: 10px; display: flex; align-items: baseline; gap: 6px;
    }
    .sn-count { font-size: 10.5px; font-weight: 500; color: var(--text-faint); letter-spacing: 0; text-transform: none; }
    .sn-hint { font-size: 11px; color: var(--text-faint); line-height: 1.6; }
    .sn-empty { padding: 4px 2px; color: var(--text-faint); font-size: 12px; }

    .sn-component-row {
      border: 1px solid var(--border-soft); border-left: 3px solid var(--border-strong); border-radius: 6px;
      padding: 7px 10px; margin-bottom: 6px; font-size: 11.5px; color: var(--text-dim); line-height: 1.6; background: var(--bg);
    }
    .sn-component-row:last-child { margin-bottom: 0; }
    .sn-component-row-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 3px; }
    .sn-component-row--global { border-left-color: var(--status-solved); }
    .sn-component-row--local { border-left-color: var(--status-info); }
    .sn-component-row--conflict { border-left-color: var(--status-conflict); }
    .sn-component-row--pending { border-left-color: var(--status-pending); }
    .sn-component-members { color: var(--text-faint); word-break: break-word; }
    .sn-mono { font-variant-numeric: tabular-nums; font-family: var(--font-mono); font-size: 11px; color: var(--text); }
  `;
  document.head.appendChild(style);
}
