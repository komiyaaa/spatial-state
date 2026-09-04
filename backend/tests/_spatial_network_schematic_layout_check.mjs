// backend/tests/_spatial_network_schematic_layout_check.mjs
//
// spatial-network/schematic-layout.js の computeSchematicLayout() を検証する
// 自己完結型テストハーネス。閉路(cycle)・同一ノード間の冗長edge(平行辺)を
// 含む入力でも、tree構造を仮定せずクラッシュせず、かつ同一入力を2回実行
// すると出力が完全に一致する(決定性)ことを確認する。
//
// 2026-09-25: 実データ(G002↔T207、G002↔T208)で「一直線に見える」ことが
// 判明した回帰確認として、星形(star)topologyでG002からT207/T208が
// 分岐して見えること(同じX=depth、異なるY)を明示的にアサートするケースを
// 追加した。単体では実行しない(pytest経由の利用を想定)。
import { buildGraphModel } from "../../spatial-network/graph-model.js";
import { computeSchematicLayout } from "../../spatial-network/schematic-layout.js";

const results = [];
function check(label, condition, detail) {
  results.push({ label, pass: !!condition, detail: detail ?? null });
}

function positionsEqual(a, b) {
  if (a.size !== b.size) return false;
  for (const [id, p1] of a) {
    const p2 = b.get(id);
    if (!p2 || p1.x !== p2.x || p1.y !== p2.y) return false;
  }
  return true;
}

// 閉路(1-2, 2-3, 3-1)+ 冗長edge(1-2が2本)+ 2つの独立したGlobal anchor
const localSpaces = [{ space_id: "s1" }, { space_id: "s2" }, { space_id: "s3" }, { space_id: "isolated" }];
const endpoints = [
  { endpoint_id: "epA", type: "GLOBAL", global_spatial_id: "16/0/100/100" },
  { endpoint_id: "epB", type: "GLOBAL", global_spatial_id: "16/0/999/999" },
  { endpoint_id: "epL1", type: "LOCAL", space_id: "s1", local_spatial_id: "9/0/0/0" },
  { endpoint_id: "epL3", type: "LOCAL", space_id: "s3", local_spatial_id: "9/0/0/0" },
];
function conn(id, aSpace, bRef, correspondences = []) {
  return {
    connection_id: id, building_id: "b1",
    endpoint_space_a: { type: "LOCAL", space_id: aSpace },
    endpoint_space_b: bRef,
    correspondences, connection_type: "other", solution: { status: "SOLVED" },
  };
}
const connections = [
  conn("c12", "s1", { type: "LOCAL", space_id: "s2" }),
  conn("c23", "s2", { type: "LOCAL", space_id: "s3" }),
  conn("c31", "s3", { type: "LOCAL", space_id: "s1" }),
  conn("c12b", "s1", { type: "LOCAL", space_id: "s2" }), // 冗長edge(同じs1-s2を結ぶ2本目)
  conn("cga", "s1", { type: "GLOBAL", space_id: null }, [{ pair_id: "p1", node_a_id: "epL1", node_b_id: "epA" }]),
  conn("cgb", "s3", { type: "GLOBAL", space_id: null }, [{ pair_id: "p2", node_a_id: "epL3", node_b_id: "epB" }]),
];

const { nodes, edges } = buildGraphModel({ localSpaces, connections, endpoints });
check("fixture_edge_count_includes_redundant", edges.length === 6, edges.map((e) => e.id));

// --- 決定性: 同一入力を2回実行して完全一致 ---
{
  const pos1 = computeSchematicLayout(nodes, edges, null);
  const pos2 = computeSchematicLayout(nodes, edges, null);
  check("determinism_no_resolution_result", positionsEqual(pos1, pos2), {
    pos1: Array.from(pos1.entries()), pos2: Array.from(pos2.entries()),
  });
}

