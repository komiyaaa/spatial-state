/**
 * spatial-network/three-network-renderer.js
 *
 * Spatial Network View「Network View」の3Dレンダラー(2026-09-25新規)。
 * 暗い3次元空間にLocal Space / Global anchorが天体のように浮遊し、
 * connectionが空間内を結ぶ、Obsidian graphの延長にあるconstellation的な
 * 表現。`spatial-network/svg-renderer.js`(Layout View用)と同じ形のAPI
 * (`update`/`fitView`/`onNodeDrag`/`onNodeDragEnd`/`dispose`)を持つ、
 * 既存Viewer群(`nodal/nodal-space-picker-viewer.js`等)と同じThree.js
 * セットアップパターンを踏襲した表示専用モジュール。
 *
 * 【重要: この3D位置は物理的な意味を持たない】
 * ここで計算・表示する座標は`force-layout.js`が出すtopology探索用の
 * visual layoutであり、Integrated Viewが表示するresolved global
 * placement(実座標)とは無関係。Nodal Information・Spatial Resolutionへの
 * 書き戻しは一切行わない(fetch/POSTなし、display-onlyのdragのみ)。
 * 「物理配置ではない」という注記チップ自体は、呼び出し側
 * (spatial-network-view.js)が既存の`.sn-status-chip`と同じDOM
 * オーバーレイパターンで表示する(このファイルの責務ではない)。
 *
 * 【identityとstatusのvisual channelを分離する(2026-09-25、ユーザー指示)】
 * Integrated Viewの「Local Space identity color != semantic/status
 * color」の原則をここでも踏襲する。
 * - LOCAL nodeのsphere本体の色 = Local Space identity color
 *   (`IDENTITY_COLOR_PALETTE`、integrated/integrated-view.jsの
 *   `SPACE_IDENTITY_COLOR_PALETTE`と同じ値を独立定義。既存の「値は
 *   揃えるがコードは共有しない」方針を踏襲)。
 * - statusはsphereとは別要素のhalo(billboard sprite)で表現する
 *   (`graph-status.js`のkindを`--status-*`から解決した色)。
 * - GLOBAL nodeは形状(octahedron)で区別し、色は中立固定・haloなし
 *   (NodalEndpointにstatus概念が無いため)。
 * - edgeの色はconnection status(edgeはLocal Space identityの対象では
 *   ないため、statusで塗ってよい)。
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { DragControls } from "three/addons/controls/DragControls.js";
import { VIEWER_BACKGROUND_COLOR } from "../shared/viewer-theme.js";

// Local Space識別色。integrated/integrated-view.jsのSPACE_IDENTITY_COLOR_
// PALETTEと同じ値(voxel/Spatial Stateの意味を持つ色とも、statusの
// --status-*色とも無関係の、識別専用パレット)。
const IDENTITY_COLOR_PALETTE = [
  0x4f9bd6, 0xe0a733, 0x9b6fd6, 0x5fc98a, 0xe2793d, 0x6cc7c1, 0xd67ab0, 0xa3b562,
];

function colorForIdentity(id, sortedLocalIds) {
  const idx = sortedLocalIds.indexOf(id);
  return IDENTITY_COLOR_PALETTE[(idx < 0 ? 0 : idx) % IDENTITY_COLOR_PALETTE.length];
}

const NODE_RADIUS = 10;
const GLOBAL_MARKER_RADIUS = 9;
// HALO_SCALE: sphere(半径10)との間に明確なgapを持つ外周ringを描くための
// spriteサイズ(2026-09-25、視認性改善)。旧実装(3.4倍=34)はring位置が
// sphere表面より内側に来てしまい、identity色とstatus色が同系統だと
// 溶け合って見えていたため拡大した。
const HALO_SCALE = 48;
const HALO_CANVAS = 128;
// リング中心の半径(canvas px)。sphere表面(canvas換算で約26.7px)から
// はっきり離れた位置(約42px)にリングを置き、間を透明なgapにする。
const HALO_RING_RADIUS_PX = 42;
const LABEL_Y_OFFSET = NODE_RADIUS + 16;

// statusのkindごとに、色だけでなくring自体の太さ・不透明度・破線でも
// 区別する(色が偶然同系統になっても形状で見分けられるようにするため)。
const RING_STYLE_BY_KIND = {
  global: { lineWidth: 8, opacity: 0.95, dash: null, blur: 6 },
  local: { lineWidth: 5, opacity: 0.75, dash: null, blur: 5 },
  conflict: { lineWidth: 7, opacity: 0.9, dash: [11, 7], blur: 6 },
  pending: { lineWidth: 3, opacity: 0.5, dash: [2, 5], blur: 3 },
};

// edge status kind → 見た目(色はCSS変数から解決、線種のみここで定義)。
// 2D版(svg-renderer.jsのEDGE_STYLE_VAR)と同じ対応。
const EDGE_LINE_STYLE = {
  solved: { dashed: false },
  warning: { dashed: false },
  error: { dashed: true },
  pending: { dashed: true },
};

function cssVarColor(name, fallbackHex) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallbackHex;
}

function makeTextSprite(text, colorCss) {
  // canvas解像度・フォント・sprite scaleを同じ比率(4:1)で拡大し、スクリーン
  // ショットでもLocal Space名を読みやすくしている(2026-09-25、visual polish)。
  const canvas = document.createElement("canvas");
  canvas.width = 320;
  canvas.height = 80;
  const ctx = canvas.getContext("2d");
  ctx.font = "bold 39px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 10;
  ctx.strokeStyle = "rgba(8,10,12,0.85)";
  ctx.strokeText(text, 160, 42);
  ctx.fillStyle = colorCss || "#ffffff";
  ctx.fillText(text, 160, 42);
  const texture = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(42, 10.5, 1);
  sprite.renderOrder = 999;
  return sprite;
}

/** statusを示すbillboard halo(常にカメラを向くため、3Dの角度に関わらず
 * 常にstatus色として読める。3D geometryのring/torusは角度によって
 * 細い線にしか見えなくなるため採用しない)。
 * sphere本体からgapを空けた外周のringとして描画し(塗りつぶし円盤では
 * なくstroke)、kindごとにring自体の太さ・不透明度・破線でも区別する。
 * これにより、identity色(sphere)とstatus色(halo)が偶然同系統でも、
 * 「sphereの塗り」と「その外側に離れて浮くring」が常に視覚的に分離される。 */
