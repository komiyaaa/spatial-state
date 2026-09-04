// backend/tests/_space_definition_bounds_js_check.mjs
//
// computeProvisionalBounds()(shared/space-definition-bounds.js)を、
// backend/space_definition_generator.py側で計算した期待値と突き合わせるための
// テストハーネス。test_space_definition_bounds_js_port_matches_python.py から
// `node _space_definition_bounds_js_check.mjs <fixture.json>` として呼ばれる。
import { readFileSync } from 'node:fs';
import { computeProvisionalBounds } from '../../shared/space-definition-bounds.js';

const fixturePath = process.argv[2];
const cases = JSON.parse(readFileSync(fixturePath, 'utf-8'));

function flatten(points) {
  const out = new Float64Array(points.length * 3);
  points.forEach((p, i) => { out[i * 3] = p[0]; out[i * 3 + 1] = p[1]; out[i * 3 + 2] = p[2]; });
  return out;
}

function approxEqual(a, b, eps = 1e-6) {
  return Math.abs(a - b) <= eps;
}

function vecApproxEqual(a, b, eps = 1e-6) {
  return a.length === b.length && a.every((v, i) => approxEqual(v, b[i], eps));
}

const results = cases.map((c) => {
  let actual = null, error = null, pass = false;
  try {
    const positions = flatten(c.points);
    actual = computeProvisionalBounds(positions, c.rotation_rad);
    pass = (
      approxEqual(actual.degree, c.expected.degree) &&
      approxEqual(actual.rad, c.expected.rad) &&
      approxEqual(actual.length, c.expected.length) &&
      approxEqual(actual.height, c.expected.height) &&
      vecApproxEqual(actual.origin, c.expected.origin) &&
      actual.bounds.length === c.expected.bounds.length &&
      actual.bounds.every((v, i) => vecApproxEqual(v, c.expected.bounds[i]))
    );
  } catch (e) {
    error = String(e);
  }
  return { label: c.label, expected: c.expected, actual, error, pass };
});

process.stdout.write(JSON.stringify(results));
process.exit(results.every((r) => r.pass) ? 0 : 1);
