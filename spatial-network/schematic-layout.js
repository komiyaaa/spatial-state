/**
 * spatial-network/schematic-layout.js
 *
 * Spatial Network View「Layout View」(論文の図としてそのまま使える2D
 * レイアウト)用の、完全に決定的(deterministic)なレイアウトアルゴリズム。
 *
 * 【2026-09-25: BFS訪問順の1次元配置 → 分岐(branching)を保持するtree
 * layoutへ変更】
 * 以前の実装はcomponent内をBFS訪問順でそのまま1列(x=colIndex)に並べて
 * いたため、実際には星形(例: G002↔T207、G002↔T208)のadjacencyでも
 * `G002 — T207 — T208`という直線に見えてしまい、実際のtopologyと異なる
 * 関係を視覚的に示してしまっていた(ユーザー指摘による修正)。
 *
 * 変更後は、BFSで求めた各ノードの「深さ(depth、root=0)」を**X軸**、
 * 「兄弟順序(葉をDFS後順で連番)」を**Y軸**に割り当てる、単純な決定的
 * tree layoutを使う。親のY = 子のYの平均。これにより、G002(depth0)から
 * T207・T208(共にdepth1、Yが異なる)が上下に分岐して見える
 * (`G002 ---- T207` / `G002 ---- T208`のようなY方向の分岐)。
 *
 * 【重要: このtree構造は座標計算のためだけの一時的なspanning treeである】
 * BFSはあくまで「各ノードの表示位置」を1通りに確定させるための決定的な
 * 手続きであり、graph topologyのsource of truthは引き続き`graph-model.js`
 * が返す全edgesである。このモジュールはBFSで求めた木構造をモジュール
 * 内部のローカル変数としてのみ使い、`{nodes, edges}`自体を書き換えたり
 * 親子関係を新しいデータとして返り値に含めたりしない(戻り値は座標
 * `Map<id,{x,y}>`のみ)。閉路(cycle)・冗長edge(平行辺)を含む全edgeは、
 * 呼び出し側(svg-renderer.js)がこのモジュールの返す座標を使ってそのまま
 * 描画するだけであり、tree辺/非tree辺で描画を区別すること(太さを変える
 * 等)は一切行わない — すべてのedgeが対等に「実在するNodalConnection」
 * として読めることを維持する。
 *
 * 【force-layout.jsとは独立】
 * このファイルは`force-layout.js`を一切importしない(依存禁止)。
 * 論文用途では「同じデータを渡せば毎回同じ図になる」ことが必須のため、
 * `Math.random`・`Date.now`・ソートされていないMap/Set/オブジェクトkey順
 * への依存は一切使わない(並び順を決める箇所は必ず明示的にソートしてから
 * 使う)。
 *
 * 【グループ分け】
 * Spatial Resolution結果(resolutionResult)があれば、component_id単位で
 * Y方向のoffset帯を割り当てる。無ければ、LOCAL↔LOCAL edgeのみを使った
 * union-findで接続成分を求め、それでも同じ形の帯分けを行う
 * (Integrated Viewの「404 = 未resolveでも何かしら描画する」姿勢を踏襲)。
 * GLOBALノードは単一の固定位置に集約せず、それを参照しているLOCAL↔GLOBAL
 * edgeが属するcomponentの集合から、X=参照ノードの最大depth+オフセット、
 * Y=参照している各componentの代表Y(root位置)の平均、へ個別に配置する
 * (複数componentから参照されているGLOBALノードも、ノードを複製せず
 * 1箇所に固定する)。
 */

const COLUMN_SPACING = 160; // depth軸(X)の間隔
const ROW_SPACING = 90; // 兄弟軸(Y)の間隔
const COMPONENT_GAP_ROWS = 1; // component間のY方向の空き(行数換算)
const GLOBAL_LANE_GAP_COLUMNS = 2;
const GLOBAL_MIN_Y_GAP = 40;

function compareStrings(a, b) {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

/** LOCAL↔LOCAL edgeのみを使い、union-findで接続成分を求める
 * (resolutionResultが無い場合のフォールバック)。componentIdはbackendの
 * ComponentPlacementResult.component_idと同じ規約(= member_space_ids
 * の辞書順最小値)に揃える。 */
function unionFindGroups(localNodes, localEdges) {
  const parent = new Map();
  function find(x) {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));
      x = parent.get(x);
    }
    return x;
  }
  function union(a, b) {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  }
  for (const node of localNodes) parent.set(node.id, node.id);
  for (const edge of localEdges) {
    if (parent.has(edge.sourceId) && parent.has(edge.targetId)) union(edge.sourceId, edge.targetId);
  }

  const membersByRoot = new Map();
  for (const node of localNodes) {
    const root = find(node.id);
    if (!membersByRoot.has(root)) membersByRoot.set(root, []);
    membersByRoot.get(root).push(node.id);
  }

  const groups = [];
  for (const members of membersByRoot.values()) {
    members.sort(compareStrings);
    const componentId = members[0];
    groups.push({ componentId, memberIds: members, rootId: componentId });
  }
  return groups;
}