function makeHaloSprite(colorCss, kind) {
  const style = RING_STYLE_BY_KIND[kind] || RING_STYLE_BY_KIND.pending;
  const canvas = document.createElement("canvas");
  canvas.width = HALO_CANVAS;
  canvas.height = HALO_CANVAS;
  const ctx = canvas.getContext("2d");
  const c = new THREE.Color(colorCss);
  const rgb = `${Math.round(c.r * 255)},${Math.round(c.g * 255)},${Math.round(c.b * 255)}`;
  const center = HALO_CANVAS / 2;

  ctx.save();
  ctx.beginPath();
  ctx.arc(center, center, HALO_RING_RADIUS_PX, 0, Math.PI * 2);
  ctx.strokeStyle = `rgba(${rgb},${style.opacity})`;
  ctx.lineWidth = style.lineWidth;
  if (style.dash) ctx.setLineDash(style.dash);
  ctx.shadowColor = `rgba(${rgb},0.85)`;
  ctx.shadowBlur = style.blur;
  ctx.stroke();
  ctx.restore();

  const texture = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({ map: texture, depthTest: false, depthWrite: false, transparent: true });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(HALO_SCALE, HALO_SCALE, 1);
  sprite.renderOrder = 1;
  return sprite;
}

/** Network View全体の「暗い3次元空間に天体が浮遊するconstellation」演出
 * 用の、純粋に装飾的な背景star field。graphのnode/edgeとは無関係で
 * topology上の意味を一切持たないため(force-layout.js/schematic-layout.js
 * のnode位置に対する「決定的でなければならない」という制約の対象外)、
 * Math.randomをそのまま使用する。一度だけ生成し、rebuildScene()の対象には
 * しない(データ更新のたびに作り直さない)。 */
