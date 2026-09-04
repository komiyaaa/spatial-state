/**
 * shared/space-definition-bounds.js
 *
 * backend/space_definition_generator.py の _apply_z_axis_rotation_only()
 * (explicit rotation_rad指定時の経路のみ)を1対1でJS移植したもの。
 * Add Local Spaceウィザードのrotation previewが、Generate Local Space時に
 * 実際に計算されるorigin/boundsを事前に(サーバー往復無しで)表示するために
 * 使う(2026-09-03)。
 *
 * 【重要: これはpoint_to_spatial_id.pyの回転規約(shared/local-spatial-id.js)
 * とは別の、独立した規約】
 * このモジュールは「Base Mapの外接正方形を求め、origin/boundsをワールド
 * 座標で決定する」ためだけに使う回転(標準的な回転行列、det=+1)。
 * shared/local-spatial-id.jsが実装する「local座標系(Structural Label・
 * Spatial State・Nodal Spatial IDが使う座標系)」の回転式(det=-1、Y軸反転
 * 込み)とは数式が異なる。両者は役割が異なるため統一・修正しない
 * (backend/space_definition_generator.pyのモジュールdocstring参照)。
 *
 * PCAによる自動検出(Python側でrotation_rad=Noneの場合の経路)は移植しない
 * (GUIのAdd Local Spaceウィザードは常に明示的なrotation_radを渡すため、
 * このpreviewでも使わない)。
 *
 * backend/tests/test_space_definition_bounds_js_port_matches_python.py で
 * Python実装との数値一致を自動検証している。
 */

/**
 * @param {Float32Array|number[]} positions フラットなワールド座標 [x0,y0,z0, x1,y1,z1, ...]
 * @param {number} rotationRad
 * @returns {{degree:number, rad:number, bounds:number[][], origin:number[], length:number, height:number}}
 */
export function computeProvisionalBounds(positions, rotationRad) {
  const n = positions.length / 3;
  if (n === 0) {
    throw new Error('positionsが空です(bounds計算には点群データが必要です)。');
  }
  const a = rotationRad;
  const cosNegA = Math.cos(-a);
  const sinNegA = Math.sin(-a);
  // rotation_matrix(Python _apply_z_axis_rotation_only と同一の3x3行列)
  //   [[cos(-a), -sin(-a), 0],
  //    [sin(-a),  cos(-a), 0],
  //    [0,        0,       1]]
  const m00 = cosNegA, m01 = -sinNegA;
  const m10 = sinNegA, m11 = cosNegA;

  // rotated = points @ rotation_matrix.T (1点ずつ書けば rotated_p = M @ p)
  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (let i = 0; i < n; i++) {
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    const rx = m00 * x + m01 * y;
    const ry = m10 * x + m11 * y;
    if (rx < minX) minX = rx;
    if (rx > maxX) maxX = rx;
    if (ry < minY) minY = ry;
    if (ry > maxY) maxY = ry;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }

  const width = maxX - minX;
  const yExtent = maxY - minY;
  const length = Math.max(width, yExtent); // 大きい方の辺で正方形化する
  const zExtent = maxZ - minZ;
  const centerX = (maxX + minX) / 2;
  const centerY = (maxY + minY) / 2;
  const half = length / 2;

  // Python側と同じ順序で8頂点を列挙する(x: -/+、y: -/+、z: min/maxの順)
  const bboxVerticesRotated = [
    [centerX - half, centerY - half, minZ],
    [centerX - half, centerY - half, maxZ],
    [centerX - half, centerY + half, minZ],
    [centerX - half, centerY + half, maxZ],
    [centerX + half, centerY - half, minZ],
    [centerX + half, centerY - half, maxZ],
    [centerX + half, centerY + half, minZ],
    [centerX + half, centerY + half, maxZ],
  ];

  // 主成分(回転後)空間から元のワールド座標系へ戻す:
  // bbox_vertices = bbox_vertices_rotated @ inv(rotation_matrix).T
  // rotation_matrixは直交行列(回転行列)なので inv(M) = M.T、よって
  // inv(M).T = M となり、実質 q @ M を計算すればよい(Python式と対応関係を
  // 保つため、ここでも同じ「q @ M」という形でそのまま書く)。
  const bboxVertices = bboxVerticesRotated.map(([qx, qy, qz]) => [
    qx * m00 + qy * m10,
    qx * m01 + qy * m11,
    qz,
  ]);

  const origin = bboxVertices[2];

  return {
    degree: (a * 180) / Math.PI,
    rad: a,
    bounds: bboxVertices,
    origin,
    length,
    height: zExtent,
  };
}

/** boundsの8頂点(computeProvisionalBoundsのbounds、Python側と同じ頂点順序)を
 * つなぐ12本の辺(頂点indexペア)。ワイヤーフレーム描画用。 */
export const BOUNDS_WIREFRAME_EDGES = [
  [0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3],
  [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7],
];
