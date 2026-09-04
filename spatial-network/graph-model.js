/**
 * spatial-network/graph-model.js
 *
 * Nodal Information(NodalEndpoint/NodalConnection、既存API)から取得した
 * 生JSONを、Spatial Network View用の正規化されたグラフ({nodes, edges})へ
 * 変換する純粋関数。DOM・fetch・THREE.js等への依存は一切無い(Node.jsからも
 * そのまま実行できる、単体テストは backend/tests/_spatial_network_graph_
 * model_check.mjs 参照)。
 *
 * 【最重要: Global anchorノードは集約しない】
 * 異なるGlobal anchor(異なる`NodalEndpoint.global_spatial_id`を持つ、
 * 別々のNodalEndpointレコード)は、それぞれ独立したノードとして保持する。
 * 「type=GLOBALのconnectionは全部1つのGLOBALノードに潰す」という設計は、
 * 本来存在しないedge/pathをグラフ上に生じさせるため禁止する(例:
 * 「Global Anchor A ↔ Local Space 1」「Local Space 2 ↔ Global Anchor B」が
 * 別々の証拠であるにもかかわらず、AとBを同一ノードに潰すと「Space 1と
 * Space 2はGlobal経由で繋がっている」という誤ったtopologyを示してしまう)。
 * ノードのidentityには、既存のNodal Informationが既に持っている一意key
 * である`NodalEndpoint.endpoint_id`をそのまま使う(新しいsource of truthは
 * 作らない)。
 *
 * `NodalConnection.endpoint_space_a/b`は`{type, space_id}`のみでGLOBAL側の
 * 個別identityを持たない(space_idはLOCAL専用)ため、どのGLOBAL endpointが
 * 実際に使われているかは`correspondences[].node_a_id/node_b_id`を
 * NodalEndpointレコードへ解決して初めて分かる。そのため呼び出し側は、
 * `buildGraphModel()`に`endpoints`(GET /api/nodal-endpointsの結果)も
 * 渡す必要がある(このAPIはbuilding_idで絞れないため全件渡してよい。
 * ここで使うのは実際にconnections[].correspondencesが参照している
 * endpoint_idだけ)。
 *
 * Nodal Information/Spatial Resolutionのロジックには一切関与しない
 * (読み取ったデータをグラフ形状へ変換するだけ)。
 */

/**
 * @typedef {{id: string, kind: "LOCAL", spaceRecord: object}} LocalGraphNode
 * @typedef {{id: string, kind: "GLOBAL", label: string, endpointRecord: object|null}} GlobalGraphNode
 * @typedef {{
 *   id: string,
 *   connectionId: string,
 *   sourceId: string,
 *   targetId: string,
 *   connectionType: string,
 *   solutionStatus: string,
 *   raw: object,
 * }} GraphEdge
 */

/**
 * @param {object} params
 * @param {Array<object>} params.localSpaces - GET /api/buildings/<id>/local-spaces の local_spaces[]
 * @param {Array<object>} params.connections - GET /api/nodal-connections?building_id=X の connections[]
 * @param {Array<object>} params.endpoints - GET /api/nodal-endpoints の endpoints[]
 *   (building_idで絞られていない全件でよい)
 * @returns {{
 *   nodes: Array<LocalGraphNode|GlobalGraphNode>,
 *   edges: Array<GraphEdge>,
 *   skippedConnections: Array<{connectionId: string, reason: string}>,
 * }}
 */
export function buildGraphModel({ localSpaces, connections, endpoints }) {
  const endpointById = new Map((endpoints || []).map((e) => [e.endpoint_id, e]));

  const nodes = [];
  const nodeIds = new Set();

  for (const space of localSpaces || []) {
    if (nodeIds.has(space.space_id)) continue; // 防御的(space_idは本来一意)
    nodeIds.add(space.space_id);
    nodes.push({ id: space.space_id, kind: "LOCAL", spaceRecord: space });
  }

  function ensureGlobalNode(endpointId) {
    if (nodeIds.has(endpointId)) return;
    const ep = endpointById.get(endpointId);
    nodeIds.add(endpointId);
    nodes.push({
      id: endpointId,
      kind: "GLOBAL",
      label: ep ? ep.global_spatial_id : endpointId,
      endpointRecord: ep || null,
    });
  }

  const edges = [];
  const skippedConnections = [];

  for (const conn of connections || []) {
    const a = conn.endpoint_space_a;
    const b = conn.endpoint_space_b;
    const bothLocal = a.type === "LOCAL" && b.type === "LOCAL";

    if (bothLocal) {
      const sourceId = a.space_id;
      const targetId = b.space_id;
      if (!sourceId || !targetId || sourceId === targetId) {
        console.warn("[spatial-network/graph-model] LOCAL-LOCAL connectionのspace_idが不正なためskip:", conn.connection_id);
        continue;
      }
      edges.push({
        id: conn.connection_id,
        connectionId: conn.connection_id,
        sourceId,
        targetId,
        connectionType: conn.connection_type,
        solutionStatus: conn.solution ? conn.solution.status : "UNSOLVED",
        raw: conn,
      });
      continue;
    }

    // LOCAL↔GLOBAL(理論上のGLOBAL↔GLOBALも同じ経路で防御的に扱う)。
    // connection単体ではどのGLOBAL endpointか分からないため、
    // correspondencesを実際のNodalEndpointへ解決する。
    const correspondences = conn.correspondences || [];
    if (correspondences.length === 0) {
      // まだ対応点未設定 = どのGlobal anchorの証拠かも判定できない。
      // anchorノードを推測・捏造しない(fail-closed)。
      skippedConnections.push({ connectionId: conn.connection_id, reason: "NO_CORRESPONDENCES" });
      continue;
    }

    const localSideSpaceId = a.type === "LOCAL" ? a.space_id : (b.type === "LOCAL" ? b.space_id : null);

    const globalEndpointIds = new Set();
    for (const corr of correspondences) {
      for (const epId of [corr.node_a_id, corr.node_b_id]) {
        const ep = endpointById.get(epId);
        if (ep && ep.type === "GLOBAL") globalEndpointIds.add(epId);
      }
    }

    if (globalEndpointIds.size === 0) {
      // correspondencesはあるが、参照先のGLOBAL endpointレコードが
      // endpoints[]の中に見つからない(未取得/削除済み等)。
      skippedConnections.push({ connectionId: conn.connection_id, reason: "GLOBAL_ENDPOINT_NOT_FOUND" });
      continue;
    }
    if (!localSideSpaceId) {
      // 現行UIでは作れないはずのGLOBAL↔GLOBAL接続への防御。
      skippedConnections.push({ connectionId: conn.connection_id, reason: "NO_LOCAL_SIDE" });
      continue;
    }

    // 1つのconnectionが複数の異なるGLOBAL endpointを参照していれば、
    // その分だけedgeを分けて生成する(集約・丸めはしない)。
    for (const globalEndpointId of globalEndpointIds) {
      if (localSideSpaceId === globalEndpointId) {
        console.warn("[spatial-network/graph-model] source===targetのため異常edgeをskip:", conn.connection_id);
        continue;
      }
      ensureGlobalNode(globalEndpointId);
      edges.push({
        id: `${conn.connection_id}:${globalEndpointId}`,
        connectionId: conn.connection_id,
        sourceId: localSideSpaceId,
        targetId: globalEndpointId,
        connectionType: conn.connection_type,
        solutionStatus: conn.solution ? conn.solution.status : "UNSOLVED",
        raw: conn,
      });
    }
  }

  return { nodes, edges, skippedConnections };
}
