/**
 * shared/local-spatial-id.js
 *
 * Local Spatial ID("zoom/f/x/y"形式の文字列)を、そのLocal Space自身の
 * CoordinateDefinition(unit-size・origin・rad)を使って実寸メートルの
 * world座標(座標系2: provisional/world coordinate)へ変換する。
 * backend/spatial_id/local_spatial_id.pyの
 * LocalSpatialIdResolver.resolve_provisional_world_center()と同じ式
 * (2026-09-02、local_space_prototype.htmlから抽出して共通化)。
 *
 * Base Map/Spatial ID Voxel(shared/display-coordinates.js・positions.bin)と
 * 同じ座標系なので、そのまま重ねて表示できる。local_space_prototype.html
 * (単体Viewer)・integrated-view.js(複数空間の統合表示)の両方が、この
 * 1箇所だけを頼りにspatial_id→world座標の変換を行う(座標変換ロジックの
 * 複製を避けるため)。
 *
 * 【重要】固定のzoom番号を「3cmのこと」とはみなさない。voxel_sizeは常に
 * そのLocal Space自身のunit-size[zoom]から引く(zoomはspatial_id文字列に
 * 埋め込まれた値をそのまま使う)。
 */

/**
 * local方向ベクトル(x,y,z)を、ワールド方向ベクトルへ変換する(回転のみ、
 * 原点の平行移動は行わない)。localSpatialIdToWorldCenter・
 * worldPointToLocalSpatialIdの両方が使っている回転式(forward変換
 * point_to_spatial_id.world_points_to_spatial_idsと同じ対合行列、M@M=単位行列)
 * を、この1箇所へ集約したもの(2026-09-03、Local Space生成rotation previewの
 * Local X/Y/Z軸表示のために抽出。回転式自体は一切変更していない)。
 *
 * @param {[number,number,number]} localXYZ
 * @param {{rad:number}} coordinateDefinition
 * @returns {[number,number,number]}
 */
export function localVectorToWorld(localXYZ, coordinateDefinition) {
  const [localX, localY, localZ] = localXYZ;
  const theta = coordinateDefinition.rad;
  const cosT = Math.cos(theta), sinT = Math.sin(theta);
  const relX = localX * cosT + localY * sinT;
  const relY = localX * sinT - localY * cosT;
  return [relX, relY, localZ];
}

/**
 * local座標(x,y,z)を、ワールド座標(原点平行移動込み)へ変換する。
 * 回転はlocalVectorToWorldと共通(新しい式は増やさない)。
 *
 * @param {[number,number,number]} localXYZ
 * @param {{origin:number[], rad:number}} coordinateDefinition
 * @returns {[number,number,number]}
 */
export function localCoordinatesToWorld(localXYZ, coordinateDefinition) {
  const [relX, relY, relZ] = localVectorToWorld(localXYZ, coordinateDefinition);
  const origin = coordinateDefinition.origin;
  return [relX + origin[0], relY + origin[1], relZ + origin[2]];
}

/**
 * @param {string} spatialId "zoom/f/x/y"形式
 * @param {{origin:number[], rad:number, "unit-size":Record<string,number>}} coordinateDefinition
 * @returns {{center:[number,number,number], voxelSize:number}}
 */
export function localSpatialIdToWorldCenter(spatialId, coordinateDefinition) {
  const [zoomStr, fStr, xStr, yStr] = spatialId.split('/');
  const unitSize = coordinateDefinition['unit-size'];
  const voxelSize = unitSize[zoomStr];
  if (voxelSize == null) {
    throw new Error(`zoom_level ${zoomStr} はこのLocal Spaceのunit-sizeに存在しません(spatial_id=${spatialId})`);
  }
  const f = Number(fStr), x = Number(xStr), y = Number(yStr);
  const localX = (x + 0.5) * voxelSize;
  const localY = (y + 0.5) * voxelSize;
  const localZ = (f + 0.5) * voxelSize;
  // forward変換(point_to_spatial_id.world_points_to_spatial_ids)の回転式と
  // 同じ対合行列(M@M=単位行列)を使う。resolve_provisional_world_center()
  // (Python側)と完全に同じ式(localCoordinatesToWorld参照)。
  const center = localCoordinatesToWorld([localX, localY, localZ], coordinateDefinition);
  return {
    center,
    voxelSize, // このLocal Spatial IDが属するzoomの物理voxel一辺長[m]
  };
}

/**
 * world座標の1点を、そのLocal Space自身のCoordinateDefinitionを使って
 * Local Spatial ID("zoom/f/x/y")へ変換する(順方向。上のlocalSpatialIdToWorldCenter
 * の逆方向)。backend/point_to_spatial_id.py の world_points_to_spatial_ids()
 * (1点版)と数式を1対1で移植したもの — origin平行移動→rad回転(det=-1の
 * 対合行列、上のlocalSpatialIdToWorldCenterと同じ行列)→unit-sizeでfloor
 * division、という順序・式を独自解釈せずそのまま踏襲する
 * (2026-09-03、Nodal Information Connection作成UIのクリックpick用に追加。
 * backend/tests/test_local_spatial_id_js_port_matches_python.py で
 * Python側の実装との一致を自動検証している)。
 *
 * @param {[number,number,number]} point world座標 [x, y, z]
 * @param {{origin:number[], rad:number, "unit-size":Record<string,number>}} coordinateDefinition
 * @param {number} zoomLevel
 * @returns {string} "zoom/f/x/y"形式のLocal Spatial ID
 */
export function worldPointToLocalSpatialId(point, coordinateDefinition, zoomLevel) {
  const unitSize = coordinateDefinition['unit-size'];
  const voxelSize = unitSize[String(zoomLevel)];
  if (voxelSize == null) {
    throw new Error(`zoom_level ${zoomLevel} はこのLocal Spaceのunit-sizeに存在しません`);
  }
  const origin = coordinateDefinition.origin;
  const relX = point[0] - origin[0];
  const relY = point[1] - origin[1];
  const relZ = point[2] - origin[2];
  const theta = coordinateDefinition.rad;
  const cosT = Math.cos(theta), sinT = Math.sin(theta);
  // point_to_spatial_id.world_points_to_spatial_ids() と同一の回転式
  // (local_yの符号に注意。上のlocalSpatialIdToWorldCenterの逆方向)
  const localX = relX * cosT + relY * sinT;
  const localY = relX * sinT - relY * cosT;
  const localZ = relZ;

  const xIdx = Math.floor(localX / voxelSize);
  const yIdx = Math.floor(localY / voxelSize);
  const fIdx = Math.floor(localZ / voxelSize);

  return `${zoomLevel}/${fIdx}/${xIdx}/${yIdx}`;
}
