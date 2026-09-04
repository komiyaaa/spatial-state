// backend/tests/_spatial_network_graph_model_check.mjs
//
// spatial-network/graph-model.js の buildGraphModel() を検証する自己完結型
// テストハーネス。test_spatial_network_graph_model_js.py から
// `node _spatial_network_graph_model_check.mjs` として呼ばれる。
// point_to_spatial_id.pyのような対応するPython実装は無い(このロジックは
// frontend専用のグラフ構築であり、backend側に「正」となる実装は存在しない)
// ため、既知の期待値をこのファイル内にハードコードした自己完結型アサーション
// にする(cross-language parityチェックではない)。単体では実行しない
// (pytest経由の利用を想定)。
import { buildGraphModel } from "../../spatial-network/graph-model.js";

const results = [];
function check(label, condition, detail) {
  results.push({ label, pass: !!condition, detail: detail ?? null });
}

// --- (a) LOCAL-LOCAL / LOCAL-GLOBAL混在の基本形 ---
{
  const localSpaces = [{ space_id: "s1" }, { space_id: "s2" }];
  const endpoints = [
    { endpoint_id: "epG", type: "GLOBAL", global_spatial_id: "16/0/100/100" },
    { endpoint_id: "epL1", type: "LOCAL", space_id: "s1", local_spatial_id: "9/0/0/0" },
  ];
  const connections = [
    {
      connection_id: "c-ll", building_id: "b1",
      endpoint_space_a: { type: "LOCAL", space_id: "s1" },
      endpoint_space_b: { type: "LOCAL", space_id: "s2" },
      correspondences: [], connection_type: "other", solution: { status: "SOLVED" },
    },
    {
      connection_id: "c-lg", building_id: "b1",
      endpoint_space_a: { type: "LOCAL", space_id: "s1" },
      endpoint_space_b: { type: "GLOBAL", space_id: null },
      correspondences: [{ pair_id: "p1", node_a_id: "epL1", node_b_id: "epG" }],
      connection_type: "other", solution: { status: "SOLVED" },
    },
  ];
  const { nodes, edges, skippedConnections } = buildGraphModel({ localSpaces, connections, endpoints });
  check("basic_node_count", nodes.length === 3, { nodes: nodes.map((n) => n.id) });
  check("basic_edge_count", edges.length === 2, { edges: edges.map((e) => `${e.sourceId}->${e.targetId}`) });
  check("basic_no_skipped", skippedConnections.length === 0, skippedConnections);
}

// --- (b) 孤立ノード(接続0件のLocal Space) ---
{
  const localSpaces = [{ space_id: "s1" }, { space_id: "isolated" }];
  const { nodes, edges } = buildGraphModel({ localSpaces, connections: [], endpoints: [] });
  check("isolated_node_present", nodes.some((n) => n.id === "isolated"), nodes.map((n) => n.id));
  check("isolated_no_edges", edges.length === 0, edges);
}

// --- (c) 同一のGLOBAL endpoint_idを複数connectionが参照 → ノード1個に重複排除 ---
{
  const localSpaces = [{ space_id: "s1" }, { space_id: "s2" }];
  const endpoints = [
    { endpoint_id: "epG", type: "GLOBAL", global_spatial_id: "16/0/1/1" },
    { endpoint_id: "epL1", type: "LOCAL", space_id: "s1", local_spatial_id: "9/0/0/0" },
    { endpoint_id: "epL2", type: "LOCAL", space_id: "s2", local_spatial_id: "9/0/0/0" },
  ];
  const connections = [
    {
      connection_id: "c1", building_id: "b1",
      endpoint_space_a: { type: "LOCAL", space_id: "s1" }, endpoint_space_b: { type: "GLOBAL", space_id: null },
      correspondences: [{ pair_id: "p1", node_a_id: "epL1", node_b_id: "epG" }],
      connection_type: "other", solution: { status: "SOLVED" },
    },
    {
      connection_id: "c2", building_id: "b1",
      endpoint_space_a: { type: "LOCAL", space_id: "s2" }, endpoint_space_b: { type: "GLOBAL", space_id: null },
      correspondences: [{ pair_id: "p2", node_a_id: "epL2", node_b_id: "epG" }],
      connection_type: "other", solution: { status: "SOLVED" },
    },
  ];
  const { nodes, edges } = buildGraphModel({ localSpaces, connections, endpoints });
  const globalNodes = nodes.filter((n) => n.kind === "GLOBAL");
  check("dedup_single_global_node", globalNodes.length === 1 && globalNodes[0].id === "epG", globalNodes);
  check("dedup_two_edges_to_same_anchor", edges.length === 2 && edges.every((e) => e.targetId === "epG"), edges);
}

