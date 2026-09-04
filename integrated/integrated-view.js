/**
 * integrated/integrated-view.js
 *
 * Integrated Viewの最小実装(ロードマップPhase 3.10)。
 * Spatial Resolutionのderived result(/api/spatial-resolution/results/<building_id>、
 * ロードマップPhase 3.6で確立済み)を読み取り、複数のLocal Spaceのvoxel表示
 * (/api/local-spaces/<space_id>/spatial-voxels*、ロードマップStep 2〜4で確立済み)を
 * 同一の3Dシーンへ、Nodal Informationから導出されたtransformを適用して配置する。
 *
 * 【新しい座標推定ロジックは一切追加しない】
 * ここで行っているのは、既にbackendが計算済みの
 * RigidTransform2D(yaw_rad, translation)を、既存のvoxel座標
 * (world/provisional座標、spatial_id.local_spatial_id.resolve_provisional_world_center
 * が返す値と同じもの)に対して単純に適用するだけ。適用する前に、各Local Space
 * 自身のCoordinateDefinition(origin, rad)を使って、いったん座標系1
 * (intrinsic local physical coordinate、spatial_id.local_spatial_id.
 * resolve_local_center)へ戻す変換を挟む(Nodal correspondence/transform推定は
 * 座標系1で行われているため、座標系2のままではNodal transformを正しく
 * 適用できない)。この「座標系2→1」変換自体も、既存の
 * resolve_provisional_world_center()が使っている回転行列が対合
 * (M@M=単位行列)であることを利用した、既存ロジックの単純な逆変換の
 * クライアント側での再現であり、新しい推定・最適化ロジックではない。
 *
 * 【Nodal Informationをsource of truthとする/Spatial Resolution結果は
 * derived dataとして読むだけ】
 * このモジュールはPOST /api/spatial-resolution/resolve を一切呼ばない
 * (明示的なresolveはNodal Informationタブの責務のまま)。GET
 * /api/spatial-resolution/results/<building_id> で「直近の実行結果」を
 * 読み取って表示するだけであり、ここで新たに何かを解決・確定させることはない。
 *
 * 【component-local placementとglobal placementの区別】
 * - global_resolution.status === 'RESOLVED' の場合のみ、
 *   member_transforms_to_global(EPSG:6677などのmetric座標系)を使う。
 * - それ以外で local_placement.status === 'RESOLVED' の場合は、
 *   component-local frame(root_space_id基準、Globalとは無関係の相対座標)の
 *   transformsを使う。
 * - 両者は明確に別のframeであり、frameLabelとして常に画面上に区別して表示する
 *   (どちらの座標系で配置されているか、常にユーザーに分かるようにする)。
 *
 * 【fail-closed】
 * local_placement.status === 'CONFLICT'、または
 * global_resolution.status が 'GLOBAL_CONFLICT'/'BLOCKED_BY_LOCAL_CONFLICT' の
 * 場合、そのcomponentのmemberは一切描画しない(自動でどちらかの候補を選んで
 * 「解決されたかのように」表示することは絶対にしない)。該当componentは
 * サイドパネルに warning として一覧表示するだけに留める。
 *
 * 【provisional coordinate definitionをresolved placementとして扱わない】
 * Spatial Resolution結果に一切登場しない(=どのcomponentにも属さない)
 * Local Spaceは、たとえCoordinateDefinitionを持っていても描画しない
 * (「まだNodal Connectionが無い/resolveされていない」という状態を、
 * 「原点0・回転0で解決済み」であるかのように誤魔化して表示しない)。
 *
 * 【Spatial State表示について(2026-09-02追加)】
 * 「Spatial State(占有・消失状態)の統合表示、編集機能」は以前はスコープ外
 * だったが、今回Read Model経由での表示のみを追加した(編集機能は引き続き
 * スコープ外)。GET /api/spatial-state/<space_id>(backend/spatial_state_view.py
 * が返す安定した表示契約: presence/confidence/mobility)のみに依存し、
 * Spatial State Updaterの内部表現(state/confidence_flag/mu/kappa等)は
 * このファイルに一切登場しない。座標変換は既存のvoxel表示と全く同じ
 * パイプライン(world→intrinsic→Nodal transform→display)を再利用する。
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { toDisplayCoordinates } from "../shared/display-coordinates.js";
import { localSpatialIdToWorldCenter } from "../shared/local-spatial-id.js";
import { fetchSpatialState, colorForPresence, isRenderablePresence } from "../shared/spatial-state-client.js";
import { VIEWER_BACKGROUND_COLOR } from "../shared/viewer-theme.js";

const DEFAULT_COARSER_STEPS = 3; // finestからこの段数だけ粗いlevelを既定にする(表示の軽量化、較正前の初期値)
// local_space_prototype.htmlのSPATIAL_STATE_VOXEL_DISPLAY_RATIOと同じ値
// (隣接voxelを見分けやすくするための表示比率、物理サイズそのものではない)。
const SPATIAL_STATE_VOXEL_DISPLAY_RATIO = 0.92;

// 通常Voxelのmaterial parameter(2026-09-04の半透明化で採用した値)。
// selection機能から「未選択時の基準値」として再利用する、純粋なrendering
// parameterであり、Spatial State/confidence/statusの意味は持たない。
const VOXEL_OPACITY_DEFAULT = 0.55;
const VOXEL_EMISSIVE_INTENSITY_DEFAULT = 0.12;
const VOXEL_OPACITY_SELECTED = 0.85;
const VOXEL_EMISSIVE_INTENSITY_SELECTED = 0.35;
// 選択中Local Spaceのbounds強調に使う中立色(SPACE_IDENTITY_COLOR_PALETTEや
// Spatial State/status色とは無関係の、selection専用の固定色)。
const SELECTION_BOUNDS_COLOR = 0xffffff;
// pointerdown→pointerupの移動距離がこれを超えたらOrbitControlsによる
// 回転/パン操作とみなし、selectionのクリック判定を行わない。
const CLICK_DRAG_THRESHOLD_PX = 4;

// ================================================================
// Reality View / Local Workspace(2026-09-06)。
// 【重要】ここで新設するのは「presentation-only、client-onlyの表示位置」
// だけであり、Spatial Resolution・Nodal Information・backend/data model
// には一切影響しない(spatial-network/force-layout.jsと同じ位置づけ:
// fetchしない・書き戻さない・決定的)。GLOBAL(Reality View)は既存の
// member_transforms_to_globalをそのまま使うだけで新しい座標は増やさない。
// COMPONENT_LOCAL/UNPLACED/UNRESOLVED/CONFLICT(Local Workspace)は、
// 既存のlocal_placement.transforms(component内部の相対配置、real)は
// そのまま使い、「component同士・floating slot同士を重ねずに並べる」
// ためのoffsetだけをこのファイル内で新規に計算する。このoffsetの計算に
// CoordinateDefinition.originを使うことは無い(originはworldToIntrinsic
// の中で「差し引かれて消える」正規化にのみ使われる、既存の全spaceが通る
// 前処理であり、offset値そのものの入力にはならない)。
// ================================================================
const WORKSPACE_NOTICE_TEXT = "Presentation layout only — not a resolved physical placement";
// component/floating slot間の水平間隔(仮の初期値、較正前——実データを
// 見ながら調整する前提の値。spatial_id_design_memo等の他の「較正前」定数と
// 同じ位置づけ)。
const WORKSPACE_SLOT_SPACING = 80;
// floating slot(UNPLACED/UNRESOLVED/CONFLICT)のbounds wireframe色
// (Stage 1.5のsidebar badge色`--status-*`と同じ配色を3D側にも流用する。
// 新しい配色システムは作らない)。
const FLOATING_SLOT_STATUS_COLOR_VAR = {
  unplaced: "--status-pending",
  unresolved: "--status-warning",
  conflict: "--status-conflict",
};
// Connect Spaces(Stage 2)の候補集合は、既存の`isConnectCandidate()`が
// そのまま定義する('unplaced'/'unresolved'のみ)。floating slotが3D/
// sidebarで見える・選択できるようになっても、この候補集合は変更しない
// (2026-09-06ユーザー指示:「3Dで見える/選択できる」と「Connect Spacesの
// 候補にできる」は分離する)。

/** CSS custom property(design-tokens.css)を解決する(three-network-
 * renderer.js/nodal-panel.jsと同じパターン)。floating slotのbounds
 * wireframe色をStage 1.5のsidebar badge色と揃えるために使う。 */
function cssVarColor(name, fallbackHex) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallbackHex;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

/**
 * 座標系2(provisional/world, resolve_provisional_world_centerと同じ値)から、
 * そのLocal Space自身のCoordinateDefinition(origin, rad)を使って
 * 座標系1(intrinsic local physical coordinate)へ戻す。
 * spatial_id/local_spatial_id.pyのresolve_provisional_world_center()の
 * 数式をそのまま(順方向と同じ式を再適用すると逆変換になる、という既存の
 * 対合行列の性質を使って)クライアント側で再現しているだけで、新しい
 * 座標推定ロジックではない。
 */
function worldToIntrinsic(worldPoint, coordinateDefinition) {
  const [wx, wy, wz] = worldPoint;
  const origin = coordinateDefinition.origin;
  const relX = wx - origin[0];
  const relY = wy - origin[1];
  const localZ = wz - origin[2];
  const theta = coordinateDefinition.rad;
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);
  const localX = relX * cosT + relY * sinT;
  const localY = relX * sinT - relY * cosT;
  return [localX, localY, localZ];
}

/**
 * domain.transform.RigidTransform2D.apply()と同一の式
 * (R(yaw) = [[cos,-sin],[sin,cos]]、det=+1の真の回転のみ、scale/reflection無し)。
 */
function applyRigidTransform2D(yawRad, translation, point) {
  const [x, y, z] = point;
  const cosT = Math.cos(yawRad);
  const sinT = Math.sin(yawRad);
  return [
    cosT * x - sinT * y + translation[0],
    sinT * x + cosT * y + translation[1],
    z + translation[2],
  ];
}

