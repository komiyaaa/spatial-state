/**
 * spatial-network/force-layout.js
 *
 * Spatial Network View「Network View」(暗い3次元空間にnodeが浮遊する
 * constellation的表現)用の、自前のforce-directed simulation(新規
 * ライブラリ追加なし、frontend-only / display-only)。
 *
 * 【2026-09-25: 2D(x,y)から3D(x,y,z)へ拡張】
 * Network ViewがThree.jsによる3D表示になったことに伴い、position計算も
 * x/y/zの3軸へ拡張した。反発力・ばね力・中心引力・減衰・収束判定の
 * 式自体は変更せず、次元を1つ増やしただけ(z成分にも同じ式を適用する)。
 * 2D Layout View(schematic-layout.js)は元々このモジュールを一切
 * importしていないため、この変更による影響は無い。
 *
 * 【厳守事項(ユーザー指示)】
 * - Nodal Information・Spatial Resolutionへは一切書き戻さない
 *   (このモジュールはpositionを計算するだけで、fetchは一切行わない)。
 * - 初期配置は決定的にする(node idを辞書順ソートしてFibonacci sphere
 *   格子(球面上への均等配置、乱数不要の既知アルゴリズム)に並べるだけ→
 *   同じデータなら毎回同じ初期配置)。
 * - 減衰・収束判定を設け、無限に揺れ続けない(運動エネルギーが一定tick
 *   連続で閾値未満になったら`settled=true`。念のため`maxTicks`の
 *   強制上限も設ける)。
 * - node dragの解放後は、全体を作り直さず「適切に再収束する程度」で良い
 *   (alphaパラメータを再加熱してループを再開するだけ)。
 * - 2D Layout View(schematic-layout.js)はこのモジュールを一切使わない
 *   (別の決定的アルゴリズム)。
 * - この3D位置はresolved global placementではない。あくまでtopologyを
 *   探索するためのvisual layoutであり、物理的な意味を一切持たない
 *   (Integrated Viewの物理空間表示とは無関係)。
 *
 * DOM・requestAnimationFrame等は呼び出し側(spatial-network-view.js)の
 * 責務。このモジュールは`tick()`を呼ばれた分だけ1ステップ進める、
 * 純粋に近い状態機械。
 */

/**
 * N個の点を、単位球面上へ均等かつ決定的に配置する(golden angleを使った
 * Fibonacci sphere格子)。乱数・ハッシュは一切使わない
 * (index iとnだけで決まる)。
 * @param {number} i 0始まりのindex
 * @param {number} n 総数
 * @returns {[number, number, number]} 単位球面上の[x,y,z](半径1)
 */
function fibonacciSpherePoint(i, n) {
  if (n <= 1) return [0, 0, 0];
  const goldenAngle = Math.PI * (1 + Math.sqrt(5));
  const phi = Math.acos(1 - (2 * (i + 0.5)) / n); // 極角(0..π)
  const theta = goldenAngle * i; // 方位角
  return [
    Math.sin(phi) * Math.cos(theta),
    Math.sin(phi) * Math.sin(theta),
    Math.cos(phi),
  ];
}

/**
 * @param {Array<{id: string}>} nodes
 * @param {Array<{sourceId: string, targetId: string}>} edges
 * @param {object} [opts]
 * @returns {{
 *   tick: () => {positions: Map<string,{x:number,y:number,z:number}>, kineticEnergy: number, settled: boolean},
 *   setNodePosition: (id: string, x: number, y: number, z: number) => void,
 *   releaseNode: (id: string) => void,
 *   isSettled: () => boolean,
 *   getPositions: () => Map<string,{x:number,y:number,z:number}>,
 * }}
 */
