// backend/tests/_local_spatial_id_js_check.mjs
//
// worldPointToLocalSpatialId()(shared/local-spatial-id.js)を、
// backend/point_to_spatial_id.py側で計算した期待値と突き合わせるための
// テストハーネス。test_local_spatial_id_js_port_matches_python.py から
// `node _local_spatial_id_js_check.mjs <fixture.json>` として呼ばれる。
// 単体では実行しない(pytest経由の利用を想定)。
import { readFileSync } from 'node:fs';
import { worldPointToLocalSpatialId } from '../../shared/local-spatial-id.js';

const fixturePath = process.argv[2];
const cases = JSON.parse(readFileSync(fixturePath, 'utf-8'));

const results = cases.map((c) => {
  let actual = null, error = null;
  try {
    actual = worldPointToLocalSpatialId(c.point, c.coordinate_definition, c.zoom_level);
  } catch (e) {
    error = String(e);
  }
  return { label: c.label, expected: c.expected, actual, error, pass: actual === c.expected };
});

process.stdout.write(JSON.stringify(results));
process.exit(results.every((r) => r.pass) ? 0 : 1);