/**
 * 1つのcomponentについて、fail-closedを含めた表示方針を決定する。
 * badgeKindは表示色の分類のみに使う追加フィールド(2026-09-03、Integrated View
 * visual design改善)。'global'/'local'/'conflict'/'pending'の4種で、
 * 「Global frame」「Component-local frame」「Conflict」「Unresolved」を
 * 一目で判別できるようにする。判定条件そのもの(mode算出ロジック)は無変更。
 * @returns {{mode: 'GLOBAL'|'COMPONENT_LOCAL'|'FAIL_CLOSED', badgeKind: 'global'|'local'|'conflict'|'pending', transforms?: object, frameLabel?: string, reason?: string}}
 */
function resolveComponentDisplayPlan(component) {
  const lp = component.local_placement;
  const gr = component.global_resolution;

  if (lp.status === "CONFLICT") {
    return { mode: "FAIL_CLOSED", badgeKind: "conflict", reason: `local placementがCONFLICT(component ${component.component_id})` };
  }
  if (gr.status === "GLOBAL_CONFLICT") {
    return { mode: "FAIL_CLOSED", badgeKind: "conflict", reason: `global resolutionがGLOBAL_CONFLICT(component ${component.component_id})` };
  }
  if (gr.status === "BLOCKED_BY_LOCAL_CONFLICT") {
    return { mode: "FAIL_CLOSED", badgeKind: "conflict", reason: `global resolutionがBLOCKED_BY_LOCAL_CONFLICT(component ${component.component_id})` };
  }
  if (gr.status === "RESOLVED") {
    return {
      mode: "GLOBAL",
      badgeKind: "global",
      transforms: gr.member_transforms_to_global,
      frameLabel: `GLOBAL (EPSG:${gr.target_epsg})`,
    };
  }
  if (lp.status === "RESOLVED") {
    // NO_ANCHOR / ANCHOR_UNRESOLVABLE / ANCHOR_INSUFFICIENT はここに来る
    // (Global未確定だが、component-local placement自体は正常に成立している)。
    return {
      mode: "COMPONENT_LOCAL",
      badgeKind: "local",
      transforms: lp.transforms,
      frameLabel: `COMPONENT-LOCAL (root=${lp.root_space_id})`,
    };
  }
  return { mode: "FAIL_CLOSED", badgeKind: "pending", reason: `local placementが${lp.status}` };
}

// Local Spaceの識別専用の固定パレット(voxel/Spatial Stateの意味を持つ色とは
// 無関係。3D Viewer内のbounds wireframeとサイドパネルの色ドットを同じ色で
// 対応させ、「どのメッシュがどのLocal Spaceか」を一目で分かるようにするだけ)。
const SPACE_IDENTITY_COLOR_PALETTE = [
  0x4f9bd6, 0xe0a733, 0x9b6fd6, 0x5fc98a, 0xe2793d, 0x6cc7c1, 0xd67ab0, 0xa3b562,
];

function colorForSpaceIdentity(spaceId, allSpaceIdsSorted) {
  const idx = allSpaceIdsSorted.indexOf(spaceId);
  return SPACE_IDENTITY_COLOR_PALETTE[(idx < 0 ? 0 : idx) % SPACE_IDENTITY_COLOR_PALETTE.length];
}

