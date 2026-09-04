/**
 * registration/rotation-preview-viewer.js
 *
 * Add Local Spaceウィザードのrotation設定ステップ専用の3D preview Viewer
 * (2026-09-03追加)。生成予定のCoordinateDefinitionが、Base Mapに対して
 * どの向き・どの範囲になるかを、サーバー往復無し(Base Map自体は一切変更
 * しない)でリアルタイムに確認できるようにする。
 *
 * 表示するもの:
 *   - Base Map(生ワールド座標、shared/display-coordinates.jsのみで表示。
 *     本Viewer(local_space_prototype.htmlのensureSpatialPointCloud)の
 *     Base Map表示と全く同じ見え方にする — rotationはここでは適用しない)。
 *   - Local X/Y/Z軸(shared/local-spatial-id.jsのlocalVectorToWorld、
 *     Structural Label・Spatial State・Nodal Spatial IDが使う座標系
 *     そのものの向き)。
 *   - 生成予定のbounds wireframe(shared/space-definition-bounds.jsの
 *     computeProvisionalBounds、space_definition_generator.pyのorigin/bounds
 *     算出とビット単位で一致することをテスト済み)。
 *
 * 【重要】上記2つの回転規約(Local軸 vs bounds)は数式が異なる、別の規約
 * (各shared moduleのdocstring参照)。ここではどちらも「既存の実装をそのまま
 * 呼ぶだけ」で、新しい回転式は一切書かない。
 *
 * registration/plane-viewer.js と同じThree.jsセットアップの考え方
 * (OrbitControls・shared/display-coordinates.jsによる表示座標変換)を
 * 踏襲した、ウィザード専用の独立実装。
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { toDisplayCoordinates } from "../shared/display-coordinates.js";
import { localVectorToWorld } from "../shared/local-spatial-id.js";
import { computeProvisionalBounds, BOUNDS_WIREFRAME_EDGES } from "../shared/space-definition-bounds.js";
import { VIEWER_BACKGROUND_COLOR } from "../shared/viewer-theme.js";

const AXIS_COLORS = { x: 0xe63946, y: 0x2a9d3f, z: 0x2f6fed }; // Three.jsのAxesHelper既定色と同系統
const BOUNDS_COLOR = 0xf5a623;

export function createRotationPreviewViewer(holder) {
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
  let axesGroup = null;
  let boundsObject = null;
  let rawPositions = null; // 生ワールド座標(Float32Array)、update()で使い回す
  let lastResult = null; // 直近のcomputeProvisionalBounds()結果(読み取り用)

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

  function toDisplayPoint(p) {
    return toDisplayCoordinates(p[0], p[1], p[2]);
  }

  function toDisplayBuffer(positions) {
    const n = positions.length / 3;
    const out = new Float32Array(positions.length);
    for (let i = 0; i < n; i++) {
      const [dx, dy, dz] = toDisplayCoordinates(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
      out[i * 3] = dx; out[i * 3 + 1] = dy; out[i * 3 + 2] = dz;
    }
    return out;
  }

  function clearPoints() {
    if (pointsObject) { scene.remove(pointsObject); pointsObject.geometry.dispose(); pointsObject.material.dispose(); pointsObject = null; }
  }
  function clearAxes() {
    if (axesGroup) {
      axesGroup.children.forEach((child) => { child.geometry?.dispose(); child.material?.dispose(); child.dispose?.(); });
      scene.remove(axesGroup);
      axesGroup = null;
    }
  }
  function clearBounds() {
    if (boundsObject) { scene.remove(boundsObject); boundsObject.geometry.dispose(); boundsObject.material.dispose(); boundsObject = null; }
  }

  /** Base Mapを差し替える(生ワールド座標、Detect Planes時に取得済みのものを
   * そのまま渡す想定。ここでは一切変換・変更しない)。 */
  function setBasePositions(positions) {
    rawPositions = positions;
    clearPoints();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(toDisplayBuffer(positions), 3));
    const mat = new THREE.PointsMaterial({ size: 0.02, color: 0x8fa6b8 });
    pointsObject = new THREE.Points(geo, mat);
    scene.add(pointsObject);
  }

  function drawAxes(origin, length) {
    clearAxes();
    axesGroup = new THREE.Group();
    const [ox, oy, oz] = toDisplayPoint(origin);
    const originVec = new THREE.Vector3(ox, oy, oz);
    const axisLen = Math.max(length * 0.35, 0.3);

    for (const [axisName, localDir] of [["x", [1, 0, 0]], ["y", [0, 1, 0]], ["z", [0, 0, 1]]]) {
      // Local軸方向は、生成後のLocal Spatial ID座標系(shared/local-spatial-id.js)と
      // 全く同じ式(localVectorToWorld)で、ワールド方向ベクトルへ変換する。
      const worldDir = localVectorToWorld(localDir, { rad: lastResult ? lastResult.rad : 0 });
      // 方向ベクトルなので、原点と(原点+方向)の2点をtoDisplayCoordinatesし、
      // その差分を表示座標系での方向として使う(座標変換は既存関数のみ使用)。
      const [tx, ty, tz] = toDisplayPoint([origin[0] + worldDir[0], origin[1] + worldDir[1], origin[2] + worldDir[2]]);
      const dir = new THREE.Vector3(tx - ox, ty - oy, tz - oz).normalize();
      const arrow = new THREE.ArrowHelper(dir, originVec, axisLen, AXIS_COLORS[axisName], axisLen * 0.18, axisLen * 0.1);
      axesGroup.add(arrow);
    }
    scene.add(axesGroup);
  }

  function drawBoundsWireframe(bounds) {
    clearBounds();
    const displayCorners = bounds.map((p) => toDisplayPoint(p));
    const vertices = [];
    for (const [i, j] of BOUNDS_WIREFRAME_EDGES) {
      vertices.push(...displayCorners[i], ...displayCorners[j]);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(vertices), 3));
    const mat = new THREE.LineBasicMaterial({ color: BOUNDS_COLOR, linewidth: 2 });
    boundsObject = new THREE.LineSegments(geo, mat);
    scene.add(boundsObject);
  }

  /**
   * 現在のBase Map(setBasePositions済み)に対して、rotationRad(ラジアン)で
   * Local軸・bounds wireframeを再計算・再描画する(リアルタイム更新用、
   * Base Map自体は一切変更しない)。
   * @returns {{degree:number, rad:number, bounds:number[][], origin:number[], length:number, height:number}}
   */
  function update(rotationRad) {
    if (!rawPositions) throw new Error("先にsetBasePositions()でBase Mapを設定してください。");
    const result = computeProvisionalBounds(rawPositions, rotationRad);
    lastResult = result;
    drawAxes(result.origin, result.length);
    drawBoundsWireframe(result.bounds);
    return result;
  }

  function getLastResult() {
    return lastResult;
  }

  function fitView() {
    const box = new THREE.Box3();
    let any = false;
    if (pointsObject) { pointsObject.geometry.computeBoundingBox(); if (pointsObject.geometry.boundingBox) { box.union(pointsObject.geometry.boundingBox); any = true; } }
    if (boundsObject) { boundsObject.geometry.computeBoundingBox(); if (boundsObject.geometry.boundingBox) { box.union(boundsObject.geometry.boundingBox); any = true; } }
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

  function dispose() {
    window.removeEventListener("resize", resize);
    clearPoints(); clearAxes(); clearBounds();
    renderer.dispose();
  }

  return { setBasePositions, update, getLastResult, fitView, dispose };
}
