/**
 * rigid-transform.js
 *
 * 「変えないアルゴリズム」そのもの。EdgePicking_test.py の以下の関数と
 * 1対1で対応させている(将来Python版と見比べて監査できるように、
 * 関数名・処理の順序をあえて変えていない)。
 *
 *   get_straight_vector  → getStraightVector
 *   align_vectors_z      → alignVectorsZ
 *   get_matrix_z         → rotationMatrixZ (alignVectorsZの結果から行列を作る部分を分離)
 *   transform_points     → applyRotation
 *   translation_points   → applyTranslation
 *
 * このファイルは3D描画・ファイルI/O・近傍探索のいずれにも依存しない、
 * 純粋な数値計算だけを置く。アルゴリズムを変更する必要が生じたら、
 * 触るべきはこのファイルだけ、という設計。
 */

/**
 * 2点から、始点→終点のベクトルを求める(get_straight_vector)。
 * @param {[number,number,number]} p1 始点
 * @param {[number,number,number]} p2 終点
 * @returns {[number,number,number]}
 */
export function getStraightVector(p1, p2) {
  return [p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]];
}

/**
 * XY平面に投影した2つのベクトルを揃えるための、Z軸まわりの回転角を求める
 * (align_vectors_z)。
 * @returns {number} ラジアン
 */
export function alignVectorsZ(vector1, vector2) {
  const len1 = Math.hypot(vector1[0], vector1[1]);
  const len2 = Math.hypot(vector2[0], vector2[1]);
  const v1 = [vector1[0] / len1, vector1[1] / len1];
  const v2 = [vector2[0] / len2, vector2[1] / len2];

  let dot = v1[0] * v2[0] + v1[1] * v2[1];
  dot = Math.max(-1.0, Math.min(1.0, dot)); // クランプ(浮動小数点誤差対策、Python版と同じ)
  let angle = Math.acos(dot);

  const cross2d = v1[0] * v2[1] - v1[1] * v2[0];
  if (cross2d < 0) angle = -angle;

  return angle;
}

/**
 * Z軸まわりの回転角から、3x3回転行列を作る(get_matrix_z の後半部分)。
 * 行優先(row-major)の9要素配列 [r00,r01,r02, r10,r11,r12, r20,r21,r22] で返す。
 */
export function rotationMatrixZ(angleRad) {
  const c = Math.cos(angleRad);
  const s = Math.sin(angleRad);
  return [
    c, -s, 0,
    s, c, 0,
    0, 0, 1,
  ];
}

/**
 * 点群(Float32Array、xyzxyzxyz...の並び)に3x3回転行列を適用する
 * (transform_points)。新しい配列を返す(元の配列は変更しない)。
 */
export function applyRotation(positions, matrix3x3) {
  const [m00, m01, m02, m10, m11, m12, m20, m21, m22] = matrix3x3;
  const out = new Float32Array(positions.length);
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i], y = positions[i + 1], z = positions[i + 2];
    out[i]     = m00 * x + m01 * y + m02 * z;
    out[i + 1] = m10 * x + m11 * y + m12 * z;
    out[i + 2] = m20 * x + m21 * y + m22 * z;
  }
  return out;
}

/**
 * 基準点1組(回転後のsource側の点、target側の点)から並進ベクトルを求め、
 * 点群全体に適用する(translation_points)。
 * Python版は同次座標(4x4行列)を経由しているが、並進のみなので
 * 数学的には「全点に同じベクトルを足す」ことと完全に等価。
 */
export function applyTranslation(positions, refPointRotated, refPointTarget) {
  const tx = refPointTarget[0] - refPointRotated[0];
  const ty = refPointTarget[1] - refPointRotated[1];
  const tz = refPointTarget[2] - refPointRotated[2];

  const out = new Float32Array(positions.length);
  for (let i = 0; i < positions.length; i += 3) {
    out[i]     = positions[i] + tx;
    out[i + 1] = positions[i + 1] + ty;
    out[i + 2] = positions[i + 2] + tz;
  }
  return out;
}

/**
 * ラフレジストレーション本体: 2点ペア(source側2点・target側2点、いずれも
 * identify_feature で精緻化済みの座標)から、source点群全体を
 * 「Z軸回転 → 並進」の順で変換する。
 *
 * これが registration_app.py の _run_registration に相当する、
 * 一連の処理の入り口。将来的にアルゴリズム自体を差し替える場合は、
 * この関数の中身(呼び出す関数の組み合わせ)を変更する。
 *
 * @param {Float32Array} sourcePositions 変換対象のsource点群全体
 * @param {[number,number,number][]} sourceFeaturePoints [点1, 点2](edge精緻化済み)
 * @param {[number,number,number][]} targetFeaturePoints [点1, 点2](edge精緻化済み)
 * @returns {Float32Array} 変換後の点群
 */
export function registerRigidZAxis(sourcePositions, sourceFeaturePoints, targetFeaturePoints) {
  const sourceVector = getStraightVector(sourceFeaturePoints[0], sourceFeaturePoints[1]);
  const targetVector = getStraightVector(targetFeaturePoints[0], targetFeaturePoints[1]);

  const angle = alignVectorsZ(sourceVector, targetVector);
  const matrix = rotationMatrixZ(angle);

  const rotated = applyRotation(sourcePositions, matrix);

  // 基準点1(sourceFeaturePoints[0])を回転させた座標を求め、
  // targetFeaturePoints[0] に一致するよう並進する
  const [rx, ry, rz] = applyRotation(
    new Float32Array(sourceFeaturePoints[0]), matrix
  );
  const translated = applyTranslation(rotated, [rx, ry, rz], targetFeaturePoints[0]);

  return translated;
}
