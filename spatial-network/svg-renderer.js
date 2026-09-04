/**
 * spatial-network/svg-renderer.js
 *
 * Spatial Network ViewのDOM層。SVGシーンの構築・pan/zoom・node drag・
 * legend用の色/線種の一元管理・Export SVGを担当する。Network View
 * (force-layout.js)とLayout View(schematic-layout.js)のどちらも、
 * 「{nodes, edges}と、node id/edge idごとのposition・statusを渡せば
 * 描画する」という同じ`update()`だけを使う(レイアウト方式ごとに
 * 描画コードを分けない)。
 *
 * pan/zoomはSVGの`viewBox`操作のみで実装する(three.jsのOrbitControls等は
 * 使わない、2Dで完結)。node dragは、既存Viewer群の「動いたらpickしない」
 * 判定とは違い、ここでは意図的にドラッグそのものを検知する必要があるため、
 * PointerEvent + `setPointerCapture`を使う標準的な手法を用いる。
 *
 * Export SVGは、書き出したファイル単体でも正しい色で開けるよう、export時に
 * `--status-*`等のCSS変数を`getComputedStyle`でリテラル値へ解決してから
 * 書き込む(registration/pointcloud-io.jsのdownloadHandlerと同じ
 * Blob→<a download>パターンをそのまま流用する)。
 */

const SVG_NS = "http://www.w3.org/2000/svg";
const NODE_RADIUS = 14;
const GLOBAL_MARKER_HALF_SIZE = 11;

// node/edgeの色は共通design token(--status-*)を直接参照する
// (graph-status.jsのkind語彙とNodal Information/Integrated Viewの
// 視覚言語をそのまま統一する)。
const NODE_FILL_VAR = {
  global: "var(--status-solved)",
  local: "var(--status-info)",
  conflict: "var(--status-conflict)",
  pending: "var(--status-pending)",
  neutral: "var(--text-dim)",
};

const EDGE_STYLE_VAR = {
  solved: { stroke: "var(--status-solved)", dasharray: "", width: 2 },
  warning: { stroke: "var(--status-warning)", dasharray: "", width: 3 },
  error: { stroke: "var(--status-error)", dasharray: "6,4", width: 2 },
  pending: { stroke: "var(--status-pending)", dasharray: "2,3", width: 1.5 },
};

/**
 * @param {HTMLElement} holder
 * @returns {{
 *   update: (graph: {nodes:Array,edges:Array}, positions: Map, nodeStatus: Map, edgeStatus: Map) => void,
 *   fitView: (positions: Map, padding?: number) => void,
 *   onNodeDrag: (cb: (id:string,x:number,y:number)=>void) => void,
 *   onNodeDragEnd: (cb: (id:string)=>void) => void,
 *   exportSvg: (filename: string) => void,
 *   dispose: () => void,
 * }}
 */