/** BFS(隣接edgeはconnection_idでソートしてから辿る、配列到着順に依存
 * しない)で、1つのcomponent内の各ノードのdepth(root=0)と、レイアウト
 * 専用の一時的なspanning tree(親→子)を求める。この木は戻り値として
 * 外部へは一切出さない(呼び出し元のローカル計算にのみ使う)。 */
function computeBfsTree(memberIds, rootId, localEdges) {
  const memberSet = new Set(memberIds);
  const adjacency = new Map();
  for (const id of memberIds) adjacency.set(id, []);
  for (const edge of localEdges) {
    if (memberSet.has(edge.sourceId) && memberSet.has(edge.targetId)) {
      adjacency.get(edge.sourceId).push({ neighbor: edge.targetId, key: edge.connectionId });
      adjacency.get(edge.targetId).push({ neighbor: edge.sourceId, key: edge.connectionId });
    }
  }
  for (const list of adjacency.values()) list.sort((a, b) => compareStrings(a.key, b.key));

  const sortedMembers = memberIds.slice().sort(compareStrings);
  const start = memberSet.has(rootId) ? rootId : sortedMembers[0];

  const depthById = new Map([[start, 0]]);
  const childrenById = new Map();
  for (const id of memberIds) childrenById.set(id, []);

  const visited = new Set([start]);
  const queue = [start];
  while (queue.length > 0) {
    const current = queue.shift();
    for (const { neighbor } of adjacency.get(current) || []) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        depthById.set(neighbor, depthById.get(current) + 1);
        childrenById.get(current).push(neighbor);
        queue.push(neighbor);
      }
    }
  }
  // BFSが到達しなかったmember(理論上起きないはずの防御)は、rootの直下
  // (depth1)として扱い、取りこぼさない。
  for (const id of sortedMembers) {
    if (!visited.has(id)) {
      depthById.set(id, 1);
      childrenById.get(start).push(id);
    }
  }

  return { root: start, depthById, childrenById };
}

/** 兄弟順序(Y)を、葉のDFS後順連番で決定的に求める。親のY = 子のYの平均。
 * 葉から順に採番するため、同じ深さの兄弟同士のYが重ならない
 * (実際のadjacency/branchingが視覚的に保持される)。 */
function layoutSiblingPositions(root, childrenById) {
  const yById = new Map();
  let nextLeafSlot = 0;

  function visit(nodeId) {
    const children = childrenById.get(nodeId) || [];
    if (children.length === 0) {
      const y = nextLeafSlot;
      nextLeafSlot += 1;
      yById.set(nodeId, y);
      return y;
    }
    const childYs = children.map((c) => visit(c));
    const y = childYs.reduce((sum, v) => sum + v, 0) / childYs.length;
    yById.set(nodeId, y);
    return y;
  }
  visit(root);

  return { yById, leafCount: Math.max(nextLeafSlot, 1) };
}

/**
 * @param {Array<object>} nodes - graph-model.jsのbuildGraphModel()が返すnodes
 * @param {Array<object>} edges - 同edges(tree/非treeの区別なく、全edgeが
 *   呼び出し側でそのまま描画される前提)
 * @param {{components: Array<object>}|null} resolutionResult
 *   GET /api/spatial-resolution/results/<building_id> の { result }(404時はnull)
 * @returns {Map<string, {x: number, y: number}>}
 */
