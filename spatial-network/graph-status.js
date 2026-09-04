/**
 * spatial-network/graph-status.js
 *
 * Spatial Network Viewのnode/edge色分け(SOLVED/WARNING/CONFLICT/ERROR等の
 * 共通design token語彙への統一)。
 *
 * 【既存2ファイルとの関係(意図的に独立実装、2026-09-25)】
 * `nodal/nodal-panel.js`の`STATUS_BADGE_KIND`と`integrated/integrated-view.js`
 * の`resolveComponentDisplayPlan`は、形が異なる(前者はSolutionStatus等の
 * フラットな10key→5kind lookup、後者はtransform/frameLabelまで決める判定
 * 関数)。加えて、このモジュールが必要とする「1つのnodeについて、複数
 * connection/componentを横断した最終ステータス」という集約は、既存の
 * どちらの関数にも無い新規ロジック。無理に共通化せず、独立した3本目として
 * 実装する(色トークン名(--status-*)は完全に一致させ、視覚的にはNodal
 * Information/Integrated Viewと統一する)。3箇所の形が収束したら
 * `shared/`への抽出を検討する。
 *
 * このファイルはNodal Information/Spatial Resolutionの判定ロジックを
 * 一切変更しない(既に確定した`solution.status`/component結果を読んで
 * 表示クラスへ振り分けるだけ)。
 */

// nodal-panel.jsのSTATUS_BADGE_KINDと同じ4値(NodalConnection.solution.status
// の値のみを対象とする、このモジュールで使うのはこの4値だけ)。
const SOLUTION_STATUS_KIND = {
  UNSOLVED: "pending",
  SOLVED: "solved",
  WARNING_HIGH_RESIDUAL: "warning",
  UNSOLVABLE: "error",
};

/**
 * @param {import("./graph-model.js").GraphEdge} edge
 * @returns {{kind: "pending"|"solved"|"warning"|"error", label: string}}
 */
export function classifyEdge(edge) {
  const status = edge.solutionStatus || "UNSOLVED";
  return { kind: SOLUTION_STATUS_KIND[status] || "pending", label: status };
}

/**
 * 1つのcomponent(Spatial Resolution結果)から、そのmemberのLocal Space群に
 * 割り当てるべきbadgeKindを決める。integrated-view.jsのresolveComponentDisplayPlan
 * と同じ判定ラダーをそのまま踏襲する(mode/frameLabelは持たず、kindのみ)。
 */
function componentBadgeKind(component) {
  const lp = component.local_placement;
  const gr = component.global_resolution;

  if (lp.status === "CONFLICT") return "conflict";
  if (gr.status === "GLOBAL_CONFLICT") return "conflict";
  if (gr.status === "BLOCKED_BY_LOCAL_CONFLICT") return "conflict";
  if (gr.status === "RESOLVED") return "global";
  if (lp.status === "RESOLVED") return "local";
  return "pending";
}

const BADGE_KIND_PRIORITY = { conflict: 3, global: 2, local: 1, pending: 0 };

/**
 * @param {Array<import("./graph-model.js").LocalGraphNode|import("./graph-model.js").GlobalGraphNode>} nodes
 * @param {Array<import("./graph-model.js").GraphEdge>} edges
 * @param {{components: Array<object>}|null} resolutionResult
 *   GET /api/spatial-resolution/results/<building_id> の { result } の中身
 *   (404時はnullを渡す。Integrated Viewと同じ許容)。
 * @returns {Map<string, {kind: "global"|"local"|"conflict"|"pending"}>}
 */
export function classifyNodes(nodes, edges, resolutionResult) {
  const kindBySpaceId = new Map();

  if (resolutionResult && Array.isArray(resolutionResult.components)) {
    for (const component of resolutionResult.components) {
      const kind = componentBadgeKind(component);
      const memberIds = (component.local_placement && component.local_placement.member_space_ids) || [];
      for (const spaceId of memberIds) {
        const existing = kindBySpaceId.get(spaceId);
        // componentはspace_idを重複無く分割する設計だが、万一重複しても
        // 安全側(より重大な方)を優先する防御。
        if (!existing || BADGE_KIND_PRIORITY[kind] > BADGE_KIND_PRIORITY[existing]) {
          kindBySpaceId.set(spaceId, kind);
        }
      }
    }
  }

  const result = new Map();
  for (const node of nodes) {
    if (node.kind === "GLOBAL") {
      // NodalEndpoint自体にRESOLVED/CONFLICTの概念が無いため、GLOBALノードは
      // 常に中立。ノード同士の区別は色ではなくlabel(global_spatial_id)と
      // グラフ上の接続関係で行う。
      result.set(node.id, { kind: "neutral" });
      continue;
    }
    result.set(node.id, { kind: kindBySpaceId.get(node.id) || "pending" });
  }
  return result;
}