function createStarfield() {
  const STAR_COUNT = 700;
  const FIELD_RADIUS = 3000;
  const positions = new Float32Array(STAR_COUNT * 3);
  for (let i = 0; i < STAR_COUNT; i++) {
    const r = FIELD_RADIUS * (0.55 + Math.random() * 0.45);
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const mat = new THREE.PointsMaterial({
    color: 0x8a97ad, size: 3.2, sizeAttenuation: true,
    transparent: true, opacity: 0.55, depthWrite: false,
  });
  const points = new THREE.Points(geo, mat);
  points.renderOrder = -1;
  return points;
}

/**
 * @param {HTMLElement} holder
 * @returns {{
 *   update: (graph: {nodes:Array,edges:Array}, positions: Map, nodeStatus: Map, edgeStatus: Map) => void,
 *   fitView: (positions: Map) => void,
 *   onNodeDrag: (cb: (id:string,x:number,y:number,z:number)=>void) => void,
 *   onNodeDragEnd: (cb: (id:string)=>void) => void,
 *   getDebugSnapshot: () => Array<{id:string, kind:string, identityColor:string|null, statusColor:string|null}>,
 *   dispose: () => void,
 * }}
 */
export function createThreeNetworkRenderer(holder) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(VIEWER_BACKGROUND_COLOR);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 20000);
  camera.position.set(400, 260, 400);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.domElement.setAttribute("data-testid", "spatial-network-3d-canvas");
  renderer.domElement.setAttribute("data-node-count", "0");
  renderer.domElement.setAttribute("data-edge-count", "0");
  holder.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  // constellation演出のためAmbientLightをやや落とし、emissiveなsphere/halo
  // のコントラストを引き立てる(2026-09-25、visual polish)。
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const dir = new THREE.DirectionalLight(0xffffff, 0.55);
  dir.position.set(250, 400, 300);
  scene.add(dir);
  scene.add(createStarfield());

  const nodeGroup = new THREE.Group();
  const edgeGroup = new THREE.Group();
  scene.add(edgeGroup);
  scene.add(nodeGroup);

  let meshById = new Map(); // nodeId -> THREE.Mesh(sphere/octahedron)
  let relatedByMeshUuid = new Map(); // mesh.uuid -> {halo: Sprite|null, label: Sprite}
  let debugById = new Map(); // nodeId -> {kind, identityColor, statusColor}
  let dragControls = null;
  let lastGraphRef = null;

  const dragMoveCallbacks = [];
  const dragEndCallbacks = [];

  function resize() {
    const w = holder.clientWidth, h = holder.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  requestAnimationFrame(() => requestAnimationFrame(resize));

  let animating = true;
  function animate() {
    if (!animating) return;
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  function disposeObject(obj) {
    if (obj.geometry) obj.geometry.dispose();
    if (obj.material) {
      if (obj.material.map) obj.material.map.dispose();
      obj.material.dispose();
    }
  }

  function clearGroup(group) {
    for (const obj of group.children.slice()) {
      group.remove(obj);
      disposeObject(obj);
    }
  }

  function refreshEdgeGeometry() {
    for (const line of edgeGroup.children) {
      const a = meshById.get(line.userData.sourceId);
      const b = meshById.get(line.userData.targetId);
      if (!a || !b) continue;
      const posAttr = line.geometry.attributes.position;
      posAttr.setXYZ(0, a.position.x, a.position.y, a.position.z);
      posAttr.setXYZ(1, b.position.x, b.position.y, b.position.z);
      posAttr.needsUpdate = true;
      line.geometry.computeBoundingSphere();
      if (line.material.isLineDashedMaterial) line.computeLineDistances();
    }
  }

  function rebuildScene(graph, nodeStatus, edgeStatus) {
    if (dragControls) { dragControls.dispose(); dragControls = null; }
    clearGroup(nodeGroup);
    clearGroup(edgeGroup);
    meshById = new Map();
    relatedByMeshUuid = new Map();
    debugById = new Map();

    const localIds = graph.nodes.filter((n) => n.kind === "LOCAL").map((n) => n.id).sort();
    const neutralColor = cssVarColor("--text-dim", "#99a1a8");
    const statusColorByKind = {
      global: cssVarColor("--status-solved", "#34c785"),
      local: cssVarColor("--status-info", "#4f9bd6"),
      conflict: cssVarColor("--status-conflict", "#e2793d"),
      pending: cssVarColor("--status-pending", "#8a919a"),
    };

    for (const node of graph.nodes) {
      let mesh;
      let identityColorCss = null;
      let statusColorCss = null;

      if (node.kind === "GLOBAL") {
        const geo = new THREE.OctahedronGeometry(GLOBAL_MARKER_RADIUS);
        const mat = new THREE.MeshStandardMaterial({
          color: neutralColor, emissive: neutralColor, emissiveIntensity: 0.2,
          roughness: 0.55, metalness: 0.1,
        });
        mesh = new THREE.Mesh(geo, mat);
      } else {
        const identityHex = colorForIdentity(node.id, localIds);
        identityColorCss = `#${identityHex.toString(16).padStart(6, "0")}`;
        const geo = new THREE.SphereGeometry(NODE_RADIUS, 22, 16);
        const mat = new THREE.MeshStandardMaterial({
          color: identityHex, emissive: identityHex, emissiveIntensity: 0.4,
          roughness: 0.35, metalness: 0.1,
        });
        mesh = new THREE.Mesh(geo, mat);
      }
      mesh.userData.nodeId = node.id;
      mesh.userData.nodeKind = node.kind;
      nodeGroup.add(mesh);
      meshById.set(node.id, mesh);

      let halo = null;
      if (node.kind === "LOCAL") {
        const kind = ((nodeStatus && nodeStatus.get(node.id)) || { kind: "pending" }).kind;
        statusColorCss = statusColorByKind[kind] || statusColorByKind.pending;
        halo = makeHaloSprite(statusColorCss, kind);
        nodeGroup.add(halo);
      }

      const labelText = node.kind === "GLOBAL"
        ? (node.label || node.id)
        : ((node.spaceRecord && node.spaceRecord.tokutei_code) || node.id);
      const label = makeTextSprite(labelText, "#e7e9ea");
      nodeGroup.add(label);

      relatedByMeshUuid.set(mesh.uuid, { halo, label });
      debugById.set(node.id, { kind: node.kind, identityColor: identityColorCss, statusColor: statusColorCss });
    }

    const edgeColorByKind = {
      solved: cssVarColor("--status-solved", "#34c785"),
      warning: cssVarColor("--status-warning", "#e0a733"),
      error: cssVarColor("--status-error", "#e2555a"),
      pending: cssVarColor("--status-pending", "#8a919a"),
    };

    // 全edgeを(tree/非treeの区別なく、graph-model.jsが返す全件をconnection
    // statusのみで)同じ扱いで描く。schematic-layout.jsのspanning treeは
    // Layout View専用の座標計算補助であり、この3Dレンダラーには一切
    // 関与しない。
    for (const edge of graph.edges) {
      if (!meshById.has(edge.sourceId) || !meshById.has(edge.targetId)) continue;
      const status = (edgeStatus && edgeStatus.get(edge.id)) || { kind: "pending" };
      const style = EDGE_LINE_STYLE[status.kind] || EDGE_LINE_STYLE.pending;
      const color = edgeColorByKind[status.kind] || edgeColorByKind.pending;
      const geo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, 0),
      ]);
      const material = style.dashed
        ? new THREE.LineDashedMaterial({ color, dashSize: 6, gapSize: 4 })
        : new THREE.LineBasicMaterial({ color });
      const line = new THREE.Line(geo, material);
      line.userData.edgeId = edge.id;
      line.userData.sourceId = edge.sourceId;
      line.userData.targetId = edge.targetId;
      line.userData.sourceKind = meshById.get(edge.sourceId).userData.nodeKind;
      line.userData.targetKind = meshById.get(edge.targetId).userData.nodeKind;
      edgeGroup.add(line);
    }

    // dragの対象は全node(LOCAL/GLOBAL両方、2D版と同じくどちらもdisplay-only
    // でドラッグ可能にする)。
    const draggable = Array.from(meshById.values());
    dragControls = new DragControls(draggable, camera, renderer.domElement);
    dragControls.addEventListener("dragstart", () => { controls.enabled = false; });
    dragControls.addEventListener("dragend", (event) => {
      controls.enabled = true;
      const id = event.object.userData.nodeId;
      dragEndCallbacks.forEach((cb) => cb(id));
    });
    dragControls.addEventListener("drag", (event) => {
      const mesh = event.object;
      const related = relatedByMeshUuid.get(mesh.uuid);
      if (related) {
        if (related.halo) related.halo.position.copy(mesh.position);
        related.label.position.set(mesh.position.x, mesh.position.y + LABEL_Y_OFFSET, mesh.position.z);
      }
      refreshEdgeGeometry();
      const id = mesh.userData.nodeId;
      dragMoveCallbacks.forEach((cb) => cb(id, mesh.position.x, mesh.position.y, mesh.position.z));
    });
  }

  function applyPositions(positions) {
    for (const [id, mesh] of meshById) {
      const p = positions.get(id);
      if (!p) continue;
      mesh.position.set(p.x, p.y, p.z || 0);
      const related = relatedByMeshUuid.get(mesh.uuid);
      if (related) {
        if (related.halo) related.halo.position.copy(mesh.position);
        related.label.position.set(mesh.position.x, mesh.position.y + LABEL_Y_OFFSET, mesh.position.z);
      }
    }
    refreshEdgeGeometry();
  }

  /** svg-renderer.jsのcreateGraphRenderer().updateと同じ形のAPI。
   * graph自体(参照)が前回と同じなら位置更新のみ(force simulationの
   * tick毎の呼び出しで、毎回CanvasTexture等を作り直すコストを避けるため)、
   * 違えば(building切り替え・グラフ再取得時)フルに作り直す。 */
  function update(graph, positions, nodeStatus, edgeStatus) {
    renderer.domElement.setAttribute("data-node-count", String(graph.nodes.length));
    renderer.domElement.setAttribute("data-edge-count", String(graph.edges.length));

    if (graph !== lastGraphRef) {
      rebuildScene(graph, nodeStatus, edgeStatus);
      lastGraphRef = graph;
    }
    applyPositions(positions);
  }

  function fitView(positions) {
    if (!positions || positions.size === 0) return;
    const box = new THREE.Box3();
    let any = false;
    for (const p of positions.values()) {
      box.expandByPoint(new THREE.Vector3(p.x, p.y, p.z || 0));
      any = true;
    }
    if (!any) return;
    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    box.getCenter(center);
    box.getSize(size);
    // 小規模graph(3〜10 node程度)でも初期表示でtopologyが画面中央で
    // 一回り大きく見えるよう、カメラ距離のみを詰める(near/farの安全マージンは
    // 詰める前のradiusのまま計算し、クリッピングの余裕は変えない)
    // (2026-09-25、visual polish)。
    const radius = Math.max(size.length() / 2, 45);
    const camDistance = radius * 0.72;
    camera.position.set(center.x + camDistance, center.y + camDistance * 0.6, center.z + camDistance);
    controls.target.copy(center);
    camera.near = Math.max(0.1, radius * 0.01);
    camera.far = radius * 30;
    camera.updateProjectionMatrix();
    controls.update();
    resize();
  }

  function getDebugSnapshot() {
    return Array.from(debugById.entries()).map(([id, info]) => ({ id, ...info }));
  }

  function dispose() {
    window.removeEventListener("resize", resize);
    animating = false;
    if (dragControls) dragControls.dispose();
    controls.dispose();
    clearGroup(nodeGroup);
    clearGroup(edgeGroup);
    renderer.dispose();
    renderer.domElement.remove();
  }

  return {
    update, fitView,
    onNodeDrag(cb) { dragMoveCallbacks.push(cb); },
    onNodeDragEnd(cb) { dragEndCallbacks.push(cb); },
    getDebugSnapshot,
    dispose,
    domElement: renderer.domElement,
  };
}