// --- resolutionResultありでも決定性を保つ ---
{
  const resolutionResult = {
    components: [{
      component_id: "s1",
      local_placement: { member_space_ids: ["s1", "s2", "s3"], root_space_id: "s1", status: "RESOLVED" },
      global_resolution: { status: "NO_ANCHOR" },
    }],
  };
  const pos1 = computeSchematicLayout(nodes, edges, resolutionResult);
  const pos2 = computeSchematicLayout(nodes, edges, resolutionResult);
  check("determinism_with_resolution_result", positionsEqual(pos1, pos2), null);

  // 全LOCALノードにpositionが割り当たっていること(閉路・冗長edgeでBFSが
  // 取りこぼさない)
  const allLocalPlaced = ["s1", "s2", "s3", "isolated"].every((id) => pos1.has(id));
  check("all_local_nodes_placed_despite_cycle", allLocalPlaced, Array.from(pos1.keys()));

  // 2つの独立したGlobal anchorノードも両方配置されている
  const bothAnchorsPlaced = pos1.has("epA") && pos1.has("epB");
  check("both_anchor_nodes_placed", bothAnchorsPlaced, Array.from(pos1.keys()));
}

// --- resolutionResultが存在するがcomponents=[]の場合(resolveは実行済みだが
//     どのconnectionもestimateされておらずcomponentが1件も無い、という
//     実データで実際に発生したケース)でも、孤立ノード扱いにせず
//     connectivityで分岐が保たれることの回帰確認 ---
{
  const emptyComponentsResult = { components: [] };
  const pos = computeSchematicLayout(nodes, edges, emptyComponentsResult);
  const s1 = pos.get("s1");
  const s2 = pos.get("s2");
  const s3 = pos.get("s3");
  check("empty_components_not_all_isolated_at_x0", !(s1.x === s2.x && s2.x === s3.x), { s1, s2, s3 });
  check("empty_components_all_nodes_placed", ["s1", "s2", "s3", "isolated", "epA", "epB"].every((id) => pos.has(id)), null);
}

// --- 星形topology(G002↔T207、G002↔T208)がbranchingして見えることの回帰確認 ---
{
  const starLocalSpaces = [{ space_id: "G002" }, { space_id: "T207" }, { space_id: "T208" }];
  const starConnections = [
    conn("cA", "G002", { type: "LOCAL", space_id: "T207" }),
    conn("cB", "G002", { type: "LOCAL", space_id: "T208" }),
  ];
  const star = buildGraphModel({ localSpaces: starLocalSpaces, connections: starConnections, endpoints: [] });
  const starPos = computeSchematicLayout(star.nodes, star.edges, null);
  const g002 = starPos.get("G002");
  const t207 = starPos.get("T207");
  const t208 = starPos.get("T208");

  check("star_children_share_same_depth_x", t207.x === t208.x, { t207, t208 });
  check("star_children_have_different_y", t207.y !== t208.y, { t207, t208 });
  check("star_root_not_on_same_column_as_children", g002.x !== t207.x, { g002, t207 });

  // 3点が一直線に並んでいない(「G002 — T207 — T208」という以前のバグの
  // 再現防止)。外積が0なら3点は共線(collinear)。
  const cross = (t207.x - g002.x) * (t208.y - g002.y) - (t208.x - g002.x) * (t207.y - g002.y);
  check("star_nodes_not_collinear", Math.abs(cross) > 1e-9, { cross, g002, t207, t208 });

  // determinism: 同じ星形入力を2回計算しても一致する
  const starPos2 = computeSchematicLayout(star.nodes, star.edges, null);
  check("star_layout_deterministic", positionsEqual(starPos, starPos2), null);
}

// --- force-layout.jsを一切importしていないことの静的確認
//     (ヘッダーコメント中の言及は許容し、実際のimport文だけを見る) ---
{
  const source = await (await import("node:fs/promises")).readFile(
    new URL("../../spatial-network/schematic-layout.js", import.meta.url),
    "utf-8",
  );
  const hasImportStatement = /^\s*import\b[^\n]*force-layout\.js/m.test(source);
  check("does_not_import_force_layout", !hasImportStatement, null);
}

process.stdout.write(JSON.stringify(results));
process.exit(results.every((r) => r.pass) ? 0 : 1);