// --- (d) 異なるglobal_spatial_idの2つの独立したGLOBAL endpointは別ノードのまま、
//         直接結ぶedge/pathは一切生じない(このセッションでの中心的な回帰確認)---
{
  const localSpaces = [{ space_id: "s1" }, { space_id: "s2" }];
  const endpoints = [
    { endpoint_id: "epA", type: "GLOBAL", global_spatial_id: "16/0/100/100" },
    { endpoint_id: "epB", type: "GLOBAL", global_spatial_id: "16/0/999/999" },
    { endpoint_id: "epL1", type: "LOCAL", space_id: "s1", local_spatial_id: "9/0/0/0" },
    { endpoint_id: "epL2", type: "LOCAL", space_id: "s2", local_spatial_id: "9/0/0/0" },
  ];
  const connections = [
    {
      connection_id: "cga", building_id: "b1",
      endpoint_space_a: { type: "LOCAL", space_id: "s1" }, endpoint_space_b: { type: "GLOBAL", space_id: null },
      correspondences: [{ pair_id: "p1", node_a_id: "epL1", node_b_id: "epA" }],
      connection_type: "other", solution: { status: "SOLVED" },
    },
    {
      connection_id: "cgb", building_id: "b1",
      endpoint_space_a: { type: "LOCAL", space_id: "s2" }, endpoint_space_b: { type: "GLOBAL", space_id: null },
      correspondences: [{ pair_id: "p2", node_a_id: "epL2", node_b_id: "epB" }],
      connection_type: "other", solution: { status: "SOLVED" },
    },
  ];
  const { nodes, edges } = buildGraphModel({ localSpaces, connections, endpoints });
  const globalNodes = nodes.filter((n) => n.kind === "GLOBAL");
  const hasBothAnchors = globalNodes.some((n) => n.id === "epA") && globalNodes.some((n) => n.id === "epB");
  check("two_anchors_stay_distinct", globalNodes.length === 2 && hasBothAnchors, globalNodes);
  const phantomEdge = edges.some(
    (e) => (e.sourceId === "epA" && e.targetId === "epB") || (e.sourceId === "epB" && e.targetId === "epA"),
  );
  check("no_phantom_edge_between_anchors", !phantomEdge, edges.map((e) => `${e.sourceId}->${e.targetId}`));
}

// --- (e) correspondences 0件のLOCAL↔GLOBAL connectionはedge化せずskippedへ ---
{
  const localSpaces = [{ space_id: "s1" }];
  const connections = [
    {
      connection_id: "c-empty", building_id: "b1",
      endpoint_space_a: { type: "LOCAL", space_id: "s1" }, endpoint_space_b: { type: "GLOBAL", space_id: null },
      correspondences: [], connection_type: "other", solution: { status: "UNSOLVED" },
    },
  ];
  const { nodes, edges, skippedConnections } = buildGraphModel({ localSpaces, connections, endpoints: [] });
  check("no_correspondence_no_global_node", nodes.every((n) => n.kind !== "GLOBAL"), nodes);
  check("no_correspondence_no_edge", edges.length === 0, edges);
  check(
    "no_correspondence_skipped_recorded",
    skippedConnections.length === 1 && skippedConnections[0].connectionId === "c-empty"
      && skippedConnections[0].reason === "NO_CORRESPONDENCES",
    skippedConnections,
  );
}

// --- (f) 空建物 ---
{
  const { nodes, edges, skippedConnections } = buildGraphModel({ localSpaces: [], connections: [], endpoints: [] });
  check("empty_building", nodes.length === 0 && edges.length === 0 && skippedConnections.length === 0,
    { nodes, edges, skippedConnections });
}

// --- (g) 自己ループ防御(本来backendが禁止する形だが、防御的にJS側でも
//         source===targetのedgeを作らないことを確認する) ---
{
  const localSpaces = [{ space_id: "s1" }];
  const connections = [
    {
      connection_id: "c-self", building_id: "b1",
      endpoint_space_a: { type: "LOCAL", space_id: "s1" }, endpoint_space_b: { type: "LOCAL", space_id: "s1" },
      correspondences: [], connection_type: "other", solution: { status: "UNSOLVED" },
    },
  ];
  const { edges } = buildGraphModel({ localSpaces, connections, endpoints: [] });
  check("self_loop_filtered", edges.length === 0, edges);
}

process.stdout.write(JSON.stringify(results));
process.exit(results.every((r) => r.pass) ? 0 : 1);