export function createGraphRenderer(holder) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "sn-svg");
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "100%");
  svg.setAttribute("data-testid", "spatial-network-svg");
  svg.setAttribute("data-node-count", "0");
  svg.setAttribute("data-edge-count", "0");

  let viewBox = { x: -400, y: -300, w: 800, h: 600 };
  function applyViewBox() {
    svg.setAttribute("viewBox", `${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`);
  }
  applyViewBox();
  holder.appendChild(svg);

  const edgeLayer = document.createElementNS(SVG_NS, "g");
  edgeLayer.setAttribute("class", "sn-edge-layer");
  const nodeLayer = document.createElementNS(SVG_NS, "g");
  nodeLayer.setAttribute("class", "sn-node-layer");
  svg.appendChild(edgeLayer);
  svg.appendChild(nodeLayer);

  // --- pan/zoom(viewBox操作のみ) ---
  let panning = false;
  let panStartClient = null;
  let panStartViewBox = null;

  svg.addEventListener("pointerdown", (ev) => {
    if (ev.target !== svg) return; // ノード上のpointerdownはnode drag側が個別に処理する
    panning = true;
    panStartClient = [ev.clientX, ev.clientY];
    panStartViewBox = { ...viewBox };
  });
  window.addEventListener("pointermove", (ev) => {
    if (!panning) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const scaleX = viewBox.w / rect.width;
    const scaleY = viewBox.h / rect.height;
    viewBox.x = panStartViewBox.x - (ev.clientX - panStartClient[0]) * scaleX;
    viewBox.y = panStartViewBox.y - (ev.clientY - panStartClient[1]) * scaleY;
    applyViewBox();
  });
  window.addEventListener("pointerup", () => { panning = false; });
  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const zoomFactor = ev.deltaY > 0 ? 1.1 : 0.9;
    const mx = viewBox.x + ((ev.clientX - rect.left) / rect.width) * viewBox.w;
    const my = viewBox.y + ((ev.clientY - rect.top) / rect.height) * viewBox.h;
    viewBox.w *= zoomFactor;
    viewBox.h *= zoomFactor;
    viewBox.x = mx - ((ev.clientX - rect.left) / rect.width) * viewBox.w;
    viewBox.y = my - ((ev.clientY - rect.top) / rect.height) * viewBox.h;
    applyViewBox();
  }, { passive: false });

  function fitView(positions, padding = 80) {
    if (!positions || positions.size === 0) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of positions.values()) {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    }
    if (!Number.isFinite(minX)) return;
    viewBox = {
      x: minX - padding,
      y: minY - padding,
      w: Math.max(maxX - minX, 1) + padding * 2,
      h: Math.max(maxY - minY, 1) + padding * 2,
    };
    applyViewBox();
  }

  // --- node drag(PointerEvent + setPointerCapture) ---
  const dragMoveCallbacks = [];
  const dragEndCallbacks = [];
  function onNodeDrag(cb) { dragMoveCallbacks.push(cb); }
  function onNodeDragEnd(cb) { dragEndCallbacks.push(cb); }

  function screenToViewBoxPoint(ev) {
    const rect = svg.getBoundingClientRect();
    return {
      x: viewBox.x + ((ev.clientX - rect.left) / rect.width) * viewBox.w,
      y: viewBox.y + ((ev.clientY - rect.top) / rect.height) * viewBox.h,
    };
  }

  // --- 描画 ---
  function update(graph, positions, nodeStatus, edgeStatus) {
    edgeLayer.textContent = "";
    nodeLayer.textContent = "";

    svg.setAttribute("data-node-count", String(graph.nodes.length));
    svg.setAttribute("data-edge-count", String(graph.edges.length));

    const kindById = new Map(graph.nodes.map((n) => [n.id, n.kind]));

    for (const edge of graph.edges) {
      const p1 = positions.get(edge.sourceId);
      const p2 = positions.get(edge.targetId);
      if (!p1 || !p2) continue;
      const status = (edgeStatus && edgeStatus.get(edge.id)) || { kind: "pending" };
      const style = EDGE_STYLE_VAR[status.kind] || EDGE_STYLE_VAR.pending;
      const line = document.createElementNS(SVG_NS, "line");
      line.setAttribute("x1", String(p1.x));
      line.setAttribute("y1", String(p1.y));
      line.setAttribute("x2", String(p2.x));
      line.setAttribute("y2", String(p2.y));
      line.setAttribute("class", "sn-edge");
      line.setAttribute("data-edge-id", edge.id);
      line.setAttribute("data-source-id", edge.sourceId);
      line.setAttribute("data-target-id", edge.targetId);
      line.setAttribute("data-source-kind", kindById.get(edge.sourceId) || "");
      line.setAttribute("data-target-kind", kindById.get(edge.targetId) || "");
      line.setAttribute(
        "style",
        `stroke:${style.stroke}; stroke-width:${style.width}; stroke-dasharray:${style.dasharray};`,
      );
      edgeLayer.appendChild(line);
    }

    for (const node of graph.nodes) {
      const p = positions.get(node.id);
      if (!p) continue;
      const kind = ((nodeStatus && nodeStatus.get(node.id)) || { kind: "pending" }).kind;
      const fill = NODE_FILL_VAR[kind] || NODE_FILL_VAR.pending;

      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("class", "sn-node");
      g.setAttribute("data-node-id", node.id);
      g.setAttribute("data-node-kind", node.kind);
      g.setAttribute("data-status-kind", kind);
      g.setAttribute("transform", `translate(${p.x},${p.y})`);

      let shape;
      if (node.kind === "GLOBAL") {
        // GLOBALノードは専用マーカー形状(ひし形)。色は中立
        // (NodalEndpoint自体にRESOLVED/CONFLICTの概念が無いため)、
        // 個々のノードの区別はlabel(global_spatial_id)と接続関係で行う。
        shape = document.createElementNS(SVG_NS, "rect");
        const s = GLOBAL_MARKER_HALF_SIZE;
        shape.setAttribute("x", String(-s));
        shape.setAttribute("y", String(-s));
        shape.setAttribute("width", String(s * 2));
        shape.setAttribute("height", String(s * 2));
        shape.setAttribute("transform", "rotate(45)");
      } else {
        shape = document.createElementNS(SVG_NS, "circle");
        shape.setAttribute("r", String(NODE_RADIUS));
      }
      shape.setAttribute("style", `fill:${fill}; stroke:var(--panel); stroke-width:2;`);
      g.appendChild(shape);

      const label = document.createElementNS(SVG_NS, "text");
      label.textContent = node.kind === "GLOBAL"
        ? (node.label || node.id)
        : ((node.spaceRecord && node.spaceRecord.tokutei_code) || node.id);
      label.setAttribute("class", "sn-node-label");
      label.setAttribute("y", String(node.kind === "GLOBAL" ? GLOBAL_MARKER_HALF_SIZE + 14 : NODE_RADIUS + 14));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("style", "fill:var(--text); font-size:11px; font-family:var(--font-sans);");
      g.appendChild(label);

      let dragging = false;
      g.addEventListener("pointerdown", (ev) => {
        ev.stopPropagation();
        dragging = true;
        g.setPointerCapture(ev.pointerId);
      });
      g.addEventListener("pointermove", (ev) => {
        if (!dragging) return;
        const pt = screenToViewBoxPoint(ev);
        dragMoveCallbacks.forEach((cb) => cb(node.id, pt.x, pt.y));
      });
      const endDrag = () => {
        if (!dragging) return;
        dragging = false;
        dragEndCallbacks.forEach((cb) => cb(node.id));
      };
      g.addEventListener("pointerup", endDrag);
      g.addEventListener("pointercancel", endDrag);

      nodeLayer.appendChild(g);
    }
  }

  /** 現在のSVGを、CSS変数をリテラル色へ解決した上でダウンロードさせる
   * (書き出したファイル単体でもこのアプリのstylesheet無しで正しく開ける
   * ようにするため)。registration/pointcloud-io.jsのdownloadHandlerと
   * 同じBlob→<a download>パターン。 */
  function exportSvg(filename) {
    const clone = svg.cloneNode(true);
    const computed = getComputedStyle(document.documentElement);
    function resolveVars(styleText) {
      return styleText.replace(/var\((--[a-zA-Z0-9-]+)\)/g, (_, name) => {
        const value = computed.getPropertyValue(name).trim();
        return value || "#888888";
      });
    }
    clone.querySelectorAll("[style]").forEach((el) => {
      el.setAttribute("style", resolveVars(el.getAttribute("style")));
    });
    clone.setAttribute("xmlns", SVG_NS);
    const bg = computed.getPropertyValue("--bg-canvas").trim() || "#10141a";
    clone.setAttribute("style", `background:${bg};`);

    const svgText = new XMLSerializer().serializeToString(clone);
    const blob = new Blob([svgText], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function dispose() {
    svg.remove();
  }

  return { update, fitView, onNodeDrag, onNodeDragEnd, exportSvg, dispose, svgElement: svg };
}
