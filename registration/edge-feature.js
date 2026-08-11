/**
 * edge-feature.js
 *
 * identify_feature の移植。ユーザーがクリックした点の周辺から、
 * 「一番エッジらしい(周囲の平面から外れている)点」を自動で探す。
 * spatial-hash.js(近傍探索)と pca.js(平面フィッティング)を組み合わせるだけの、
 * 薄いオーケストレーション層。
 */
import { buildSpatialHash, queryRadius } from "./spatial-hash.js";
import { featureValue } from "./pca.js";

const OUTER_RADIUS = 0.3;   // ユーザーがクリックした点の周辺を、まずこの半径で切り出す
const INNER_RADIUS = 0.15;  // 切り出した点それぞれについて、この半径でエッジらしさを測る
const MAX_CANDIDATES = 10000; // 計算量を抑えるための上限(downsample_point_cloudに相当)
const ALLOWABLE_ERROR = 0.01;

/**
 * @param {Float32Array} allPositions 点群全体(xyzxyzxyz...)
 * @param {[number,number,number]} pickedPoint ユーザーがクリックした点の座標
 * @param {Map} outerHash allPositions から buildSpatialHash(OUTER_RADIUS) で作った近傍索引
 * @param {(done: number, total: number) => void} [onProgress] 進捗コールバック(任意)
 * @returns {Promise<[number,number,number]|null>} エッジに吸着された点の座標
 */
export async function identifyFeature(allPositions, pickedPoint, outerHash, onProgress) {
  // Step1: クリックした点の周辺(半径0.3)を切り出す
  const outerIndices = queryRadius(outerHash, allPositions, OUTER_RADIUS, pickedPoint, OUTER_RADIUS);
  if (outerIndices.length === 0) return null;

  // Step2: 候補が多すぎる場合は間引く(downsample_point_cloud相当)
  const candidateIndices = downsample(outerIndices, MAX_CANDIDATES);
  const candidatePositions = extractPositions(allPositions, candidateIndices);

  // Step3: 切り出した候補点だけで、内側の近傍探索(半径0.15)用の索引を作り直す
  //         (半径が違うので、外側の索引をそのまま使い回さない)
  const innerHash = buildSpatialHash(candidatePositions, INNER_RADIUS);

  let bestScore = -1;
  let bestPoint = null;
  const n = candidatePositions.length / 3;
  const YIELD_EVERY = 500; // これだけ処理するごとに1フレーム分ブラウザに制御を返す

  for (let i = 0; i < n; i++) {
    const point = [candidatePositions[i * 3], candidatePositions[i * 3 + 1], candidatePositions[i * 3 + 2]];
    const neighborIdx = queryRadius(innerHash, candidatePositions, INNER_RADIUS, point, INNER_RADIUS);
    if (neighborIdx.length >= 3) {
      const neighborPositions = extractPositions(candidatePositions, neighborIdx);
      const score = featureValue(point, neighborPositions, ALLOWABLE_ERROR);
      if (score > bestScore) { bestScore = score; bestPoint = point; }
    }

    if (i % YIELD_EVERY === 0) {
      onProgress?.(i, n);
      await new Promise(requestAnimationFrame); // ブラウザが固まらないよう、定期的に制御を返す
    }
  }
  onProgress?.(n, n);

  return bestPoint;
}

function downsample(indices, maxCount) {
  if (indices.length <= maxCount) return indices;
  // ランダムサンプリング(Python版のnp.random.choiceに相当)
  const shuffled = indices.slice();
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, maxCount);
}

function extractPositions(allPositions, indices) {
  const out = new Float32Array(indices.length * 3);
  for (let k = 0; k < indices.length; k++) {
    const idx = indices[k];
    out[k * 3] = allPositions[idx * 3];
    out[k * 3 + 1] = allPositions[idx * 3 + 1];
    out[k * 3 + 2] = allPositions[idx * 3 + 2];
  }
  return out;
}

export { OUTER_RADIUS, INNER_RADIUS };
