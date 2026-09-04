/**
 * shared/spatial-state-client.js
 *
 * Spatial State API(GET /api/spatial-state/<space_id>)への薄いfetchラッパと、
 * backend/spatial_state_view.py(Presentation / Read Model層)が返す安定した
 * 表示契約(presence/confidence/mobility)を色・凡例ラベルへ対応付けるだけの、
 * 表示補助ヘルパー。
 *
 * 【重要: ここに意味判定のロジックを書かないこと】
 * Spatial State Updaterの内部表現(state/confidence_flag/mu/kappa/p_occ等、
 * spatial_state/のState/ConfidenceFlag Enumやalpha/beta由来の値)から、
 * presence/confidence/mobilityへの変換は、backend/spatial_state_view.pyの
 * 責務であり、ここでは行わない(内部表現の名前は、このファイルにも
 * Viewer/Integrated View側にも一切登場しない)。フロントエンドがUpdaterの
 * 内部表現を直接知ってしまうと、将来更新アルゴリズムが変わるたびに
 * Viewer/Integrated View側の修正が必要になり、Read Modelを挟んだ意味が
 * 無くなる。このモジュールが知ってよいのは、Read Modelが返す3つの
 * 安定した語彙(presence: PRESENT/ABSENT/UNOBSERVED、confidence: HIGH/LOW、
 * mobility: STATIC/DYNAMIC/PENDING)と、それらを描画するための色・ラベル
 * だけである。
 */

/**
 * GET /api/spatial-state/<space_id> を取得する薄いラッパ。
 * @param {string} spaceId
 * @returns {Promise<{space_id: string, voxels: Record<string, {presence: string, confidence: string, mobility: string}>}>}
 */
export async function fetchSpatialState(spaceId) {
  const res = await fetch(`/api/spatial-state/${encodeURIComponent(spaceId)}`);
  if (!res.ok) {
    throw new Error(`Spatial State取得に失敗しました(space_id=${spaceId}, status=${res.status})`);
  }
  return res.json();
}

const PRESENCE_COLOR = {
  PRESENT: 0x4a7c74,
  ABSENT: 0xd64545,
};

const MOBILITY_COLOR = {
  STATIC: 0x3a6ea5,
  DYNAMIC: 0xd98c2b,
  PENDING: 0x9a988e,
};

/** presence('PRESENT'|'ABSENT'|'UNOBSERVED')を表示色(hex)へ対応付ける。
 * 'UNOBSERVED'、または未知の値の場合は undefined を返す。 */
export function colorForPresence(presence) {
  return PRESENCE_COLOR[presence];
}

/** mobility('STATIC'|'DYNAMIC'|'PENDING')を表示色(hex)へ対応付ける。 */
export function colorForMobility(mobility) {
  return MOBILITY_COLOR[mobility];
}

/** presenceが実体を持つ(描画対象になり得る)かどうか。
 * 'UNOBSERVED'(証拠がまだ無い)は、Viewerが従来どおり描画しない。 */
export function isRenderablePresence(presence) {
  return presence === 'PRESENT' || presence === 'ABSENT';
}

/** 凡例表示用: presenceの並び順・ラベル・色・ghost表示(ワイヤーフレーム)の要否。 */
export const PRESENCE_LEGEND = [
  { key: 'PRESENT', label: '占有(PRESENT)', color: PRESENCE_COLOR.PRESENT, ghost: false },
  { key: 'ABSENT', label: '消失(ABSENT)', color: PRESENCE_COLOR.ABSENT, ghost: true },
];

/** 凡例表示用: mobilityの並び順・ラベル・色。 */
export const MOBILITY_LEGEND = [
  { key: 'STATIC', label: '静的(STATIC)', color: MOBILITY_COLOR.STATIC },
  { key: 'DYNAMIC', label: '動的(DYNAMIC)', color: MOBILITY_COLOR.DYNAMIC },
  { key: 'PENDING', label: '判断保留(PENDING)', color: MOBILITY_COLOR.PENDING },
];
