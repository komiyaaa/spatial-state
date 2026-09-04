/**
 * plane-viewer.js
 *
 * 「ローカル空間を追加」フローの Detect Planes ステップ専用の、最小限の
 * 点群ビューワ。抽出したPlaneごとに点を色分けして表示し、リストで選択した
 * Planeをハイライトする(3Dクリック選択は実装しない。最低限の要求は
 * 「Viewer上で平面ごとに識別できる」「リストで選択して詳細を確認できる」)。
 *
 * registration-controller.js の createViewer() と同じThree.jsセットアップの
 * 考え方(OrbitControls・PointsMaterial・shared/display-coordinates.jsによる
 * 表示座標変換)を踏襲しているが、既存の追加モード(ラフレジ)用ビューワとは
 * 独立した、専用の小さい実装。
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { toDisplayCoordinates } from "../shared/display-coordinates.js";
import { VIEWER_BACKGROUND_COLOR } from "../shared/viewer-theme.js";

const UNASSIGNED_COLOR = [0.55, 0.55, 0.55];
const HIGHLIGHT_COLOR = [1.0, 0.85, 0.1];
const PALETTE = [
  [0.87, 0.29, 0.29], [0.29, 0.56, 0.87], [0.35, 0.75, 0.35], [0.87, 0.63, 0.13],
  [0.62, 0.35, 0.82], [0.13, 0.75, 0.75], [0.87, 0.42, 0.68], [0.55, 0.68, 0.13],
];

export function planeColor(index) {
  return PALETTE[index % PALETTE.length];
}

export function createPlaneViewer(holder) {
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
  let basePositions = null; // Float32Array(生の点群座標、表示座標に変換済み)
  let planeIndexOfPoint = null; // Int32Array(点ごとの所属plane index、-1=未分類)

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

  function colorForPlaneIndex(idx) {
    return idx == null || idx < 0 ? UNASSIGNED_COLOR : planeColor(idx);
  }

  function rebuildColors(highlightPlaneIndex) {
    const n = planeIndexOfPoint.length;
    const colors = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const pIdx = planeIndexOfPoint[i];
      const c = (highlightPlaneIndex != null && pIdx === highlightPlaneIndex) ? HIGHLIGHT_COLOR : colorForPlaneIndex(pIdx);
      colors[i * 3] = c[0]; colors[i * 3 + 1] = c[1]; colors[i * 3 + 2] = c[2];
    }
    return colors;
  }

  /**
   * @param {Float32Array} positions 生の点群座標(x,y,z を平坦化したもの)
   * @param {Int32Array|number[]} planeIndexPerPoint 各点が属するplaneの配列index(-1=未分類)
   */
  function setPointsWithPlanes(positions, planeIndexPerPoint) {
    if (pointsObject) { scene.remove(pointsObject); pointsObject.geometry.dispose(); pointsObject.material.dispose(); }
    basePositions = toDisplayBuffer(positions);
    planeIndexOfPoint = Int32Array.from(planeIndexPerPoint);

    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(basePositions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(rebuildColors(null), 3));
    const mat = new THREE.PointsMaterial({ size: 0.02, vertexColors: true });
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

  /** リストで選択したPlaneを、他より目立つ色でハイライトする。nullで解除。 */
  function highlightPlane(planeIndex) {
    if (!pointsObject) return;
    pointsObject.geometry.setAttribute("color", new THREE.BufferAttribute(rebuildColors(planeIndex), 3));
  }

  return { setPointsWithPlanes, highlightPlane, resize };
}
