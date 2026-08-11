/**
 * pointcloud-io.js
 *
 * 点群の読み込み(.las / .ply)と、書き出し(PLY組み立て + 出力先)を扱う。
 *
 * 【将来「別サーバーへの送信」に差し替える設計】
 * exportRegisteredPointCloud() は、PLYのバイナリを組み立てるところまでを
 * 担当し、実際にそれをどう送り出すか(ブラウザダウンロード / fetchでの
 * サーバー送信 等)は、引数で渡す `outputHandler` に委ねている。
 * 将来、別サーバーへの送信に変える場合は、呼び出し側で渡す
 * outputHandler を差し替えるだけでよく、このファイル自体は
 * 一切変更する必要が無い(下記 downloadHandler / postToServerHandler を参照)。
 */

/**
 * ファイル拡張子に応じて読み込み方法を振り分ける(File入力、手動選択用)。
 * @param {File} file
 * @returns {Promise<{positions: Float32Array, colors: Float32Array|null}>}
 */
export async function loadPointCloud(file) {
  const ext = extensionOf(file.name);
  const buffer = await file.arrayBuffer();
  return parseByExtension(buffer, ext);
}

/**
 * URLから点群を読み込む(base_maps/manifest.json 経由の選択用)。
 * ローカルサーバー(http://localhost:...)で配信されている前提
 * (file://では fetch がブロックされるため動作しない)。
 * @param {string} url
 */
export async function loadPointCloudFromURL(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`ベースマップの取得に失敗しました: ${url} (${res.status})`);
  const buffer = await res.arrayBuffer();
  const ext = extensionOf(url);
  return parseByExtension(buffer, ext);
}

/**
 * base_maps/manifest.json を取得する。
 * manifest.jsonの形式: [{ "id": "G001", "label": "G001(1階)", "file": "G001.las" }, ...]
 * @param {string} manifestUrl 既定 "./base_maps/manifest.json"
 */
export async function loadBaseMapManifest(manifestUrl = "./base_maps/manifest.json") {
  const res = await fetch(manifestUrl);
  if (!res.ok) throw new Error(`ベースマップ一覧の取得に失敗しました: ${manifestUrl}`);
  return res.json();
}

