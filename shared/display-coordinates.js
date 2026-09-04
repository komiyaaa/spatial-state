/**
 * shared/display-coordinates.js
 *
 * raw point-cloud座標(Z軸が鉛直上向き。測量・LiDAR一般の慣習であり、
 * CloudCompare等が表示する座標系と同じ)と、Three.jsの表示座標系
 * (Y軸が鉛直上向き)との間の変換を、アプリ全体で共通の1箇所に定義する
 * (2026-09-02修正)。
 *
 * 【経緯: なぜ共通化したか】
 * 従来、各Viewer(registration-controller.js・plane-viewer.js・
 * local_space_prototype.html・integrated-view.js)が、それぞれ個別に
 * `swapYZ(x,y,z) => [x,z,y]`(Y軸とZ軸を入れ替えるだけ)を実装していた。
 * この変換は2軸だけを交換する奇置換であり、行列式が-1になる
 * (幾何学的な鏡映を伴う)。実際に、CloudCompareで元のBase Mapファイルを
 * 表示した形状と、GUIのRough Registration画面のTarget表示を比較したところ、
 * 鏡に映したように反転していることが実データで確認された。
 *
 * 【採用した変換: (x,y,z) -> (-x,z,y)】
 * Y/Z入れ替えに加えてX軸の符号も反転することで、行列式を+1に戻す
 * (det=+1のproper rotation、鏡映を含まない)。検証(符号付き体積による
 * キラリティ確認)済み。かつ、この変換は自己逆変換(2回適用すると元の
 * 座標に戻る)でもあるため、pick時の「表示座標→raw座標」への逆変換にも
 * 同じ計算式がそのまま使える(fromDisplayCoordinates()として、意図を
 * 明確にするために別名でも提供する)。
 *
 * 【重要: CoordinateDefinitionの変換とは別物】
 * backend/spatial_id/local_spatial_id.pyのresolve_provisional_world_center()が
 * 使うorigin/rad変換(det=-1、Local Spatial IDのidentityに関わる、
 * ロードマップPhase 3で監査済みの意図的な既存仕様)とは完全に無関係。
 * ここで扱うのは「読み込んだraw point-cloud座標を、Three.jsの画面に
 * どう映すか」という、純粋に描画上の変換のみ。
 *
 * 【この変換を使ってはいけない場所】
 * Rough Registrationの数値計算(edge feature検出・rigid transform推定)、
 * POST /api/registration-resultsへ送信するデータ、run_vgicp()への入力、
 * Spatial State更新アルゴリズム、Local Spatial ID割当のいずれにも、
 * この変換を混入させないこと(いずれも既存どおりraw座標のまま扱う)。
 */

/**
 * raw point-cloud座標(Z-up)を、Three.jsの表示座標(Y-up)へ変換する。
 * @param {number} x
 * @param {number} y
 * @param {number} z
 * @returns {[number, number, number]}
 */
export function toDisplayCoordinates(x, y, z) {
  return [-x, z, y];
}

/**
 * Three.jsの表示座標(Y-up)を、raw point-cloud座標(Z-up)へ戻す。
 * toDisplayCoordinates()は自己逆変換(det=+1、2回適用で元に戻る)のため
 * 数式としては同一だが、呼び出し意図(「表示座標を元に戻す」)を明確に
 * するため、別名の関数として提供する。
 * @param {number} x
 * @param {number} y
 * @param {number} z
 * @returns {[number, number, number]}
 */
export function fromDisplayCoordinates(x, y, z) {
  return [-x, z, y];
}
