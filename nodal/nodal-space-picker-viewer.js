/**
 * nodal/nodal-space-picker-viewer.js
 *
 * Local↔Local NodalConnection作成UI(nodal-panel.js)専用の、クリックで
 * ワールド座標pointを取得できる3D Viewer。
 *
 * 【設計方針】
 * - Viewport構築・raycastクリック判定(ドラッグ誤検知対策込み)は、
 *   registration/registration-controller.js の createViewer() と同じ式を
 *   そのまま移植している(独自のraycast閾値・判定式は作らない)。
 * - 複数レイヤー(Base Map / voxel)を同時表示できる構造は、
 *   registration/registration-result-panel.js の createSimpleViewer() の
 *   レイヤー管理パターンを踏襲している。
 * - 座標変換はshared/display-coordinates.jsのtoDisplayCoordinates/
 *   fromDisplayCoordinatesのみを使い、新しい変換ロジックは作らない。
 * - Registration固有のロジック(edge-feature refinement・rigid-transform
 *   計算・preview/accept)は一切持ち込まない。ここでの役割は「pointを1つ
 *   取得すること」だけであり、correspondenceのペアリング・作成ロジックは
 *   呼び出し側(nodal-panel.js)が担う。
 * - marker は pair番号(1始まり)をキーに管理し、同じ番号のmarkerは
 *   setMarker()の再呼び出しで置き換えられる(削除→再構築で色・対応関係が
 *   崩れないようにするため、呼び出し側は「現在のcorrespondence配列」から
 *   毎回markerを作り直す想定)。
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { toDisplayCoordinates, fromDisplayCoordinates } from "../shared/display-coordinates.js";
import { VIEWER_BACKGROUND_COLOR } from "../shared/viewer-theme.js";

// Show All(registration-result-panel.js)と同じ固定パレットを流用し、
// correspondence番号ごとに識別しやすい色を割り当てる。
export const PICK_COLOR_PALETTE = [
  0xe6194b, 0x3cb44b, 0x4363d8, 0xf58231, 0x911eb4,
  0x46f0f0, 0xf032e6, 0xbcf60c, 0xfabebe, 0x008080,
  0xe6beff, 0x9a6324, 0x800000, 0x808000, 0x000075,
];

export function pickColorForIndex(i) {
  return PICK_COLOR_PALETTE[i % PICK_COLOR_PALETTE.length];
}

export function createSpacePickerViewer(holder) {
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
  const markers = new Map(); // pairIndex(number) -> {sphere, label}
  let pendingMarker = null; // Aのみ選択済み(B待ち)の状態を示すring(2026-09-03、visual design改善)
  let pickTargetLayer = "base_map"; // raycastの対象レイヤー名
  const pickCallbacks = [];

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

  function setLayerPoints(name, positions, colors, options = {}) {
    clearLayer(name);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(toDisplayBuffer(positions), 3));
    let mat;
    if (colors) {
      geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      mat = new THREE.PointsMaterial({ size: options.size ?? 0.02, vertexColors: true });
    } else {
      mat = new THREE.PointsMaterial({ size: options.size ?? 0.02, color: options.defaultColor ?? 0x8fa6b8 });
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

  function setLayerVisible(name, visible) {
    const obj = layers.get(name);
    if (obj) obj.visible = visible;
  }

  function setPickTargetLayer(name) {
    pickTargetLayer = name;
  }

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

  /** marker球体の上に、pair番号を書いた小さなラベル(CanvasTexture Sprite)を
   * 添える(2026-09-03、visual design改善)。correspondence一覧テーブルの
   * swatch(同じ番号・同じ色)と1対1対応させ、「どのmarkerがどのpair行か」を
   * 3D Viewer上でも一目で分かるようにするだけで、marker自体の色(colorHex、
   * pickColorForIndexの結果)やpick処理・座標変換には一切関与しない。 */
  function makeNumberSprite(number) {
    const canvas = document.createElement("canvas");
    canvas.width = 64; canvas.height = 64;
    const ctx = canvas.getContext("2d");
    ctx.font = "bold 40px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 7;
    ctx.strokeStyle = "rgba(10,12,14,0.85)";
    ctx.strokeText(String(number), 32, 34);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(String(number), 32, 34);
    const texture = new THREE.CanvasTexture(canvas);
    const mat = new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(0.09, 0.09, 1);
    sprite.renderOrder = 999; // 点群に埋もれず常に手前に見えるようにする(ラベルの可読性のみが目的)
    return sprite;
  }

  /** pair番号(1始まり)ごとにmarkerを置く/置き換える。同じ番号を再度呼べば
   * 既存markerを破棄して作り直す(呼び出し側は常に「今のcorrespondence配列」
   * からmarkerを作り直す想定なので、番号と色の対応が崩れることはない)。 */
  function setMarker(pairIndex, point, colorHex) {
    removeMarker(pairIndex);
    const geo = new THREE.SphereGeometry(0.035, 14, 14);
    const mat = new THREE.MeshBasicMaterial({ color: colorHex });
    const sphere = new THREE.Mesh(geo, mat);
    const [dx, dy, dz] = toDisplayCoordinates(point[0], point[1], point[2]);
    sphere.position.set(dx, dy, dz);
    scene.add(sphere);
    const label = makeNumberSprite(pairIndex);
    label.position.set(dx, dy + 0.075, dz);
    scene.add(label);
    markers.set(pairIndex, { sphere, label });
  }

  function removeMarker(pairIndex) {
    const entry = markers.get(pairIndex);
    if (!entry) return;
    scene.remove(entry.sphere);
    entry.sphere.geometry.dispose();
    entry.sphere.material.dispose();
    scene.remove(entry.label);
    entry.label.material.map.dispose();
    entry.label.material.dispose();
    markers.delete(pairIndex);
  }

  function clearAllMarkers() {
    for (const pairIndex of Array.from(markers.keys())) removeMarker(pairIndex);
  }

  /** A側のみ選択済み(B側クリック待ち)の状態を示す、確定markerとは別の
   * ring表示(2026-09-03)。correspondence配列にはまだ入っていない
   * 「選択済み/次に選択する側/未選択」の中間状態を視覚的に区別するためだけの
   * 表示ヘルパーで、pick処理・座標変換・correspondence source of truthには
   * 関与しない。 */
  function setPendingMarker(point, colorHex) {
    clearPendingMarker();
    const geo = new THREE.TorusGeometry(0.045, 0.008, 8, 28);
    const mat = new THREE.MeshBasicMaterial({ color: colorHex, transparent: true, opacity: 0.85 });
    const ring = new THREE.Mesh(geo, mat);
    const [dx, dy, dz] = toDisplayCoordinates(point[0], point[1], point[2]);
    ring.position.set(dx, dy, dz);
    ring.rotation.x = Math.PI / 2;
    scene.add(ring);
    pendingMarker = ring;
  }

  function clearPendingMarker() {
    if (!pendingMarker) return;
    scene.remove(pendingMarker);
    pendingMarker.geometry.dispose();
    pendingMarker.material.dispose();
    pendingMarker = null;
  }

  function clearAllLayers() {
    for (const name of Array.from(layers.keys())) clearLayer(name);
  }

  // クリック判定(ドラッグ誤検知対策込み)は
  // registration-controller.js の createViewer() と同じ式をそのまま使う。
  const raycaster = new THREE.Raycaster();
  raycaster.params.Points.threshold = 0.03;
  const mouse = new THREE.Vector2();
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
    if (moved > CLICK_MOVE_THRESHOLD_PX) return; // ドラッグ(カメラ回転)とみなし、pickはしない

    const target = layers.get(pickTargetLayer);
    if (!target || !target.visible) return;
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const hit = raycaster.intersectObject(target);
    if (hit.length > 0) {
      const [rx, ry, rz] = fromDisplayCoordinates(hit[0].point.x, hit[0].point.y, hit[0].point.z);
      pickCallbacks.forEach((cb) => cb([rx, ry, rz]));
    }
  });

  return {
    setLayerPoints, clearLayer, setLayerVisible, clearAllLayers,
    setPickTargetLayer, fitCameraToVisibleLayers,
    setMarker, removeMarker, clearAllMarkers,
    setPendingMarker, clearPendingMarker,
    onPick(cb) { pickCallbacks.push(cb); },
    dispose() { window.removeEventListener("resize", resize); clearPendingMarker(); renderer.dispose(); },
  };
}
