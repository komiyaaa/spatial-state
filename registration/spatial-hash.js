/**
 * spatial-hash.js
 *
 * 「座標を整数で丸めてバケツに入れる」近傍探索。KDTreeの代わりに使う。
 * この研究のcore/pillars.py・core/confidence.pyの近傍計算(agr等)と
 * 同じ発想を、点群の近傍探索にも転用したもの。
 *
 * 使い方:
 *   const hash = buildSpatialHash(positions, cellSize);
 *   const nearbyIndices = queryRadius(hash, positions, cellSize, center, radius);
 *
 * cellSize は「探したい半径」に対して、それより少し大きい値にすることを
 * 推奨する(半径と同じか少し大きければ、周囲27マス(3x3x3)を見るだけで
 * 半径内を確実にカバーできる)。
 */

function cellKey(ix, iy, iz) {
  return `${ix},${iy},${iz}`;
}

/**
 * 点群からボクセルバケットのハッシュマップを構築する。
 * @param {Float32Array} positions xyzxyzxyz...の並び
 * @param {number} cellSize バケツ1個の一辺の長さ
 * @returns {Map<string, number[]>} セルキー → その中に入る点のインデックス配列
 */
export function buildSpatialHash(positions, cellSize) {
  const hash = new Map();
  const n = positions.length / 3;
  for (let i = 0; i < n; i++) {
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    const ix = Math.floor(x / cellSize);
    const iy = Math.floor(y / cellSize);
    const iz = Math.floor(z / cellSize);
    const key = cellKey(ix, iy, iz);
    let bucket = hash.get(key);
    if (!bucket) { bucket = []; hash.set(key, bucket); }
    bucket.push(i);
  }
  return hash;
}

/**
 * 指定した中心点から半径radius以内にある点のインデックスを返す。
 * cellSizeがradius以上であることを前提に、周囲27マスだけを走査してから、
 * 実際の距離で最終的に絞り込む(バケツ分けによる粗い絞り込み+正確な距離判定)。
 *
 * @param {Map<string, number[]>} hash buildSpatialHashの結果
 * @param {Float32Array} positions ハッシュ構築に使ったのと同じ点群
 * @param {number} cellSize ハッシュ構築に使ったのと同じセルサイズ
 * @param {[number,number,number]} center 中心点
 * @param {number} radius 探索半径(cellSize以下であること)
 * @returns {number[]} 半径内の点のインデックス
 */
export function queryRadius(hash, positions, cellSize, center, radius) {
  const [cx, cy, cz] = center;
  const icx = Math.floor(cx / cellSize);
  const icy = Math.floor(cy / cellSize);
  const icz = Math.floor(cz / cellSize);
  const r2 = radius * radius;

  const result = [];
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      for (let dz = -1; dz <= 1; dz++) {
        const bucket = hash.get(cellKey(icx + dx, icy + dy, icz + dz));
        if (!bucket) continue;
        for (const idx of bucket) {
          const px = positions[idx * 3], py = positions[idx * 3 + 1], pz = positions[idx * 3 + 2];
          const ddx = px - cx, ddy = py - cy, ddz = pz - cz;
          if (ddx * ddx + ddy * ddy + ddz * ddz <= r2) result.push(idx);
        }
      }
    }
  }
  return result;
}
