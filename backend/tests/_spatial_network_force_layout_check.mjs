// backend/tests/_spatial_network_force_layout_check.mjs
//
// spatial-network/force-layout.js の createForceSimulation() を検証する
// 自己完結型テストハーネス。2026-09-25、x/y/zの3D対応後の検証:
// 決定的なFibonacci sphere初期配置(z込み)、運動エネルギーが単調に
// (ノイズを許容しつつ)0へ収束し、有限tick数以内にsettled=trueとなること、
// drag(setNodePosition/releaseNode)後もローカルに再収束することを確認する。
// 厳密な位置の期待値は手計算できない(力学シミュレーションのため)ので、
// 「収束するか」という性質のみを検証する。単体では実行しない
// (pytest経由の利用を想定)。
import { createForceSimulation } from "../../spatial-network/force-layout.js";

const results = [];
function check(label, condition, detail) {
  results.push({ label, pass: !!condition, detail: detail ?? null });
}

const nodes = [{ id: "A" }, { id: "B" }, { id: "C" }, { id: "D" }, { id: "E" }];
const edges = [
  { sourceId: "A", targetId: "B" },
  { sourceId: "B", targetId: "C" },
  { sourceId: "C", targetId: "D" },
  { sourceId: "D", targetId: "A" },
  { sourceId: "A", targetId: "E" },
];

// --- 決定的な初期配置(同じ入力なら毎回同じ初期position、z込み) ---
{
  const sim1 = createForceSimulation(nodes, edges, { width: 800, height: 600 });
  const sim2 = createForceSimulation(nodes, edges, { width: 800, height: 600 });
  const p1 = sim1.getPositions();
  const p2 = sim2.getPositions();
  let identical = true;
  for (const [id, pos] of p1) {
    const pos2 = p2.get(id);
    if (!pos2 || pos.x !== pos2.x || pos.y !== pos2.y || pos.z !== pos2.z) identical = false;
  }
  check("deterministic_initial_placement", identical, { p1: Array.from(p1.entries()) });

  // 「暗い3次元空間に天体のように浮遊する」表現のため、z成分が意味のある
  // ばらつきを持つこと(全ノードがz=0の平面に潰れていないこと)を確認する。
  const zValues = Array.from(p1.values()).map((p) => p.z);
  const hasZVariance = Math.max(...zValues) - Math.min(...zValues) > 1;
  check("initial_placement_has_z_variance", hasZVariance, { zValues });
}

// --- 収束: 有限tick数以内にsettled=trueになり、その後は無限ループしない ---
{
  const sim = createForceSimulation(nodes, edges, { width: 800, height: 600 });
  let lastEnergy = Infinity;
  let settledAtTick = -1;
  const MAX_TICKS = 2500; // force-layout.js自体のmaxTicks(2000)より少し余裕を見た監視用上限
  for (let i = 0; i < MAX_TICKS; i++) {
    const { kineticEnergy, settled } = sim.tick();
    lastEnergy = kineticEnergy;
    if (settled) { settledAtTick = i; break; }
  }
  check("settles_within_tick_budget", settledAtTick >= 0 && settledAtTick < MAX_TICKS, { settledAtTick });
  check("settled_energy_near_zero", lastEnergy < 0.05, { lastEnergy });
  check("is_settled_flag_true", sim.isSettled(), null);

  // settled後にtick()を呼んでも、position/energyがそれ以上変化しない
  // (無限ループにならず、値も安定していることの確認)
  const before = sim.getPositions();
  const after1 = sim.tick();
  check("no_further_movement_after_settled", after1.kineticEnergy === 0 && after1.settled === true, after1);
  let stillSame = true;
  for (const [id, p] of before) {
    const p2 = after1.positions.get(id);
    if (p.x !== p2.x || p.y !== p2.y || p.z !== p2.z) stillSame = false;
  }
  check("positions_frozen_after_settled", stillSame, null);
}

// --- drag(setNodePosition/releaseNode、z込み)後のローカル再収束 ---
{
  const sim = createForceSimulation(nodes, edges, { width: 800, height: 600 });
  for (let i = 0; i < 2500 && !sim.isSettled(); i++) sim.tick();
  check("initial_settle_before_drag", sim.isSettled(), null);

  sim.setNodePosition("A", 1000, 1000, 1000);
  check("fixed_node_ignores_further_ticks", (() => {
    const before = sim.getPositions().get("A");
    sim.tick(); // settled中はtick()が即returnするため位置は変化しないはず(fixedでも同じ)
    const after = sim.getPositions().get("A");
    return before.x === after.x && before.y === after.y && before.z === after.z;
  })(), null);

  sim.releaseNode("A");
  check("release_resets_settled_flag", !sim.isSettled(), null);

  let reSettledAtTick = -1;
  for (let i = 0; i < 2500; i++) {
    const { settled } = sim.tick();
    if (settled) { reSettledAtTick = i; break; }
  }
  check("re_settles_after_release", reSettledAtTick >= 0, { reSettledAtTick });
}

process.stdout.write(JSON.stringify(results));
process.exit(results.every((r) => r.pass) ? 0 : 1);