export function createForceSimulation(nodes, edges, opts = {}) {
  const {
    width = 800,
    height = 600,
    repulsion = 1800,
    springConstant = 0.02,
    restLength = 140,
    centeringStrength = 0.01,
    damping = 0.85,
    dt = 1,
    minDistance = 8,
    energyThreshold = 0.02,
    energyStableTicks = 10,
    maxTicks = 2000,
  } = opts;

  // 決定的な初期配置: node idを辞書順ソートし、Fibonacci sphere格子で
  // 球面上へ均等配置する。GLOBALノードが複数あっても特別扱い(中心固定)は
  // しない(単一の"GLOBAL"ノードへ集約しなくなったため、特定ノードを
  // 中心に据える理由が無い。どのノードも対等に扱い、位置関係は力学のみで
  // 決める)。
  const ids = (nodes || []).map((n) => n.id).slice().sort();
  const n = ids.length;
  const radius = Math.max(Math.min(width, height) * 0.35, 80);
  const state = new Map();
  ids.forEach((id, i) => {
    const [ux, uy, uz] = fibonacciSpherePoint(i, n);
    state.set(id, {
      x: ux * radius, y: uy * radius, z: uz * radius,
      vx: 0, vy: 0, vz: 0, fixed: false,
    });
  });

  const edgeList = (edges || [])
    .map((e) => ({ source: e.sourceId, target: e.targetId }))
    .filter((e) => state.has(e.source) && state.has(e.target) && e.source !== e.target);

  let alpha = 1;
  const alphaMin = 0.001;
  const alphaDecay = 0.99;
  let lowEnergyStreak = 0;
  let tickCount = 0;
  let settled = n === 0;

  function getPositions() {
    const out = new Map();
    for (const id of ids) {
      const s = state.get(id);
      out.set(id, { x: s.x, y: s.y, z: s.z });
    }
    return out;
  }

  function tick() {
    if (settled) {
      return { positions: getPositions(), kineticEnergy: 0, settled: true };
    }
    tickCount++;

    const forces = new Map();
    for (const id of ids) forces.set(id, { fx: 0, fy: 0, fz: 0 });

    // 反発力(全ペア、O(n^2)。1建物あたりのnode数は数十〜程度の想定で許容範囲)。
    for (let i = 0; i < ids.length; i++) {
      const a = state.get(ids[i]);
      for (let j = i + 1; j < ids.length; j++) {
        const b = state.get(ids[j]);
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dz = a.z - b.z;
        let dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (!Number.isFinite(dist) || dist < minDistance) dist = minDistance;
        const force = (repulsion * alpha) / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        const fz = (dz / dist) * force;
        const fa = forces.get(ids[i]);
        fa.fx += fx; fa.fy += fy; fa.fz += fz;
        const fb = forces.get(ids[j]);
        fb.fx -= fx; fb.fy -= fy; fb.fz -= fz;
      }
    }

    // ばね力(edgeに沿って。同一ノード間に複数edge=平行辺があっても、
    // それぞれ独立したばねとして加算する)。
    for (const e of edgeList) {
      const a = state.get(e.source);
      const b = state.get(e.target);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dz = b.z - a.z;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.0001;
      const diff = dist - restLength;
      const force = springConstant * diff * alpha;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      const fz = (dz / dist) * force;
      const fa = forces.get(e.source);
      fa.fx += fx; fa.fy += fy; fa.fz += fz;
      const fb = forces.get(e.target);
      fb.fx -= fx; fb.fy -= fy; fb.fz -= fz;
    }

    // 弱い中心引力(全体が画面外へ流出するのを防ぐだけ)。
    for (const id of ids) {
      const s = state.get(id);
      const f = forces.get(id);
      f.fx += -s.x * centeringStrength * alpha;
      f.fy += -s.y * centeringStrength * alpha;
      f.fz += -s.z * centeringStrength * alpha;
    }

    let kineticEnergy = 0;
    for (const id of ids) {
      const s = state.get(id);
      if (s.fixed) { s.vx = 0; s.vy = 0; s.vz = 0; continue; }
      const f = forces.get(id);
      s.vx = (s.vx + f.fx * dt) * damping;
      s.vy = (s.vy + f.fy * dt) * damping;
      s.vz = (s.vz + f.fz * dt) * damping;
      s.x += s.vx * dt;
      s.y += s.vy * dt;
      s.z += s.vz * dt;
      kineticEnergy += s.vx * s.vx + s.vy * s.vy + s.vz * s.vz;
    }

    alpha = Math.max(alphaMin, alpha * alphaDecay);

    if (kineticEnergy < energyThreshold) lowEnergyStreak++;
    else lowEnergyStreak = 0;

    if (lowEnergyStreak >= energyStableTicks || tickCount >= maxTicks) {
      settled = true;
    }

    return { positions: getPositions(), kineticEnergy, settled };
  }

  function setNodePosition(id, x, y, z) {
    const s = state.get(id);
    if (!s) return;
    s.x = x; s.y = y; s.z = z ?? s.z; s.vx = 0; s.vy = 0; s.vz = 0; s.fixed = true;
  }

  /** dragを離した後、全体を作り直さずローカルに再収束させる
   * (alphaを再加熱してループを再開するだけ)。 */
  function releaseNode(id) {
    const s = state.get(id);
    if (!s) return;
    s.fixed = false;
    alpha = Math.max(alpha, 0.3);
    lowEnergyStreak = 0;
    tickCount = 0;
    settled = false;
  }

  function isSettled() {
    return settled;
  }

  return { tick, setNodePosition, releaseNode, isSettled, getPositions };
}