export function initIntegratedView(container) {
  container.innerHTML = `
    <div class="iv-workbench">
      <div class="iv-header">
        <div class="iv-header-main">
          <span class="iv-header-label">Spatial Digital Twin — Integrated View</span>
          <span class="iv-header-building" id="ivHeaderBuilding"></span>
        </div>
        <div class="iv-header-meta" id="ivResultMeta">―</div>
      </div>
      <div class="iv-view-tabs">
        <button type="button" class="iv-view-tab active" data-view="reality">Reality View</button>
        <button type="button" class="iv-view-tab" data-view="workspace">Local Workspace</button>
      </div>
      <div class="iv-body">
        <div class="iv-canvas-wrap">
          <div id="ivCanvasHolder"></div>
          <div class="iv-status-chip iv-status-chip--empty" id="ivStatus">building未選択</div>
          <div class="iv-workspace-notice" id="ivWorkspaceNotice" style="display:none">${escapeHtml(WORKSPACE_NOTICE_TEXT)}</div>
          <div class="iv-legend">
            <div class="iv-legend-item"><span class="iv-legend-dot iv-legend-dot--global"></span>Global frame(RESOLVED)</div>
            <div class="iv-legend-item"><span class="iv-legend-dot iv-legend-dot--local"></span>Component-local frame</div>
            <div class="iv-legend-item"><span class="iv-legend-dot iv-legend-dot--conflict"></span>Conflict</div>
            <div class="iv-legend-item"><span class="iv-legend-dot iv-legend-dot--pending"></span>Unresolved</div>
          </div>
        </div>
        <div class="iv-side">
          <div class="iv-side-section">
            <div class="iv-side-section-title">Controls</div>
            <label class="iv-toggle"><input type="checkbox" id="ivShowSpatialState"> Spatial State表示</label>
            <div id="ivSpatialStateStatus" class="iv-hint"></div>
          </div>
          <div class="iv-side-section" id="ivSelectedSpaceSection" style="display:none">
            <div class="iv-side-section-title">Selected Local Space</div>
            <div class="iv-mono" id="ivSelectedSpaceId"></div>
            <div class="iv-action-list" id="ivSelectedSpaceActions"></div>
          </div>
          <div class="iv-side-section" id="ivConnectSpacesSection" style="display:none">
            <div class="iv-side-section-title">Connect Spaces</div>
            <div id="ivConnectSpacesBody"></div>
          </div>
          <div class="iv-side-section">
            <div class="iv-side-section-title">Components<span class="iv-count" id="ivComponentCount"></span></div>
            <div id="ivComponentList"></div>
          </div>
          <div class="iv-side-section">
            <div class="iv-side-section-title">Local Space<span class="iv-count" id="ivSpaceCount"></span></div>
            <div id="ivSpaceList"></div>
          </div>
        </div>
      </div>
    </div>
  `;
  injectStyles();

  const canvasHolder = document.getElementById("ivCanvasHolder");
  const statusEl = document.getElementById("ivStatus");

  /** ivStatusの文字列表示に加えて、loading/empty/ok/conflictの4状態を
   * 色分けする(2026-09-03、Integrated View visual design改善)。
   * ステータス判定ロジック自体(何が表示され何がfail-closedになるか)は
   * 一切変更せず、既存に算出済みの結果をどう見せるかだけを変える。 */
  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = `iv-status-chip iv-status-chip--${kind}`;
  }

  // Reality View(GLOBAL、実EPSG座標)とLocal Workspace(COMPONENT_LOCAL/
  // UNPLACED/UNRESOLVED/CONFLICT、presentation-only)を、それぞれ独立した
  // Scene/Camera/OrbitControlsの組として持つ(2026-09-06)。WebGLの
  // コンテキストは1つ(renderer/canvasは共有)、renderループ・マウス操作の
  // 対象になる組をtab切り替えで入れ替える。
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  canvasHolder.appendChild(renderer.domElement);

  function createSubView() {
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(VIEWER_BACKGROUND_COLOR);
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.enabled = false;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x555044, 1.1));
    const dir = new THREE.DirectionalLight(0xffffff, 0.6);
    dir.position.set(10, 20, 10);
    scene.add(dir);
    // 半透明化したvoxelの陰影が平板にならないよう、主光源と逆方向・低強度の
    // 補助光を追加する(key+fillの2灯構成。2026-09-04、通常Voxel visual polish)。
    const fillDir = new THREE.DirectionalLight(0xffffff, 0.25);
    fillDir.position.set(-10, -6, -10);
    scene.add(fillDir);
    return {
      scene, camera, controls,
      meshes: [], // disposal対象(voxel InstancedMesh・bounds wireframe等)
      gridHelper: null,
    };
  }
  const realityView = createSubView();
  const workspaceView = createSubView();
  let viewMode = "reality"; // 'reality' | 'workspace'
  let activeView = realityView;
  realityView.controls.enabled = true;

  // 3DクリックによるLocal Space選択(2026-09-04)。「1 Local Space =
  // 1 InstancedMesh」構成のため、voxel単位のinstanceIdまでは見ず、
  // どのmeshに当たったかだけを見ればspace_idが特定できる
  // (local_space_prototype.htmlのonHover()と同じraycasting手順を踏襲、
  // ただしvoxel単位ではなくspace単位で扱う点が異なる)。
  const raycaster = new THREE.Raycaster();
  const pointerNdc = new THREE.Vector2();
  let pointerDownClient = null;
  renderer.domElement.addEventListener("pointerdown", (ev) => {
    pointerDownClient = { x: ev.clientX, y: ev.clientY };
  });
  renderer.domElement.addEventListener("pointerup", (ev) => {
    if (!pointerDownClient) return;
    const dx = ev.clientX - pointerDownClient.x;
    const dy = ev.clientY - pointerDownClient.y;
    pointerDownClient = null;
    // OrbitControlsによる回転/パン操作をクリックと誤認しないよう、
    // pointerdown→pointerupの移動量が閾値を超えたら選択処理を行わない。
    if (Math.hypot(dx, dy) > CLICK_DRAG_THRESHOLD_PX) return;

    const rect = renderer.domElement.getBoundingClientRect();
    pointerNdc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointerNdc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointerNdc, activeView.camera);
    // raycast対象は、現在表示中のview(Reality/Workspace)のspaceだけに
    // 絞る(非表示側のmeshをクリックしたことにしない)。
    const targets = Array.from(spaceEntryById.values())
      .filter((entry) => entry.view === activeView)
      .map((entry) => entry.mesh);
    const hits = raycaster.intersectObjects(targets, false);
    const hitSpaceId = hits.length > 0 ? hits[0].object.userData.spaceId : null;
    // Connect Spaces(2026-09-05、Stage 2)中は、3Dクリックの結果を
    // endpoint Bの選択へ回す(通常のselectedSpaceIdとは別のstate)。
    // normal single selectionとconnection pair selectionを混ぜない
    // ——同じraycasting結果を、mode次第でどちらか一方にだけ渡す。
    if (mode === "connect") {
      setConnectionEndpointB(hitSpaceId);
    } else {
      setSelectedSpace(hitSpaceId);
    }
  });

  function setViewMode(nextMode) {
    if (nextMode !== "reality" && nextMode !== "workspace") return;
    viewMode = nextMode;
    activeView = viewMode === "reality" ? realityView : workspaceView;
    realityView.controls.enabled = viewMode === "reality";
    workspaceView.controls.enabled = viewMode === "workspace";
    document.querySelectorAll(".iv-view-tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.view === viewMode);
    });
    const noticeEl = document.getElementById("ivWorkspaceNotice");
    if (noticeEl) noticeEl.style.display = viewMode === "workspace" ? "block" : "none";
    requestAnimationFrame(() => requestAnimationFrame(resize));
  }
  document.querySelectorAll(".iv-view-tab").forEach((tab) => {
    tab.addEventListener("click", () => setViewMode(tab.dataset.view));
  });

  // spaceId -> { mesh, min, max, view }。selection機能(3Dクリック選択、
  // 2026-09-04)専用の索引で、raycast対象の絞り込み・選択ハイライトの
  // material書き換え・選択bounds表示のために、voxel本体meshとbounding boxを
  // 束ねて保持する。viewはrealityView/workspaceViewのどちらに属するかの
  // 参照(2026-09-06)——選択ハイライトのBox3Helperを正しいsceneへ追加する
  // ために必要。meshes(disposal用配列)とは別で、bounds wireframe
  // (識別色helper)は含まない。
  let spaceEntryById = new Map();
  let selectedSpaceId = null;
  const spaceSelectedCallbacks = [];
  // 「Selected Local Space」セクションのactionボタン用(2026-09-05)。
  // Integrated View自身はここでnavigateしない——(actionId, spaceId)を
  // 呼び出し元(local_space_prototype.html)へそのまま渡すだけ。
  const spaceActionCallbacks = [];
  document.getElementById("ivSelectedSpaceSection").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".iv-action-btn[data-action]");
    if (!btn || !selectedSpaceId) return;
    if (btn.dataset.action === "connect") {
      // 「Connect Spaces」はnavigationではなく、Integrated View内部の
      // mode遷移(normal→connect)。呼び出し元(local_space_prototype.html)
      // へは何も渡さない(Stage 2、2026-09-05)。
      startConnectSpaces(selectedSpaceId);
      return;
    }
    spaceActionCallbacks.forEach((cb) => cb(btn.dataset.action, selectedSpaceId));
  });

  // ================================================================
  // Connect Spaces(Stage 2、2026-09-05)。normal single selectionと
  // connection pair selectionを混ぜない——mode='connect'の間、
  // selectedSpaceId(normal選択)は使わず、connectionEndpointA/Bという
  // 別のstateだけを使う。Integrated View自身はここでconnectionを
  // 一切作らない・遷移もしない(Continueで呼び出し元にA/Bを渡すだけ)。
  // ================================================================
  let mode = "normal"; // 'normal' | 'connect'
  let connectionEndpointA = null;
  let connectionEndpointB = null;
  const connectSpacesCallbacks = [];

  function updateConnectSpacesSection() {
    const sectionEl = document.getElementById("ivConnectSpacesSection");
    const bodyEl = document.getElementById("ivConnectSpacesBody");
    if (!sectionEl || !bodyEl) return;
    if (mode !== "connect") {
      sectionEl.style.display = "none";
      return;
    }
    sectionEl.style.display = "block";
    const bLine = connectionEndpointB
      ? `<div class="iv-mono">B: ${escapeHtml(connectionEndpointB)}</div>`
      : `<div class="iv-hint">B: 未選択(3D上のGLOBAL/COMPONENT_LOCAL、またはsidebarのUNPLACED/UNRESOLVEDから選択してください)</div>`;
    bodyEl.innerHTML = `
      <div class="iv-mono">A: ${escapeHtml(connectionEndpointA)}</div>
      ${bLine}
      <div class="iv-action-list">
        <button type="button" class="dtw-btn dtw-btn--primary iv-action-btn" id="ivConnectContinueBtn" ${connectionEndpointB ? "" : "disabled"}>Continue</button>
        <button type="button" class="dtw-btn iv-action-btn" id="ivConnectCancelBtn">Cancel</button>
      </div>
    `;
    document.getElementById("ivConnectContinueBtn").onclick = () => continueConnectSpaces();
    document.getElementById("ivConnectCancelBtn").onclick = () => cancelConnectSpaces();
  }

  /** そのspaceの3Dハイライト(opacity/emissiveIntensity+選択bounds)を
   * Stage 1のselected表現と同じ見た目で付け外しする、connect mode専用の
   * 補助(setSelectedSpace()とは独立——normal selectionのtoggle状態には
   * 一切触れない)。3Dにmeshが無いspace(UNPLACED/UNRESOLVED)は何もしない。 */
  function setConnectHighlight(spaceId, on) {
    const entry = spaceId ? spaceEntryById.get(spaceId) : null;
    if (!entry) return;
    entry.mesh.material.opacity = on ? VOXEL_OPACITY_SELECTED : VOXEL_OPACITY_DEFAULT;
    entry.mesh.material.emissiveIntensity = on ? VOXEL_EMISSIVE_INTENSITY_SELECTED : VOXEL_EMISSIVE_INTENSITY_DEFAULT;
  }

  function startConnectSpaces(spaceId) {
    if (!spaceId) return;
    // normal selectionのハイライトはそのまま流用する(A自身は
    // connect mode中もハイライトされ続ける——setSelectedSpaceのtoggle
    // ロジックには触れず、selectedSpaceIdの値だけクリアする)。
    selectedSpaceId = null;
    mode = "connect";
    connectionEndpointA = spaceId;
    connectionEndpointB = null;
    document.getElementById("ivSelectedSpaceSection").style.display = "none";
    if (lastSpaceListArgs) renderSpaceList(...lastSpaceListArgs, null);
    updateConnectSpacesSection();
  }

  function setConnectionEndpointB(spaceId) {
    if (mode !== "connect") return;
    if (!spaceId || spaceId === connectionEndpointA) return; // 自己接続不可・背景クリック(null)は無視
    if (!isConnectCandidate(spaceId)) return;
    const next = spaceId === connectionEndpointB ? null : spaceId; // 同じBの再クリックで解除
    setConnectHighlight(connectionEndpointB, false);
    connectionEndpointB = next;
    setConnectHighlight(connectionEndpointB, true);
    if (lastSpaceListArgs) renderSpaceList(...lastSpaceListArgs, null);
    updateConnectSpacesSection();
  }

  /** GLOBAL/COMPONENT_LOCAL(componentとして描画された=renderedSpaceIds)か、
   * UNPLACED/UNRESOLVED(sidebar分類)のみをconnection candidateとする。
   * NOT_READY/CONFLICTは対象外(調査結果: NOT_READYはdual viewerが
   * 技術的に扱えない、CONFLICTは既存の矛盾を確認せずconnectionを増やす
   * 導線になるため今回は除外——Stage 2で確定した候補集合、無変更)。
   * 【2026-09-06】UNPLACED/UNRESOLVED/CONFLICTがLocal Workspaceで
   * floating slotとして3D表示・選択可能になった後も、spaceEntryByIdに
   * 登録されているかどうかだけでは候補判定できなくなった(CONFLICTも
   * floating slot meshを持つため)。そのため、unrenderedKindByIdの分類
   * 結果を優先的に見る——「3Dで見える/選択できる」ことと「Connect Spaces
   * の候補にできる」ことを明確に分離する。 */
  function isConnectCandidate(spaceId) {
    const unrenderedKindById = lastSpaceListArgs ? lastSpaceListArgs[3] : null;
    const kind = unrenderedKindById ? unrenderedKindById.get(spaceId) : null;
    if (kind) return CONNECT_CANDIDATE_KINDS.has(kind); // conflict/not_readyはfalse
    // unrenderedKindByIdに登場しない = renderedSpaceIds(componentとして
    // 描画されたGLOBAL/COMPONENT_LOCAL)なので、meshの有無で判定する。
    return spaceEntryById.has(spaceId);
  }

  function connectModeInfo() {
    return { active: mode === "connect", endpointA: connectionEndpointA, endpointB: connectionEndpointB };
  }

  function cancelConnectSpaces() {
    const restoreId = connectionEndpointA;
    setConnectHighlight(connectionEndpointB, false);
    mode = "normal";
    connectionEndpointA = null;
    connectionEndpointB = null;
    document.getElementById("ivConnectSpacesSection").style.display = "none";
    // connection mode開始前にAとして選択されていた状態へ復元する
    // (未選択には戻さない、2026-09-05ユーザー指示)。selectedSpaceIdは
    // startConnectSpaces()でnullにしてあるため、setSelectedSpace(restoreId)は
    // 「新規選択」として扱われる(同じspaceの再クリック=解除、という
    // toggle条件には当たらない)。
    setSelectedSpace(restoreId);
  }

  function continueConnectSpaces() {
    if (mode !== "connect" || !connectionEndpointA || !connectionEndpointB) return;
    const a = connectionEndpointA;
    const b = connectionEndpointB;
    // Integrated View自身はここでconnectionを作らない・画面遷移もしない。
    // A/Bをそのまま呼び出し元(local_space_prototype.html)へ渡すだけ。
    connectSpacesCallbacks.forEach((cb) => cb(a, b));
  }

  function clearSubViewMeshes(view) {
    view.meshes.forEach((m) => { view.scene.remove(m); m.geometry.dispose(); m.material.dispose(); });
    view.meshes = [];
    clearGridHelper(view);
  }

  function clearMeshes() {
    clearSubViewMeshes(realityView);
    clearSubViewMeshes(workspaceView);
    spaceEntryById = new Map();
    selectedSpaceId = null;
    clearSelectionBoundsHelper();
    // building切り替え・再読込のたびに、Connect Spaces(Stage 2)の
    // mode状態も必ずnormalへ戻す(古いbuildingのspace_idを引きずらない)。
    mode = "normal";
    connectionEndpointA = null;
    connectionEndpointB = null;
    const connectSectionEl = document.getElementById("ivConnectSpacesSection");
    if (connectSectionEl) connectSectionEl.style.display = "none";
    const selectedSectionEl = document.getElementById("ivSelectedSpaceSection");
    if (selectedSectionEl) selectedSectionEl.style.display = "none";
  }

  // 表示中の全Local Spaceの合成bounding boxに合わせた、床面のgrid helper
  // (2026-09-03、Integrated View visual design改善)。voxel/Spatial Stateの
  // 座標計算には一切関与しない、純粋な3D Viewerの背景装飾。Reality/
  // Workspaceそれぞれ独立したgridHelperを持つ(2026-09-06、スケールが
  // 大きく異なるため共用しない)。
  function clearGridHelper(view) {
    if (!view.gridHelper) return;
    view.scene.remove(view.gridHelper);
    view.gridHelper.geometry.dispose();
    view.gridHelper.material.dispose();
    view.gridHelper = null;
  }
  function updateGridHelper(view, min, max) {
    clearGridHelper(view);
    const span = Math.max(max[0] - min[0], max[2] - min[2], 1);
    const grid = new THREE.GridHelper(span * 1.6, 24, 0x333b42, 0x20262c);
    grid.position.set((min[0] + max[0]) / 2, min[1] - Math.max(span * 0.01, 0.02), (min[2] + max[2]) / 2);
    grid.material.transparent = true;
    // gridは座標・床面の補助情報であり、存在は分かるが普段は意識しない
    // 程度まで弱める(モデルより手前に主張してこないように)。
    // 0.6→0.4→0.18と段階的に下げた(2026-09-04、visual polish)。
    grid.material.opacity = 0.18;
    view.gridHelper = grid;
    view.scene.add(grid);
  }

  /** Local Spaceごとに識別色のbounds wireframeを描く(SPACE_IDENTITY_COLOR_PALETTE、
   * voxelの実colorとは無関係の識別専用ヘルパー)。meshesと同じ配列で管理し、
   * 通常の描画クリアと同じタイミングでdisposeする。 */
  function buildSpaceBoundsHelper(min, max, colorHex) {
    const box = new THREE.Box3(
      new THREE.Vector3(min[0], min[1], min[2]),
      new THREE.Vector3(max[0], max[1], max[2]),
    );
    const helper = new THREE.Box3Helper(box, new THREE.Color(colorHex));
    helper.material.transparent = true;
    helper.material.opacity = 0.6;
    return helper;
  }

  // 選択中Local Spaceのbounds強調表示(3Dクリック選択、2026-09-04)。
  // 識別色のbounds wireframe(buildSpaceBoundsHelper、常時表示)とは別に、
  // 選択中のみ追加でもう1つ、中立色(SELECTION_BOUNDS_COLOR)のBox3Helperを
  // 重ねる。同じヘルパー関数を再利用し、色だけ変える。
  let selectionBoundsHelper = null;
  let selectionBoundsHelperView = null;
  function clearSelectionBoundsHelper() {
    if (!selectionBoundsHelper) return;
    selectionBoundsHelperView.scene.remove(selectionBoundsHelper);
    selectionBoundsHelper.geometry.dispose();
    selectionBoundsHelper.material.dispose();
    selectionBoundsHelper = null;
    selectionBoundsHelperView = null;
  }

  /** 選択中spaceのSelected Local Spaceセクション内action一覧を描画する
   * (2026-09-06)。Viewer/Registration/Registration Resultsは常に表示、
   * 「Connect Spaces」はisConnectCandidate()がtrueの場合のみ表示する——
   * CONFLICT等、3Dで選べるようにはなったが接続候補ではないspaceを
   * 選んだ時に「押せるが失敗する」ボタンを出さないための分離
   * (ユーザー指示: 「3Dで見える/選択できる」と「Connect Spacesの候補に
   * できる」を分離する)。 */
  function renderSelectedSpaceActions(spaceId) {
    const listEl = document.getElementById("ivSelectedSpaceActions");
    if (!listEl) return;
    const connectBtn = isConnectCandidate(spaceId)
      ? `<button type="button" class="dtw-btn iv-action-btn" data-action="connect">Connect Spaces</button>`
      : "";
    listEl.innerHTML = `
      <button type="button" class="dtw-btn iv-action-btn" data-action="viewer">Local Space Viewerを開く</button>
      <button type="button" class="dtw-btn iv-action-btn" data-action="add">Registration(新規スキャン登録)を開く</button>
      <button type="button" class="dtw-btn iv-action-btn" data-action="regresult">Registration Resultsを開く</button>
      ${connectBtn}
    `;
  }

  /** 3Dクリック⇄サイドバー一覧のselection stateを一元管理する唯一の
   * mutator(2026-09-04)。同じspaceIdを渡すとトグルで解除、nullを渡すと
   * 常に解除。selectedSpaceId自体は3D Viewer内の一時的なUI状態であり、
   * どこにも永続化しない(source of truthは既存のspace_idのまま)。 */
  function setSelectedSpace(spaceId) {
    const next = (spaceId && spaceId === selectedSpaceId) ? null : spaceId;

    if (selectedSpaceId) {
      const prevEntry = spaceEntryById.get(selectedSpaceId);
      if (prevEntry) {
        prevEntry.mesh.material.opacity = VOXEL_OPACITY_DEFAULT;
        prevEntry.mesh.material.emissiveIntensity = VOXEL_EMISSIVE_INTENSITY_DEFAULT;
      }
    }
    clearSelectionBoundsHelper();
    selectedSpaceId = next;

    if (selectedSpaceId) {
      const entry = spaceEntryById.get(selectedSpaceId);
      if (entry) {
        entry.mesh.material.opacity = VOXEL_OPACITY_SELECTED;
        entry.mesh.material.emissiveIntensity = VOXEL_EMISSIVE_INTENSITY_SELECTED;
        selectionBoundsHelper = buildSpaceBoundsHelper(entry.min, entry.max, SELECTION_BOUNDS_COLOR);
        selectionBoundsHelperView = entry.view;
        entry.view.scene.add(selectionBoundsHelper);
      }
    }

    if (lastSpaceListArgs) renderSpaceList(...lastSpaceListArgs, selectedSpaceId);
    if (window.__integratedViewLastResult) window.__integratedViewLastResult.selectedSpaceId = selectedSpaceId;

    // 「Selected Local Space」セクション(2026-09-05、既存機能への
    // navigation)。ここではspace_idを表示してactionボタンを出すだけで、
    // 遷移そのものはonSpaceAction(cb)経由で呼び出し元に委譲する。
    const selectedSectionEl = document.getElementById("ivSelectedSpaceSection");
    const selectedIdEl = document.getElementById("ivSelectedSpaceId");
    if (selectedSectionEl && selectedIdEl) {
      selectedSectionEl.style.display = selectedSpaceId ? "block" : "none";
      selectedIdEl.textContent = selectedSpaceId ? `選択中: ${selectedSpaceId}` : "";
      if (selectedSpaceId) renderSelectedSpaceActions(selectedSpaceId);
    }

    spaceSelectedCallbacks.forEach((cb) => cb(selectedSpaceId));
  }

  // Spatial State(Read Model経由)のオーバーレイ表示は、既存のvoxelメッシュ
  // (meshes)とは別のレイヤーとして管理する。表示ON/OFFの切り替えのたびに
  // 通常のvoxelメッシュまで作り直さずに済むようにするため。
  let spatialStateMeshes = []; // [{ mesh, view }]
  function clearSpatialStateMeshes() {
    spatialStateMeshes.forEach(({ mesh, view }) => { view.scene.remove(mesh); mesh.geometry.dispose(); mesh.material.dispose(); });
    spatialStateMeshes = [];
  }
  // 直近のopen()で実際に描画された(=fail-closedでスキップされなかった)
  // Local Spaceについて、Spatial Stateオーバーレイの構築に必要な情報を
  // 保持しておく(チェックボックスをON/OFFするたびにopen()し直さずに
  // 済むようにするため)。
  let lastRenderedSpaces = [];
  // renderSpaceList()に直近渡した引数(allSpaceIds/renderedSpaceIds/
  // spaceFrame)。selection変更時に3D側の再構築無しでサイドバーだけ
  // 再描画するために保持する(2026-09-04)。
  let lastSpaceListArgs = null;

  async function buildSpatialStateMeshFor(spaceId, coordinateDefinition, transform) {
    let data;
    try {
      data = await fetchSpatialState(spaceId);
    } catch (e) {
      console.warn("[integrated-view] Spatial State取得に失敗しました:", spaceId, e);
      return null;
    }
    const entries = Object.entries(data.voxels || {}).filter(([, v]) => isRenderablePresence(v.presence));
    if (entries.length === 0) return null;

    const geo = new THREE.BoxGeometry(1, 1, 1);
    const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.85, metalness: 0.0 });
    const mesh = new THREE.InstancedMesh(geo, mat, entries.length);
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(entries.length * 3), 3);
    const m = new THREE.Matrix4();
    const color = new THREE.Color();
    let written = 0;

    entries.forEach(([spatialId, v]) => {
      let center, voxelSize;
      try {
        ({ center, voxelSize } = localSpatialIdToWorldCenter(spatialId, coordinateDefinition));
      } catch (e) {
        console.warn("[integrated-view]", e.message);
        return;
      }
      const intrinsic = worldToIntrinsic(center, coordinateDefinition);
      const integrated = applyRigidTransform2D(transform.yaw_rad, transform.translation, intrinsic);
      const [dx, dy, dz] = toDisplayCoordinates(integrated[0], integrated[1], integrated[2]);
      const displaySize = voxelSize * SPATIAL_STATE_VOXEL_DISPLAY_RATIO;
      m.makeScale(displaySize, displaySize, displaySize);
      m.setPosition(dx, dy, dz);
      mesh.setMatrixAt(written, m);
      color.setHex(colorForPresence(v.presence) ?? 0xcccccc);
      mesh.setColorAt(written, color);
      written++;
    });
    if (written === 0) return null;
    mesh.count = written;
    mesh.instanceMatrix.needsUpdate = true;
    mesh.instanceColor.needsUpdate = true;
    mesh.userData.spaceId = spaceId;
    return mesh;
  }

  async function updateSpatialStateOverlay() {
    clearSpatialStateMeshes();
    const checked = document.getElementById("ivShowSpatialState")?.checked ?? false;
    const overlayStatusEl = document.getElementById("ivSpatialStateStatus");
    if (!checked) {
      if (overlayStatusEl) overlayStatusEl.textContent = "";
      return;
    }
    if (lastRenderedSpaces.length === 0) {
      if (overlayStatusEl) overlayStatusEl.textContent = "表示中のLocal Spaceがありません。";
      return;
    }
    if (overlayStatusEl) overlayStatusEl.textContent = "Spatial Stateを読み込み中…";
    let shown = 0;
    for (const { spaceId, coordinateDefinition, transform, view } of lastRenderedSpaces) {
      const mesh = await buildSpatialStateMeshFor(spaceId, coordinateDefinition, transform);
      if (mesh) {
        view.scene.add(mesh);
        spatialStateMeshes.push({ mesh, view });
        shown++;
      }
    }
    if (overlayStatusEl) {
      overlayStatusEl.textContent = shown > 0
        ? `${shown}個のLocal SpaceでSpatial Stateを表示中`
        : "表示可能なSpatial Stateがありません(まだ計測が行われていない可能性があります)。";
    }
  }
  document.getElementById("ivShowSpatialState").addEventListener("change", () => { updateSpatialStateOverlay(); });

  // サイドバーのLocal Space一覧⇄3Dクリック選択の双方向同期(2026-09-04)。
  // renderSpaceList()は毎回innerHTMLを丸ごと再生成するため、行ごとに
  // listenerを付けるのではなく、親要素に1つだけ委譲(event delegation)で
  // 登録する。
  document.getElementById("ivSpaceList").addEventListener("click", (ev) => {
    const row = ev.target.closest(".iv-space-row[data-space-id]");
    if (!row) return;
    // Connect Spaces(Stage 2)中は、sidebarクリックの結果もendpoint Bの
    // 選択へ回す(3Dクリックと同じ分岐、2026-09-05)。
    if (mode === "connect") {
      setConnectionEndpointB(row.dataset.spaceId);
    } else {
      setSelectedSpace(row.dataset.spaceId);
    }
  });

  function resize() {
    const w = canvasHolder.clientWidth, h = canvasHolder.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h);
    for (const view of [realityView, workspaceView]) {
      view.camera.aspect = w / h;
      view.camera.updateProjectionMatrix();
    }
  }
  window.addEventListener("resize", resize);

  function fitCameraToBounds(view, min, max) {
    const center = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
    const radius = Math.max(
      Math.sqrt((max[0] - min[0]) ** 2 + (max[1] - min[1]) ** 2 + (max[2] - min[2]) ** 2) / 2,
      0.5,
    );
    view.camera.position.set(center[0] + radius, center[1] + radius, center[2] + radius);
    view.controls.target.set(center[0], center[1], center[2]);
    view.camera.near = Math.max(0.01, radius * 0.005);
    view.camera.far = radius * 20;
    view.camera.updateProjectionMatrix();
    view.controls.update();
  }

  function animate() {
    requestAnimationFrame(animate);
    activeView.controls.update();
    renderer.render(activeView.scene, activeView.camera);
  }
  animate();

  async function fetchJson(url) {
    const res = await fetch(url);
    if (res.status === 404) return { notFound: true };
    if (!res.ok) throw new Error(`${url} (status=${res.status})`);
    return { data: await res.json() };
  }

  /** 既存のSpatial ID voxel API(ロードマップStep 2〜4、Local Space単体Viewerと同一)を
   * 再利用し、1つのLocal Space分のvoxel(位置・色)を取得する。 */
  async function fetchSpaceVoxels(spaceId) {
    const levelsRes = await fetch(`/api/local-spaces/${encodeURIComponent(spaceId)}/spatial-voxels/levels`);
    if (!levelsRes.ok) throw new Error(`levels取得失敗(${spaceId})`);
    const levelsData = await levelsRes.json();
    const finest = levelsData.finest_zoom_level;
    const sortedLevels = [...levelsData.levels].sort((a, b) => b.zoom_level - a.zoom_level);
    const targetIdx = Math.min(DEFAULT_COARSER_STEPS, sortedLevels.length - 1);
    const zoomLevel = sortedLevels[targetIdx] ? sortedLevels[targetIdx].zoom_level : finest;

    const metaRes = await fetch(`/api/local-spaces/${encodeURIComponent(spaceId)}/spatial-voxels?zoom_level=${zoomLevel}`);
    if (!metaRes.ok) throw new Error(`voxel meta取得失敗(${spaceId})`);
    const meta = await metaRes.json();

    const posRes = await fetch(`/api/local-spaces/${encodeURIComponent(spaceId)}/spatial-voxels/positions.bin?zoom_level=${zoomLevel}`);
    if (!posRes.ok) throw new Error(`voxel位置取得失敗(${spaceId})`);
    const positions = new Float32Array(await posRes.arrayBuffer());

    const colorMetaRes = await fetch(`/api/local-spaces/${encodeURIComponent(spaceId)}/spatial-voxels/colors?zoom_level=${zoomLevel}&mode=DEFAULT`);
    const colorMeta = colorMetaRes.ok ? await colorMetaRes.json() : null;
    let codes = null;
    if (colorMetaRes.ok) {
      const colorBinRes = await fetch(`/api/local-spaces/${encodeURIComponent(spaceId)}/spatial-voxels/colors.bin?zoom_level=${zoomLevel}&mode=DEFAULT`);
      if (colorBinRes.ok) codes = new Uint8Array(await colorBinRes.arrayBuffer());
    }

    return { meta, positions, colorMeta, codes };
  }

  function buildSpaceMesh(spaceId, voxelData, coordinateDefinition, transform) {
    const { meta, positions, colorMeta, codes } = voxelData;
    const geo = new THREE.BoxGeometry(1, 1, 1);
    // 通常Voxelを半透明化し、奥側の構造・重なりが見える立体表現にする
    // (2026-09-04、visual polish・純粋なrendering parameterで、色決定
    // ロジック(legend[code]等)や意味は一切変更しない)。depthWriteは
    // 半透明InstancedMeshの定番対策としてfalseにする(true のままだと
    // instance buffer順で先に描画されたvoxelがdepth bufferを書き込み、
    // 本来手前にあるvoxelを隠してしまう「虫食い」が起きるため)。
    // emissiveは中立色(白)・低intensityの一様な値であり、instanceColor
    // (per-instanceの色決定)には連動しない——Three.jsのinstancing shaderは
    // diffuse colorのみをinstanceColorで変調し、emissiveは変調しないため、
    // 全voxel共通の「柔らかい自己発光」演出として意味を持たない
    // (2026-09-04、visual polish)。
    const mat = new THREE.MeshStandardMaterial({
      roughness: 0.55, metalness: 0.0,
      transparent: true, opacity: VOXEL_OPACITY_DEFAULT, depthWrite: false,
      emissive: 0xffffff, emissiveIntensity: VOXEL_EMISSIVE_INTENSITY_DEFAULT,
    });
    const mesh = new THREE.InstancedMesh(geo, mat, meta.voxel_count);
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(meta.voxel_count * 3), 3);

    const m = new THREE.Matrix4();
    const color = new THREE.Color();
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    const legend = colorMeta ? colorMeta.legend : null;

    for (let i = 0; i < meta.voxel_count; i++) {
      const worldPoint = [positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]];
      const intrinsic = worldToIntrinsic(worldPoint, coordinateDefinition);
      const integrated = applyRigidTransform2D(transform.yaw_rad, transform.translation, intrinsic);
      const [dx, dy, dz] = toDisplayCoordinates(integrated[0], integrated[1], integrated[2]);
      m.makeScale(meta.voxel_size, meta.voxel_size, meta.voxel_size);
      m.setPosition(dx, dy, dz);
      mesh.setMatrixAt(i, m);

      if (legend && codes) {
        const rgb = legend[String(codes[i])] || [0.6, 0.6, 0.6];
        color.setRGB(rgb[0], rgb[1], rgb[2]);
      } else {
        color.setHex(0x4a7c74);
      }
      mesh.setColorAt(i, color);

      if (dx < min[0]) min[0] = dx; if (dy < min[1]) min[1] = dy; if (dz < min[2]) min[2] = dz;
      if (dx > max[0]) max[0] = dx; if (dy > max[1]) max[1] = dy; if (dz > max[2]) max[2] = dz;
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
    mesh.userData.spaceId = spaceId;
    return { mesh, min, max };
  }

  /** UNPLACED/UNRESOLVED/CONFLICTのfloating slot(2026-09-06、Local
   * Workspace)。「1 Local Space = 1 floating slot」——回転は常に
   * identity(yaw_rad: 0)、平行移動はslotOffset(presentation-only、
   * このファイル内でだけ計算する新規のclient-only座標)だけを与える。
   * buildSpaceMesh()をそのまま呼ぶだけであり、voxel自体のintrinsic
   * frame計算(worldToIntrinsic、CoordinateDefinition.originを差し引く
   * 正規化)は他の全spaceと共通の既存ロジックを再利用する——
   * slotOffsetの値自体はorigin/rad等、そのspace固有の値に一切依存せず、
   * space_idの並び順だけから決まる(呼び出し側で計算して渡す)。 */
  function buildFloatingSlotMesh(spaceId, voxelData, coordinateDefinition, slotOffset) {
    return buildSpaceMesh(spaceId, voxelData, coordinateDefinition, { yaw_rad: 0, translation: slotOffset });
  }

  function hex6(n) { return `#${n.toString(16).padStart(6, "0")}`; }

  // バッジ(pill)には短いラベルだけを入れ、frameLabelの詳細(EPSG番号・root
  // space_id等)は別行のテキストとして表示する(300px幅のサイドパネルで
  // 折り返し・はみ出しが起きないようにするため、2026-09-03修正)。
  const BADGE_KIND_SHORT_LABEL = { global: "GLOBAL", local: "LOCAL", conflict: "CONFLICT", pending: "UNRESOLVED" };

  // renderedでない(=共有3D sceneに配置されない)Local Spaceの状態分類
  // (2026-09-05)。「3D上に表示できない」という結果が同じでも、domain上の
  // 理由が異なる4種を区別する:
  // - not_ready: coordinate_definition等が未完成で、3D/connection操作の
  //   準備がまだ整っていない
  // - conflict: Nodal Connectionはあり、componentも組めるが、矛盾により
  //   fail-closed
  // - unresolved: Nodal Connectionは存在するが、有効なsolution
  //   (SOLVED/WARNING_HIGH_RESIDUAL)が無くcomponentを一切形成できない
  // - unplaced: Nodal Connectionが1件も無い(Local Spaceとしては
  //   coordinate_definition等が成立しており利用可能)
  // UNPLACEDとNOT_READYを混同しない(「3D操作の準備自体が無い」ことと
  // 「準備はできているが接続が無いだけ」ことは別のdomain上の理由のため)。
  // 【2026-09-06】selectable(通常mode、Stage 1のnormal selectionでの
  // sidebar/3Dクリック可否)は、conflict/unresolvedともtrueへ変更する
  // ——UNPLACED/UNRESOLVED/CONFLICTがLocal Workspaceでfloating slotとして
  // 実体を持つ3Dオブジェクトになった以上、「見えているのに選べない」を
  // 避けるための拡張。分類ロジック(classifyUnrenderedSpaces、上記)自体は
  // 一切変更しない。selectableと、Connect Spacesの候補可否
  // (isConnectCandidate、下記)は明確に別の判定であることに注意——
  // conflictはselectable:trueだがconnect候補には含めない
  // (Stage 2のcandidate集合は無変更)。
  const UNRENDERED_SPACE_KIND_INFO = {
    not_ready: { label: "NOT READY", badgeClass: "iv-badge--not-ready", selectable: false },
    conflict: { label: "CONFLICT", badgeClass: "iv-badge--conflict", selectable: true },
    unresolved: { label: "UNRESOLVED", badgeClass: "iv-badge--unresolved", selectable: true },
    unplaced: { label: "UNPLACED", badgeClass: "iv-badge--unplaced", selectable: true },
  };

  /** renderedでないspaceを上記4種へ分類する(優先順位: not_ready →
   * conflict → unresolved → unplaced)。spaceIdsInAnyComponentは
   * fail-closed分も含む全componentのmember(CONFLICT判定用)、
   * spaceIdsInAnyConnectionはNodal Connection(type=LOCALの
   * endpoint_space_a/b)に一度でも登場したspace_id
   * (UNRESOLVED判定用、/api/nodal-connectionsから取得)。 */
  function classifyUnrenderedSpaces(
    allSpaceIds, renderedSpaceIds, coordinateDefinitionBySpace,
    spaceIdsInAnyComponent, spaceIdsInAnyConnection,
  ) {
    const result = new Map();
    for (const sid of allSpaceIds) {
      if (renderedSpaceIds.has(sid)) continue;
      if (!coordinateDefinitionBySpace.has(sid)) {
        result.set(sid, "not_ready");
      } else if (spaceIdsInAnyComponent.has(sid)) {
        result.set(sid, "conflict");
      } else if (spaceIdsInAnyConnection.has(sid)) {
        result.set(sid, "unresolved");
      } else {
        result.set(sid, "unplaced");
      }
    }
    return result;
  }

  function renderComponentList(components, plans) {
    const el = document.getElementById("ivComponentList");
    const countEl = document.getElementById("ivComponentCount");
    if (countEl) countEl.textContent = components.length > 0 ? `(${components.length})` : "";
    if (components.length === 0) {
      el.innerHTML = `<div class="iv-empty">componentがありません</div>`;
      return;
    }
    el.innerHTML = components.map((c, i) => {
      const plan = plans[i];
      const badgeClass = `iv-badge--${plan.badgeKind}`;
      const badgeText = plan.mode === "FAIL_CLOSED" ? "FAIL-CLOSED" : BADGE_KIND_SHORT_LABEL[plan.badgeKind];
      return `
        <div class="iv-component-row iv-component-row--${plan.badgeKind}">
          <div class="iv-component-row-head">
            <span class="iv-mono">${escapeHtml(c.component_id)}</span>
            <span class="iv-badge ${badgeClass}">${escapeHtml(badgeText)}</span>
          </div>
          ${plan.mode !== "FAIL_CLOSED" ? `<div class="iv-component-frame">${escapeHtml(plan.frameLabel)}</div>` : ""}
          <div class="iv-component-members">members: ${c.local_placement.member_space_ids.map(escapeHtml).join(", ")}</div>
          ${plan.mode === "FAIL_CLOSED" ? `<div class="iv-reason">${escapeHtml(plan.reason)}</div>` : ""}
        </div>
      `;
    }).join("");
  }

  // Connect Spaces候補(B)として選べるkind——Stage 2で確定した集合、無変更。
  // CONFLICTは含めない(2026-09-06、「3Dで見える/選択できる」ことと
  // 「Connect Spacesの候補にできる」ことを明確に分離する)。
  const CONNECT_CANDIDATE_KINDS = new Set(["unplaced", "unresolved"]);

  /** selectedSpaceIdは3Dクリック選択(2026-09-04)/sidebar選択(2026-09-05)
   * との同期用。renderedSpaceIds(componentとして描画されたGLOBAL/
   * COMPONENT_LOCAL)か、UNRENDERED_SPACE_KIND_INFO.selectableがtrueの
   * 行にだけdata-space-id属性を付け、クリック可能にする(NOT READYのみ
   * 常に選択不可のまま)。
   * 【2026-09-06】通常mode(selectable)とconnect mode(接続候補)は
   * 別の判定にする——CONFLICTは通常mode選択は可能だが、connect mode中は
   * 接続候補ではないため選択可能な行としては出さない(CONNECT_CANDIDATE_
   * KINDS参照)。normal selectionの選択可否(UNRENDERED_SPACE_KIND_INFO.
   * selectable)自体は分類ロジックではなく「分類結果をUIでどう使うか」の
   * 消費側設定であり、classifyUnrenderedSpaces()は無変更。 */
  function renderSpaceList(allSpaceIds, renderedSpaceIds, spaceFrame, unrenderedKindById, selectedSpaceId) {
    const el = document.getElementById("ivSpaceList");
    const countEl = document.getElementById("ivSpaceCount");
    if (countEl) countEl.textContent = allSpaceIds.length > 0 ? `(${renderedSpaceIds.size}/${allSpaceIds.length})` : "";
    if (allSpaceIds.length === 0) {
      el.innerHTML = `<div class="iv-empty">Local Spaceがありません</div>`;
      return;
    }
    // Connect Spaces(Stage 2)中は、selectedSpaceId(normal選択)は常に
    // nullになっている(startConnectSpacesでクリア済み)ため、endpoint A/Bの
    // 判定とselectedSpaceIdの判定は同時にtrueにならず、単純にORでよい。
    const connectState = connectModeInfo();
    el.innerHTML = allSpaceIds.map((sid) => {
      const identityColor = hex6(colorForSpaceIdentity(sid, allSpaceIds));
      const dot = `<span class="iv-identity-dot" style="background:${identityColor}"></span>`;
      const isConnectEndpoint = connectState.active && (sid === connectState.endpointA || sid === connectState.endpointB);
      if (renderedSpaceIds.has(sid)) {
        const { badgeKind } = spaceFrame.get(sid);
        const selectedClass = (isConnectEndpoint || sid === selectedSpaceId) ? " iv-space-row--selected" : "";
        return `<div class="iv-space-row${selectedClass}" data-space-id="${escapeHtml(sid)}">${dot}<span class="iv-mono iv-space-id">${escapeHtml(sid)}</span> <span class="iv-badge iv-badge--${badgeKind}">${escapeHtml(BADGE_KIND_SHORT_LABEL[badgeKind])}</span></div>`;
      }
      const kind = (unrenderedKindById && unrenderedKindById.get(sid)) || "not_ready";
      const info = UNRENDERED_SPACE_KIND_INFO[kind] || UNRENDERED_SPACE_KIND_INFO.not_ready;
      const badge = `<span class="iv-badge ${info.badgeClass}">${escapeHtml(info.label)}</span>`;
      // normal mode: このkindが通常selectableかどうか(UNPLACED/
      // UNRESOLVED/CONFLICT、2026-09-06)。connect mode中: Stage 2の
      // 接続候補集合(UNPLACED/UNRESOLVEDのみ、CONFLICTは含めない)。
      const selectableNow = connectState.active
        ? CONNECT_CANDIDATE_KINDS.has(kind)
        : info.selectable;
      if (selectableNow) {
        const selectedClass = (isConnectEndpoint || sid === selectedSpaceId) ? " iv-space-row--selected" : "";
        return `<div class="iv-space-row${selectedClass}" data-space-id="${escapeHtml(sid)}">${dot}<span class="iv-mono iv-space-id">${escapeHtml(sid)}</span> ${badge}</div>`;
      }
      return `<div class="iv-space-row iv-space-row--unrendered">${dot}<span class="iv-mono iv-space-id">${escapeHtml(sid)}</span> ${badge}</div>`;
    }).join("");
  }

  function extendBounds(minArr, maxArr, min, max) {
    for (let k = 0; k < 3; k++) {
      if (min[k] < minArr[k]) minArr[k] = min[k];
      if (max[k] > maxArr[k]) maxArr[k] = max[k];
    }
  }

  async function open(buildingId) {
    setStatus("読み込み中…", "loading");
    document.getElementById("ivHeaderBuilding").textContent = `Building: ${buildingId}`;
    clearMeshes();
    clearSpatialStateMeshes();
    lastRenderedSpaces = [];
    document.getElementById("ivResultMeta").textContent = "―";
    document.getElementById("ivComponentList").innerHTML = "";
    document.getElementById("ivSpaceList").innerHTML = "";

    const spacesRes = await fetchJson(`/api/buildings/${encodeURIComponent(buildingId)}/local-spaces`);
    const spaces = spacesRes.data ? spacesRes.data.local_spaces : [];
    const coordinateDefinitionBySpace = new Map(
      spaces.filter((s) => s.coordinate_definition).map((s) => [s.space_id, s.coordinate_definition]),
    );
    const allSpaceIds = spaces.map((s) => s.space_id);

    // sidebarの状態分類(UNPLACED/UNRESOLVED)用に、Nodal Information本体が
    // 既に使っている一覧取得APIをここでも読む(2026-09-05)。Spatial
    // Resolution結果(componentsに登場するか)だけでは「Connectionが1件も
    // 無い」ことと「Connectionはあるが全てUNSOLVED/UNSOLVABLE」を区別
    // できないため(build_components()はSOLVED/WARNING_HIGH_RESIDUALな
    // edgeしかグラフのnodeにしない)。Nodal Information本体のロジック・
    // データモデルには一切触れず、既存の読み取り専用エンドポイントを
    // 読む場所を1つ増やすだけ。
    let spaceIdsInAnyConnection = new Set();
    try {
      const connRes = await fetchJson(`/api/nodal-connections?building_id=${encodeURIComponent(buildingId)}`);
      const connections = connRes.data ? connRes.data.connections : [];
      for (const c of connections) {
        for (const ref of [c.endpoint_space_a, c.endpoint_space_b]) {
          if (ref && ref.type === "LOCAL" && ref.space_id) spaceIdsInAnyConnection.add(ref.space_id);
        }
      }
    } catch (e) {
      console.warn("[integrated-view] Nodal Connection一覧の取得に失敗しました(sidebarの状態分類のみに影響):", e);
    }

    // 【2026-09-06】以前はSpatial Resolution結果が無い(notFound)場合、
    // ここで早期returnして何も描画しなかった。UNPLACED等のfloating slotは
    // Spatial Resolutionの実行有無と無関係に(Nodal Connectionが無いだけの
    // spaceとして)Local Workspaceへ表示すべきなので、componentsを空配列に
    // するだけで以降の処理は共通のパイプラインへ合流させる
    // (GLOBAL/COMPONENT_LOCALのループは空配列なら何もせず自然にスキップ
    // される)。
    const resultRes = await fetchJson(`/api/spatial-resolution/results/${encodeURIComponent(buildingId)}`);
    const notFound = !!resultRes.notFound;
    const result = notFound ? null : resultRes.data.result;
    if (!notFound) {
      document.getElementById("ivResultMeta").textContent =
        `resolved_at: ${result.resolved_at}  ·  target_epsg: ${result.target_epsg}`;
    }

    const components = (result && result.components) || [];
    const plans = components.map(resolveComponentDisplayPlan);
    renderComponentList(components, plans);

    // CONFLICT判定用: fail-closedかどうかに関わらず、componentのmember
    // 一覧に一度でも登場したspace_id(component.local_placement.
    // member_space_idsはFAIL_CLOSEDでも値自体は取得できる)。
    const spaceIdsInAnyComponent = new Set();
    for (const c of components) {
      for (const sid of c.local_placement.member_space_ids) spaceIdsInAnyComponent.add(sid);
    }

    const renderedSpaceIds = new Set();
    const spaceFrame = new Map();
    const realityMin = [Infinity, Infinity, Infinity];
    const realityMax = [-Infinity, -Infinity, -Infinity];
    const workspaceMin = [Infinity, Infinity, Infinity];
    const workspaceMax = [-Infinity, -Infinity, -Infinity];
    let realityCount = 0;
    let workspaceCount = 0;
    let conflictCount = 0;
    let skippedNoCoordDef = [];
    // Local Workspace内で、component/floating slot同士が重ならないように
    // 並べるための、presentation-only・client-onlyの水平offset
    // (2026-09-06)。CoordinateDefinition.originはここでは一切使わない
    // ——space_id/component_idの並び順だけから決まる。
    let workspaceOffsetX = 0;
    // 検証・デバッグ用: 各Local Spaceに実際に適用したtransformと、
    // 描画結果(bounding box)をwindowへ公開する(実機E2E確認用の計測フック、
    // 既存の[spatial-voxel-perf]系ログと同じ位置づけ。編集機能ではない)。
    const debugPlacements = [];

    for (let i = 0; i < components.length; i++) {
      const plan = plans[i];
      if (plan.mode === "FAIL_CLOSED") {
        conflictCount++; // fail-closed: このcomponentのmemberは一切描画しない(表示のみの集計、判定ロジックは無変更)
        continue;
      }

      const targetView = plan.mode === "GLOBAL" ? realityView : workspaceView;
      // COMPONENT_LOCALのみpresentation offsetを加える(component内部の
      // 相対配置=local_placement.transformsはそのまま使い、component
      // 全体をどこに置くかだけをこのoffsetで決める)。GLOBALは既存の
      // 実EPSG座標(member_transforms_to_global)をそのまま使うため不要。
      const componentOffset = plan.mode === "COMPONENT_LOCAL" ? [workspaceOffsetX, 0, 0] : null;
      let componentHadMember = false;

      for (const spaceId of components[i].local_placement.member_space_ids) {
        const coordinateDefinition = coordinateDefinitionBySpace.get(spaceId);
        if (!coordinateDefinition) {
          skippedNoCoordDef.push(spaceId);
          continue;
        }
        const baseTransform = plan.transforms[spaceId];
        if (!baseTransform) continue; // このcomponentのtransformに含まれないmember(理論上は起きない防御)
        const transform = componentOffset
          ? {
              yaw_rad: baseTransform.yaw_rad,
              translation: [
                baseTransform.translation[0] + componentOffset[0],
                baseTransform.translation[1] + componentOffset[1],
                baseTransform.translation[2] + componentOffset[2],
              ],
            }
          : baseTransform;

        setStatus(`${spaceId} のvoxelを読み込み中…`, "loading");
        let voxelData;
        try {
          voxelData = await fetchSpaceVoxels(spaceId);
        } catch (e) {
          console.error("[integrated-view]", e);
          continue;
        }
        const { mesh, min, max } = buildSpaceMesh(spaceId, voxelData, coordinateDefinition, transform);
        targetView.scene.add(mesh);
        targetView.meshes.push(mesh);
        spaceEntryById.set(spaceId, { mesh, min, max, view: targetView });
        const identityColor = colorForSpaceIdentity(spaceId, allSpaceIds);
        const boundsHelper = buildSpaceBoundsHelper(min, max, identityColor);
        targetView.scene.add(boundsHelper);
        targetView.meshes.push(boundsHelper);
        renderedSpaceIds.add(spaceId);
        spaceFrame.set(spaceId, { frameLabel: plan.frameLabel, badgeKind: plan.badgeKind });
        lastRenderedSpaces.push({ spaceId, coordinateDefinition, transform, view: targetView });
        componentHadMember = true;
        if (plan.mode === "GLOBAL") {
          realityCount++;
          extendBounds(realityMin, realityMax, min, max);
        } else {
          workspaceCount++;
          extendBounds(workspaceMin, workspaceMax, min, max);
        }
        debugPlacements.push({
          spaceId,
          frameLabel: plan.frameLabel,
          transform,
          boundsMin: min,
          boundsMax: max,
          center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2],
        });
      }

      if (plan.mode === "COMPONENT_LOCAL" && componentHadMember) {
        workspaceOffsetX += WORKSPACE_SLOT_SPACING;
      }
    }

    window.__integratedViewLastResult = { buildingId, placements: debugPlacements, selectedSpaceId: null };

    const unrenderedKindById = classifyUnrenderedSpaces(
      allSpaceIds, renderedSpaceIds, coordinateDefinitionBySpace, spaceIdsInAnyComponent, spaceIdsInAnyConnection,
    );

    // UNPLACED/UNRESOLVED/CONFLICTを、Local Workspaceへ「1 space = 1
    // floating slot」として表示する(2026-09-06)。NOT_READYは
    // coordinate_definitionが無くvoxel自体が無いため対象外(sidebarのみ)。
    let workspaceFloatingCount = 0;
    for (const spaceId of allSpaceIds) {
      const kind = unrenderedKindById.get(spaceId);
      if (kind !== "unplaced" && kind !== "unresolved" && kind !== "conflict") continue;
      const coordinateDefinition = coordinateDefinitionBySpace.get(spaceId);
      if (!coordinateDefinition) continue; // 理論上not_readyはここに来ない(念のための防御)

      setStatus(`${spaceId} のvoxelを読み込み中…`, "loading");
      let voxelData;
      try {
        voxelData = await fetchSpaceVoxels(spaceId);
      } catch (e) {
        console.error("[integrated-view]", e);
        continue;
      }
      const slotOffset = [workspaceOffsetX, 0, 0];
      const { mesh, min, max } = buildFloatingSlotMesh(spaceId, voxelData, coordinateDefinition, slotOffset);
      workspaceView.scene.add(mesh);
      workspaceView.meshes.push(mesh);
      spaceEntryById.set(spaceId, { mesh, min, max, view: workspaceView });
      // floating slotは識別色ではなく状態色のbounds wireframeにする
      // (Stage 1.5のsidebar badge色と同じ配色。UNPLACED/UNRESOLVED/
      // CONFLICTを一目で区別できるようにする、2026-09-06)。
      const statusColor = cssVarColor(FLOATING_SLOT_STATUS_COLOR_VAR[kind], "#8a919a");
      const boundsHelper = buildSpaceBoundsHelper(min, max, statusColor);
      workspaceView.scene.add(boundsHelper);
      workspaceView.meshes.push(boundsHelper);
      extendBounds(workspaceMin, workspaceMax, min, max);
      workspaceFloatingCount++;
      workspaceOffsetX += WORKSPACE_SLOT_SPACING;
    }
    workspaceCount += workspaceFloatingCount;

    lastSpaceListArgs = [allSpaceIds, renderedSpaceIds, spaceFrame, unrenderedKindById];
    renderSpaceList(...lastSpaceListArgs, selectedSpaceId);

    if (realityCount > 0) {
      fitCameraToBounds(realityView, realityMin, realityMax);
      updateGridHelper(realityView, realityMin, realityMax);
    }
    if (workspaceCount > 0) {
      fitCameraToBounds(workspaceView, workspaceMin, workspaceMax);
      updateGridHelper(workspaceView, workspaceMin, workspaceMax);
    }

    if (realityCount === 0 && workspaceCount === 0) {
      setStatus(
        notFound
          ? "このbuildingはまだSpatial Resolutionがresolveされていません(Nodal Informationタブで実行してください)。表示可能なLocal Spaceもありません。"
          : "表示可能なLocal Spaceがありません(componentが無い、全てfail-closed、またはCoordinateDefinition未整備)。",
        conflictCount > 0 ? "conflict" : "empty",
      );
    } else {
      const parts = [];
      parts.push(`Reality View: ${realityCount}件`);
      parts.push(`Local Workspace: ${workspaceCount}件`);
      if (notFound) parts.push("Spatial Resolution未実行");
      if (conflictCount > 0) parts.push(`${conflictCount}件のcomponentがfail-closed`);
      setStatus(parts.join(" / "), conflictCount > 0 ? "conflict" : "ok");
    }
    if (skippedNoCoordDef.length > 0) {
      console.warn("[integrated-view] CoordinateDefinition未整備のためスキップ:", skippedNoCoordDef);
    }

    // チェックボックスが既にONの状態でbuilding切り替え/再読込された場合も、
    // 新しいlastRenderedSpacesに基づいてオーバーレイを再構築する。
    await updateSpatialStateOverlay();

    requestAnimationFrame(() => requestAnimationFrame(resize));
  }

  return {
    open,
    // 将来、Registration/Nodal Information/Spatial State/Local Space
    // Viewer等へ選択結果(space_id)を渡すための最小限のフック
    // (2026-09-04)。今回はこのファイル内では何もsubscribeしない——
    // 呼び出し側が必要になった時に登録するだけで済むようにする。
    onSpaceSelected(cb) { spaceSelectedCallbacks.push(cb); },
    // Selected Local Spaceセクションのactionボタンが押された時に
    // (actionId, spaceId)を受け取るためのフック(2026-09-05、Stage 1)。
    // actionIdはlocal_space_prototype.htmlの.mode-tabのdata-mode値
    // ("viewer"/"add"/"regresult")とそのまま一致させてあり、変換テーブル
    // 無しで既存のタブ切り替えロジックへ渡せる。実際の画面遷移は
    // 呼び出し側の責務であり、ここでは一切行わない。
    onSpaceAction(cb) { spaceActionCallbacks.push(cb); },
    // Connect Spaces(Stage 2、2026-09-05)でContinueが押された時に
    // (spaceIdA, spaceIdB)を受け取るためのフック。onSpaceActionとは
    // 別にする(渡す値の形が単一spaceIdではなくA/Bの2つのため)。
    // Integrated View自身はconnectionを作らない・遷移もしない——
    // 呼び出し側がNodal Informationへの遷移・presetを行う。
    onConnectSpaces(cb) { connectSpacesCallbacks.push(cb); },
  };
}