function extensionOf(pathOrName) {
  return pathOrName.split(".").pop().toLowerCase().split(/[?#]/)[0];
}

function parseByExtension(buffer, ext) {
  if (ext === "las") return parseLAS(buffer);
  if (ext === "ply") return parsePLY(buffer);
  if (ext === "xyz") return parseXYZ(buffer);
  throw new Error(`未対応の拡張子です: .${ext}`);
}

/**
 * LASファイルの最小限のパーサ。LAS 1.2〜1.4、Point Data Format 0〜3を
 * 想定(RGBはFormat 2/3で読み取る)。スキャン強度・分類等、更新手法で
 * 使わない属性は読み飛ばす。
 */
function parseLAS(buffer) {
  const view = new DataView(buffer);

  // --- ヘッダ ---
  const offsetToPointData = view.getUint32(96, true);
  const pointDataFormat = view.getUint8(104);
  const pointRecordLength = view.getUint16(105, true);

  // 点数: 通常は107バイト目(uint32)。LAS1.4の一部ファイルではここが0のことがあり、
  // その場合は拡張フィールド(247バイト目、uint64)を使う(まれなケースのフォールバック)。
  let numberOfPoints = view.getUint32(107, true);
  if (numberOfPoints === 0) {
    numberOfPoints = Number(view.getBigUint64(247, true));
  }

  const scaleX = view.getFloat64(131, true);
  const scaleY = view.getFloat64(139, true);
  const scaleZ = view.getFloat64(147, true);
  const offsetX = view.getFloat64(155, true);
  const offsetY = view.getFloat64(163, true);
  const offsetZ = view.getFloat64(171, true);

  const hasRGB = pointDataFormat === 2 || pointDataFormat === 3 || pointDataFormat === 5;
  // RGBのバイトオフセット(Format 2/3共通、末尾6バイト)
  const rgbOffsetInRecord = pointDataFormat === 2 ? 20 : 28;

  const positions = new Float32Array(numberOfPoints * 3);
  const colors = hasRGB ? new Float32Array(numberOfPoints * 3) : null;

  for (let i = 0; i < numberOfPoints; i++) {
    const recOffset = offsetToPointData + i * pointRecordLength;
    const rawX = view.getInt32(recOffset, true);
    const rawY = view.getInt32(recOffset + 4, true);
    const rawZ = view.getInt32(recOffset + 8, true);

    positions[i * 3] = rawX * scaleX + offsetX;
    positions[i * 3 + 1] = rawY * scaleY + offsetY;
    positions[i * 3 + 2] = rawZ * scaleZ + offsetZ;

    if (hasRGB) {
      const r = view.getUint16(recOffset + rgbOffsetInRecord, true);
      const g = view.getUint16(recOffset + rgbOffsetInRecord + 2, true);
      const b = view.getUint16(recOffset + rgbOffsetInRecord + 4, true);
      // Python版と同じく16bit(0-65535)から0-1へ正規化
      colors[i * 3] = r / 65535;
      colors[i * 3 + 1] = g / 65535;
      colors[i * 3 + 2] = b / 65535;
    }
  }

  return { positions, colors };
}

/** PLY(ASCII/バイナリ両対応の最小限パーサ、xyz + 任意でrgb) */
function parsePLY(buffer) {
  // ヘッダはテキストなので、まずASCIIとして先頭を読む
  const headerText = new TextDecoder("ascii").decode(buffer.slice(0, Math.min(buffer.byteLength, 4096)));
  const headerEndIdx = headerText.indexOf("end_header\n") + "end_header\n".length;
  const headerLines = headerText.slice(0, headerEndIdx).split("\n");

  const isBinary = headerLines.some(l => l.startsWith("format binary"));
  const isLittleEndian = headerLines.some(l => l.includes("binary_little_endian"));

  let vertexCount = 0;
  const properties = [];
  for (const line of headerLines) {
    const m = line.match(/^element vertex (\d+)/);
    if (m) vertexCount = parseInt(m[1], 10);
    const p = line.match(/^property (\w+) (\w+)/);
    if (p) properties.push({ type: p[1], name: p[2] });
  }

  const positions = new Float32Array(vertexCount * 3);
  const hasColor = properties.some(p => p.name === "red");
  const colors = hasColor ? new Float32Array(vertexCount * 3) : null;

  if (!isBinary) {
    const bodyText = new TextDecoder("ascii").decode(buffer.slice(headerEndIdx));
    const lines = bodyText.trim().split("\n");
    for (let i = 0; i < vertexCount; i++) {
      const vals = lines[i].trim().split(/\s+/).map(Number);
      const idxMap = {};
      properties.forEach((p, k) => { idxMap[p.name] = k; });
      positions[i * 3] = vals[idxMap.x];
      positions[i * 3 + 1] = vals[idxMap.y];
      positions[i * 3 + 2] = vals[idxMap.z];
      if (hasColor) {
        colors[i * 3] = vals[idxMap.red] / 255;
        colors[i * 3 + 1] = vals[idxMap.green] / 255;
        colors[i * 3 + 2] = vals[idxMap.blue] / 255;
      }
    }
  } else {
    const view = new DataView(buffer, headerEndIdx);
    let offset = 0;
    const typeSize = { float: 4, float32: 4, double: 8, uchar: 1, uint8: 1 };
    for (let i = 0; i < vertexCount; i++) {
      const rec = {};
      for (const p of properties) {
        const size = typeSize[p.type] ?? 4;
        if (p.type === "float" || p.type === "float32") rec[p.name] = view.getFloat32(offset, isLittleEndian);
        else if (p.type === "double") rec[p.name] = view.getFloat64(offset, isLittleEndian);
        else if (p.type === "uchar" || p.type === "uint8") rec[p.name] = view.getUint8(offset);
        offset += size;
      }
      positions[i * 3] = rec.x; positions[i * 3 + 1] = rec.y; positions[i * 3 + 2] = rec.z;
      if (hasColor) {
        colors[i * 3] = rec.red / 255; colors[i * 3 + 1] = rec.green / 255; colors[i * 3 + 2] = rec.blue / 255;
      }
    }
  }

  return { positions, colors };
}

/** .xyz(単純なテキスト、1行に "x y z" または "x y z r g b") */
function parseXYZ(buffer) {
  const text = new TextDecoder("ascii").decode(buffer);
  const lines = text.trim().split("\n").filter(l => l.trim());
  const positions = new Float32Array(lines.length * 3);
  const firstCols = lines[0].trim().split(/\s+/).length;
  const hasColor = firstCols >= 6;
  const colors = hasColor ? new Float32Array(lines.length * 3) : null;

  lines.forEach((line, i) => {
    const vals = line.trim().split(/\s+/).map(Number);
    positions[i * 3] = vals[0]; positions[i * 3 + 1] = vals[1]; positions[i * 3 + 2] = vals[2];
    if (hasColor) {
      colors[i * 3] = vals[3] / 255; colors[i * 3 + 1] = vals[4] / 255; colors[i * 3 + 2] = vals[5] / 255;
    }
  });
  return { positions, colors };
}

/**
 * 点群をASCII PLY形式のテキストに組み立てる。
 */
function buildPLYText(positions, colors) {
  const n = positions.length / 3;
  const lines = [
    "ply", "format ascii 1.0", `element vertex ${n}`,
    "property float x", "property float y", "property float z",
  ];
  if (colors) {
    lines.push("property uchar red", "property uchar green", "property uchar blue");
  }
  lines.push("end_header");

  for (let i = 0; i < n; i++) {
    let row = `${positions[i * 3]} ${positions[i * 3 + 1]} ${positions[i * 3 + 2]}`;
    if (colors) {
      row += ` ${Math.round(colors[i * 3] * 255)} ${Math.round(colors[i * 3 + 1] * 255)} ${Math.round(colors[i * 3 + 2] * 255)}`;
    }
    lines.push(row);
  }
  return lines.join("\n");
}

/**
 * ラフレジ結果を出力する。PLYの組み立てはここで行い、実際の送り先は
 * outputHandler に委ねる(差し替えポイント)。
 *
 * @param {Float32Array} positions
 * @param {Float32Array|null} colors
 * @param {string} filename
 * @param {(plyText: string, filename: string, spaceId: string|null) => Promise<void>} outputHandler
 * @param {string|null} spaceId どのローカル空間向けのラフレジ結果かを示すID(任意)
 */
export async function exportRegisteredPointCloud(positions, colors, filename, outputHandler, spaceId = null) {
  const plyText = buildPLYText(positions, colors);
  await outputHandler(plyText, filename, spaceId);
}

/**
 * 出力先の実装その1(既定): ブラウザのダウンロードとして保存する。
 * Python版の「Output Folderに保存」に一番近い挙動。
 */
export async function downloadHandler(plyText, filename) {
  const blob = new Blob([plyText], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * 出力先の実装その2: 別サーバー(backend/server.py)へ送信する。
 * 受信後、サーバー側でVGICP(精密位置合わせ)・JSON化を実行する想定
 * (server.py 側の run_vgicp / convert_to_scan_json が実際の差し替えポイント)。
 */
export async function postToServerHandler(plyText, filename, spaceId) {
  const res = await fetch("/api/registration-results", {
    method: "POST",
    headers: {
      "Content-Type": "text/plain",
      "X-Filename": filename,
      "X-Space-Id": spaceId ?? "unknown",
    },
    body: plyText,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`サーバーへの送信に失敗しました(status=${res.status}) ${detail}`);
  }
  return res.json();
}
