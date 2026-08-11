/**
 * pca.js
 *
 * estimate_plane_from_point_cloud (numpyのcov+eigh) の移植。
 *
 * 【Python版との違いと、その理由】
 * Python版が遅かった主因は「1万個の小さい3x3行列を、1個ずつnp.linalg.eighへ
 * 個別に渡していた」ことによる、Python↔numpy間の関数呼び出しオーバーヘッドの
 * 積み重ねだった(numpyは本来まとめて渡せばバッチ処理できる)。
 *
 * JavaScriptにはこの種の「呼び出し自体のオーバーヘッド」がほぼ無いため、
 * 「まとめて1回で呼ぶ」という最適化は不要になる。代わりに、3x3の対称行列
 *専用の閉形式(反復計算なしで一発で解ける数式)を使うことで、1点あたりの
 * 計算量そのものを減らす。結果(固有値・固有ベクトル)はnumpy版と数学的に
 * 同一になる。
 */

/**
 * 点群(小さい近傍点集合)から、共分散行列(対称3x3、6要素で表現)を求める。
 * @param {Float32Array|number[]} points xyzxyzxyz...の並び
 * @returns {[number,number,number,number,number,number]} [a,b,c,d,e,f]
 *   共分散行列 [[a,b,c],[b,d,e],[c,e,f]]
 */
export function covariance3x3(points) {
  const n = points.length / 3;
  let mx = 0, my = 0, mz = 0;
  for (let i = 0; i < n; i++) {
    mx += points[i * 3]; my += points[i * 3 + 1]; mz += points[i * 3 + 2];
  }
  mx /= n; my /= n; mz /= n;

  let a = 0, b = 0, c = 0, d = 0, e = 0, f = 0;
  for (let i = 0; i < n; i++) {
    const dx = points[i * 3] - mx, dy = points[i * 3 + 1] - my, dz = points[i * 3 + 2] - mz;
    a += dx * dx; b += dx * dy; c += dx * dz;
    d += dy * dy; e += dy * dz; f += dz * dz;
  }
  const denom = n - 1 > 0 ? n - 1 : 1; // numpyのcovは既定でN-1で割る(不偏共分散)
  return [a / denom, b / denom, c / denom, d / denom, e / denom, f / denom];
}

/**
 * 対称3x3行列 [[a,b,c],[b,d,e],[c,e,f]] の、最小固有値に対応する
 * 固有ベクトル(=平面フィッティングにおける法線ベクトル)を、閉形式の式で求める。
 * 反復計算を行わないため、常に一定の計算量で終わる。
 *
 * @param {[number,number,number,number,number,number]} cov [a,b,c,d,e,f]
 * @returns {[number,number,number]} 正規化された法線ベクトル
 */
export function smallestEigenvectorSymmetric3x3([a, b, c, d, e, f]) {
  const p1 = b * b + c * c + e * e;

  if (p1 < 1e-18) {
    // 既に対角行列(非対角成分がほぼ0) → 固有値は対角成分そのもの
    const diag = [a, d, f];
    const minIdx = diag.indexOf(Math.min(...diag));
    const v = [0, 0, 0];
    v[minIdx] = 1;
    return v;
  }

  const q = (a + d + f) / 3;
  const p2 = (a - q) ** 2 + (d - q) ** 2 + (f - q) ** 2 + 2 * p1;
  const p = Math.sqrt(p2 / 6);

  // B = (1/p) * (A - q*I)
  const inv_p = 1 / p;
  const Ba = (a - q) * inv_p, Bd = (d - q) * inv_p, Bf = (f - q) * inv_p;
  const Bb = b * inv_p, Bc = c * inv_p, Be = e * inv_p;

  // det(B) (対称行列: [[Ba,Bb,Bc],[Bb,Bd,Be],[Bc,Be,Bf]])
  const detB =
    Ba * (Bd * Bf - Be * Be) -
    Bb * (Bb * Bf - Be * Bc) +
    Bc * (Bb * Be - Bd * Bc);

  let r = detB / 2;
  r = Math.max(-1, Math.min(1, r)); // 浮動小数点誤差でわずかに範囲外に出ることがあるためクランプ
  const phi = Math.acos(r) / 3;

  // 固有値(大きい順)
  const eig1 = q + 2 * p * Math.cos(phi);
  const eig3 = q + 2 * p * Math.cos(phi + (2 * Math.PI) / 3); // 最小固有値
  // eig2 = 3*q - eig1 - eig3;  // 中間固有値(今回は未使用)

  return eigenvectorForEigenvalue([a, b, c, d, e, f], eig3);
}

/**
 * 既知の固有値λに対応する固有ベクトルを、(A - λI)の零空間から求める。
 * (A-λI)は特異(ランク落ち)になるので、2つの行の外積を取れば
 * 零空間の方向(=固有ベクトル)が得られる。
 */
function eigenvectorForEigenvalue([a, b, c, d, e, f], lambda) {
  const m = [
    [a - lambda, b, c],
    [b, d - lambda, e],
    [c, e, f - lambda],
  ];

  // 複数の行ペアで外積を試し、最もノルムが大きい(数値的に安定した)結果を採用する
  const candidates = [
    cross(m[0], m[1]),
    cross(m[0], m[2]),
    cross(m[1], m[2]),
  ];
  let best = candidates[0], bestNorm = norm(candidates[0]);
  for (let i = 1; i < candidates.length; i++) {
    const nrm = norm(candidates[i]);
    if (nrm > bestNorm) { best = candidates[i]; bestNorm = nrm; }
  }

  if (bestNorm < 1e-12) return [0, 0, 1]; // 縮退ケースのフォールバック
  return [best[0] / bestNorm, best[1] / bestNorm, best[2] / bestNorm];
}

function cross(u, v) {
  return [
    u[1] * v[2] - u[2] * v[1],
    u[2] * v[0] - u[0] * v[2],
    u[0] * v[1] - u[1] * v[0],
  ];
}
function norm(v) { return Math.hypot(v[0], v[1], v[2]); }

/**
 * estimate_plane_from_point_cloud + feature_value の移植。
 * ある候補点の近傍点集合から法線を求め、「近傍点のうち、法線方向に
 * allowable_error以上ズレている点の割合」(= エッジらしさのスコア)を返す。
 *
 * @param {[number,number,number]} candidatePoint
 * @param {Float32Array|number[]} nearbyPoints xyzxyzxyz...
 * @param {number} allowableError
 * @returns {number} 0〜1のスコア(平面から外れている点の割合)
 */
export function featureValue(candidatePoint, nearbyPoints, allowableError = 0.01) {
  const cov = covariance3x3(nearbyPoints);
  const normal = smallestEigenvectorSymmetric3x3(cov);

  const n = nearbyPoints.length / 3;
  let countOutOfPlane = 0;
  for (let i = 0; i < n; i++) {
    const dx = nearbyPoints[i * 3] - candidatePoint[0];
    const dy = nearbyPoints[i * 3 + 1] - candidatePoint[1];
    const dz = nearbyPoints[i * 3 + 2] - candidatePoint[2];
    const dist = Math.abs(dx * normal[0] + dy * normal[1] + dz * normal[2]);
    if (dist >= allowableError) countOutOfPlane++;
  }
  return countOutOfPlane / n;
}