function injectStyles() {
  if (document.getElementById("iv-styles")) return;
  const style = document.createElement("style");
  style.id = "iv-styles";
  style.textContent = `
    /* ---- 全体レイアウト: Viewerを主役にしたWorkbench構成 ---- */
    .iv-workbench { display: flex; flex-direction: column; flex: 1; min-height: 0; background: var(--bg); }
    .iv-header {
      display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
      padding: 10px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
      flex-shrink: 0; flex-wrap: wrap; row-gap: 4px;
    }
    .iv-header-main { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
    .iv-header-label { font-size: 12.5px; font-weight: 700; color: var(--text); letter-spacing: 0.01em; white-space: nowrap; }
    .iv-header-building { font-size: 11.5px; color: var(--text-dim); font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .iv-header-meta { font-size: 11px; color: var(--text-faint); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .iv-body { display: flex; flex: 1; min-height: 0; }

    /* ---- Reality View / Local Workspace tab切り替え(2026-09-06) ---- */
    .iv-view-tabs { display: flex; gap: 2px; padding: 0 20px; border-bottom: 1px solid var(--border); background: var(--panel); flex-shrink: 0; }
    .iv-view-tab {
      appearance: none; border: none; background: transparent; cursor: pointer;
      padding: 8px 14px; font-size: 12px; font-weight: 600; color: var(--text-faint);
      border-bottom: 2px solid transparent; margin-bottom: -1px;
    }
    .iv-view-tab:hover { color: var(--text-dim); }
    .iv-view-tab.active { color: var(--text); border-bottom-color: var(--accent); }

    /* ---- Viewer本体: 余白+borderでpanel化し、edge-to-edgeのcanvasと分離する ---- */
    .iv-canvas-wrap { flex: 1; position: relative; min-width: 0; margin: 12px 0 12px 12px; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; background: var(--bg-canvas); }
    #ivCanvasHolder { position: absolute; inset: 0; }
    #ivCanvasHolder canvas { display: block; }

    .iv-status-chip {
      position: absolute; top: 12px; left: 12px; background: var(--panel-overlay);
      border: 1px solid var(--border); border-left: 3px solid var(--border-strong); border-radius: 7px;
      padding: 7px 13px; font-size: 11.5px; color: var(--text-dim); box-shadow: var(--shadow-sm);
      max-width: calc(100% - 24px);
    }
    .iv-status-chip--loading { border-left-color: var(--text-faint); color: var(--text-dim); }
    .iv-status-chip--empty { border-left-color: var(--border-strong); color: var(--text-faint); }
    .iv-status-chip--ok { border-left-color: var(--status-solved); color: var(--status-solved); }
    .iv-status-chip--conflict { border-left-color: var(--status-conflict); color: var(--status-conflict); }

    /* Local Workspaceは物理配置ではないことを常時明示する注記
       (spatial-network-view.jsの.sn-topology-noticeと同じ位置づけ、
       2026-09-06)。 */
    .iv-workspace-notice {
      position: absolute; top: 12px; right: 12px; background: var(--panel-overlay);
      border: 1px solid var(--border); border-radius: 7px; padding: 6px 12px;
      font-size: 10.5px; color: var(--text-faint); box-shadow: var(--shadow-sm);
      max-width: calc(100% - 24px);
    }

    .iv-legend {
      position: absolute; bottom: 12px; left: 12px; display: flex; flex-direction: column; gap: 4px;
      background: var(--panel-overlay); border: 1px solid var(--border); border-radius: 7px;
      padding: 8px 12px; font-size: 10.5px; color: var(--text-dim); box-shadow: var(--shadow-sm);
    }
    .iv-legend-item { display: flex; align-items: center; gap: 7px; white-space: nowrap; }
    .iv-legend-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; box-shadow: inset 0 0 0 1px var(--swatch-outline); }
    .iv-legend-dot--global { background: var(--status-solved); }
    .iv-legend-dot--local { background: var(--status-info); }
    .iv-legend-dot--conflict { background: var(--status-conflict); }
    .iv-legend-dot--pending { background: var(--status-pending); }

    /* ---- サイドパネル: 情報階層をsection単位で明示するcard構成 ---- */
    .iv-side { width: 300px; flex-shrink: 0; padding: 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
    .iv-side-section { border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); padding: 12px 14px; }
    .iv-side-section-title {
      font-size: 11px; font-weight: 700; color: var(--text-faint); letter-spacing: 0.05em; text-transform: uppercase;
      margin-bottom: 10px; display: flex; align-items: baseline; gap: 6px;
    }
    .iv-count { font-size: 10.5px; font-weight: 500; color: var(--text-faint); letter-spacing: 0; text-transform: none; }
    .iv-hint { font-size: 11px; color: var(--text-faint); line-height: 1.6; margin-top: 6px; }
    .iv-empty { padding: 8px 2px; color: var(--text-faint); font-size: 12px; }

    .iv-component-row {
      border: 1px solid var(--border-soft); border-left: 3px solid var(--border-strong); border-radius: 6px;
      padding: 7px 10px; margin-bottom: 6px; font-size: 11.5px; color: var(--text-dim); line-height: 1.6; background: var(--bg);
    }
    .iv-component-row:last-child { margin-bottom: 0; }
    .iv-component-row-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 3px; }
    .iv-component-frame { color: var(--text-dim); font-size: 11px; margin-bottom: 3px; word-break: break-word; }
    .iv-component-members { color: var(--text-faint); word-break: break-word; }
    .iv-component-row--global { border-left-color: var(--status-solved); }
    .iv-component-row--local { border-left-color: var(--status-info); }
    .iv-component-row--conflict { border-left-color: var(--status-conflict); }
    .iv-component-row--pending { border-left-color: var(--status-pending); }

    .iv-space-row {
      font-size: 12px; color: var(--text-dim); padding: 6px 2px; border-bottom: 1px solid var(--border-soft);
      display: flex; align-items: center; gap: 8px; border-radius: 5px;
    }
    .iv-space-row:last-child { border-bottom: none; }
    .iv-space-row--unrendered { opacity: 0.6; }
    /* data-space-id付き(=3D側にmeshがあり選択可能)な行だけクリック可能に見せる
       (2026-09-04、3Dクリック選択との双方向同期)。 */
    .iv-space-row[data-space-id] { cursor: pointer; }
    .iv-space-row[data-space-id]:hover { background: var(--panel-overlay); }
    .iv-space-row--selected { background: var(--panel-overlay); box-shadow: inset 2px 0 0 var(--text); }
    .iv-space-id { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .iv-identity-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; box-shadow: inset 0 0 0 1px var(--swatch-outline); }

    .iv-mono { font-variant-numeric: tabular-nums; font-family: var(--font-mono); font-size: 11px; color: var(--text); }
    .iv-reason { color: var(--status-conflict); margin-top: 3px; font-size: 11px; }
    .iv-badge { display: inline-block; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 20px; white-space: nowrap; flex-shrink: 0; }
    .iv-badge--global { background: var(--status-solved-soft); color: var(--status-solved); }
    .iv-badge--local { background: var(--status-info-soft); color: var(--status-info); }
    .iv-badge--conflict { background: var(--status-conflict-soft); color: var(--status-conflict); }
    .iv-badge--pending { background: var(--status-pending-soft); color: var(--status-pending); border: 1px solid var(--border); }
    /* renderedでないLocal Spaceの状態分類(2026-09-05)。UNPLACEDは
       選択可能なので視認しやすい配色に、NOT READYは最も控えめにする。 */
    .iv-badge--unresolved { background: var(--status-warning-soft); color: var(--status-warning); }
    .iv-badge--unplaced { background: var(--status-pending-soft); color: var(--status-pending); border: 1px solid var(--border-strong); }
    .iv-badge--not-ready { background: var(--panel); color: var(--text-faint); border: 1px solid var(--border); }
    .iv-toggle { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--text); cursor: pointer; }

    /* ---- Selected Local Space section(3Dクリック選択→既存機能への
       navigation、2026-09-05)。3D canvas内にはbuttonを置かず、既存の
       サイドバー領域だけを使う。 ---- */
    #ivSelectedSpaceId { margin-bottom: 8px; word-break: break-word; }
    .iv-action-list { display: flex; flex-direction: column; gap: 6px; }
    .iv-action-btn { width: 100%; text-align: left; font-size: 12px; }
  `;
  document.head.appendChild(style);
}