export function computeSchematicLayout(nodes, edges, resolutionResult) {
  const localNodes = (nodes || []).filter((n) => n.kind === "LOCAL");
  const globalNodes = (nodes || []).filter((n) => n.kind === "GLOBAL");
  const localIdSet = new Set(localNodes.map((n) => n.id));
  const globalIdSet = new Set(globalNodes.map((n) => n.id));
  const localEdges = (edges || []).filter((e) => localIdSet.has(e.sourceId) && localIdSet.has(e.targetId));

  // Spatial Resolution結果に含まれるcomponent(local_placementがRESOLVED
  // まで到達したもの)はそれをそのまま使う。それ以外のLOCALノード
  // (Spatial Resolutionが一度も実行されていない、またはそのconnectionが
  // 一度もestimateされておらずcomponentが1件も無い、等の理由でどの
  // componentにも含まれていないもの)は、実際のLOCAL↔LOCAL edgeの
  // connectivity(union-find)でグループ化する。「まだestimate/resolve
  // されていないconnectionも、実在するNodalConnectionとしてadjacencyには
  // 反映する」というLayout Viewの方針に合わせるための挙動であり、
  // 該当ノードを孤立ノード(singleton)として扱って直線/分岐が失われる
  // ことを防ぐ(2026-09-25、実データでcomponents=[]のケースが実際に
  // 発生することが判明したための修正)。
  const groups = [];
  const covered = new Set();
  if (resolutionResult && Array.isArray(resolutionResult.components)) {
    for (const c of resolutionResult.components) {
      const memberIds = (c.local_placement.member_space_ids || []).slice();
      groups.push({ componentId: c.component_id, memberIds, rootId: c.local_placement.root_space_id });
      for (const id of memberIds) covered.add(id);
    }
  }
  const uncoveredNodes = localNodes.filter((n) => !covered.has(n.id));
  if (uncoveredNodes.length > 0) {
    const uncoveredEdges = localEdges.filter((e) => !covered.has(e.sourceId) && !covered.has(e.targetId));
    groups.push(...unionFindGroups(uncoveredNodes, uncoveredEdges));
  }
  groups.sort((a, b) => compareStrings(a.componentId, b.componentId));

  const positions = new Map();
  const componentIdBySpace = new Map();
  const depthBySpace = new Map();
  const rootYByComponent = new Map();
  let yCursor = 0;

  groups.forEach((group) => {
    for (const id of group.memberIds) componentIdBySpace.set(id, group.componentId);

    const { root, depthById, childrenById } = computeBfsTree(group.memberIds, group.rootId, localEdges);
    const { yById, leafCount } = layoutSiblingPositions(root, childrenById);
    const yOffset = yCursor;

    for (const id of group.memberIds) {
      const depth = depthById.has(id) ? depthById.get(id) : 0;
      const yLocal = yById.has(id) ? yById.get(id) : 0;
      depthBySpace.set(id, depth);
      positions.set(id, { x: depth * COLUMN_SPACING, y: (yOffset + yLocal) * ROW_SPACING });
    }
    rootYByComponent.set(group.componentId, positions.get(root).y);

    yCursor += leafCount + COMPONENT_GAP_ROWS;
  });

  // GLOBALノード: X = 参照しているLOCALノードの中の最大depth + 固定オフセット列、
  // Y = 参照している各componentの代表Y(root位置)の平均(衝突時はY方向へずらす、
  // 現行ロジックを踏襲)。
  const referencingComponentsByGlobal = new Map();
  const maxDepthByGlobal = new Map();
  for (const edge of edges || []) {
    let globalId = null;
    let localId = null;
    if (globalIdSet.has(edge.sourceId)) { globalId = edge.sourceId; localId = edge.targetId; }
    else if (globalIdSet.has(edge.targetId)) { globalId = edge.targetId; localId = edge.sourceId; }
    if (!globalId) continue;
    const componentId = componentIdBySpace.get(localId);
    if (componentId == null) continue;

    if (!referencingComponentsByGlobal.has(globalId)) referencingComponentsByGlobal.set(globalId, new Set());
    referencingComponentsByGlobal.get(globalId).add(componentId);

    const depth = depthBySpace.get(localId) || 0;
    maxDepthByGlobal.set(globalId, Math.max(maxDepthByGlobal.get(globalId) || 0, depth));
  }

  const usedYs = [];
  const sortedGlobalIds = globalNodes.map((n) => n.id).slice().sort(compareStrings);
  for (const globalId of sortedGlobalIds) {
    const referencingComponentIds = Array.from(referencingComponentsByGlobal.get(globalId) || []).sort(compareStrings);
    let x;
    let y;
    if (referencingComponentIds.length === 0) {
      x = 0;
      y = 0; // 参照edgeが無い(graph-model.js上は起きないはずの防御的フォールバック)
    } else {
      x = ((maxDepthByGlobal.get(globalId) || 0) + GLOBAL_LANE_GAP_COLUMNS) * COLUMN_SPACING;
      const ys = referencingComponentIds.map((cid) => rootYByComponent.get(cid)).sort((a, b) => a - b);
      let sum = 0;
      for (const v of ys) sum += v;
      y = sum / ys.length;
    }
    while (usedYs.some((u) => Math.abs(u - y) < GLOBAL_MIN_Y_GAP)) y += GLOBAL_MIN_Y_GAP;
    usedYs.push(y);
    positions.set(globalId, { x, y });
  }

  return positions;
}
