"""
backend/tests/test_local_spatial_id_js_port_matches_python.py

shared/local-spatial-id.js の worldPointToLocalSpatialId()(JS移植版)が、
backend/point_to_spatial_id.py の world_points_to_spatial_ids()(正)と
完全に一致することを、Node.js経由で自動検証する。

rotation=0だけでなく、正/負のrotation・複数zoom level・voxel境界付近の点を
含める(2026-09-03、Nodal Information Connection作成UI追加時のユーザー指示)。
CoordinateDefinitionの変換規約(origin平行移動→rad回転→floor division)は
どちらの実装でも同一であることが期待される — 独自解釈の混入を検知するための
テスト。

実行方法(backendディレクトリから): python -m pytest tests/test_local_spatial_id_js_port_matches_python.py -v
Node.js(shared/local-spatial-id.jsの実行に必要)が必要。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from point_to_spatial_id import world_points_to_spatial_ids  # noqa: E402

_HARNESS_PATH = Path(__file__).resolve().parent / "_local_spatial_id_js_check.mjs"

UNIT_SIZE = {"9": 0.12, "10": 0.06, "11": 0.03}


def _expected(origin, rad, unit_size, zoom_level, point) -> str:
    space_def = {"origin": origin, "rad": rad, "unit-size": unit_size}
    return world_points_to_spatial_ids(np.array([point], dtype=np.float64), space_def, zoom_level)[0]


def _case(label, origin, rad, unit_size, zoom_level, point) -> dict:
    return {
        "label": label,
        "coordinate_definition": {"origin": origin, "rad": rad, "unit-size": unit_size},
        "zoom_level": zoom_level,
        "point": list(point),
        "expected": _expected(origin, rad, unit_size, zoom_level, point),
    }


def _rotated_local_point(origin, rad, local_x, local_y, local_z) -> list:
    """local_x/local_y/local_zをピッタリ指定した点を、rotationを込みで
    ワールド座標へ逆算する(境界値ケースを、回転ありでも正確に構成するため)。
    world_points_to_spatial_ids()の回転行列は自己逆行列(M@M=I)なので、
    same-shapeの式で逆算できる(shared/local-spatial-id.jsのコメント参照)。"""
    cos_t, sin_t = np.cos(rad), np.sin(rad)
    rel_x = local_x * cos_t + local_y * sin_t
    rel_y = local_x * sin_t - local_y * cos_t
    return [origin[0] + rel_x, origin[1] + rel_y, origin[2] + local_z]


def _build_cases() -> list:
    cases = []

    # --- rotation = 0 ---
    cases.append(_case("rotation_zero_at_origin", [0.0, 0.0, 0.0], 0.0, UNIT_SIZE, 11, [0.0, 0.0, 0.0]))
    cases.append(_case("rotation_zero_generic", [0.0, 0.0, 0.0], 0.0, UNIT_SIZE, 11, [1.234, -5.678, 2.0]))
    cases.append(_case("rotation_zero_negative", [0.0, 0.0, 0.0], 0.0, UNIT_SIZE, 11, [-1.234, -0.001, -0.5]))

    # --- 正のrotation(実データG002相当の値を含む) ---
    cases.append(_case(
        "positive_rotation_g002_like",
        [-12.286172823601943, -0.569778840372589, 0.0], 1.5963000376992902,
        UNIT_SIZE, 11, [5.5, 10.2, 1.5],
    ))
    cases.append(_case("positive_rotation_small_angle", [1.0, 2.0, 0.5], 0.2618, UNIT_SIZE, 10, [3.3, -1.1, 0.9]))
    cases.append(_case("positive_rotation_near_pi", [0.0, 0.0, 0.0], 3.0, UNIT_SIZE, 11, [4.0, -4.0, 1.0]))

    # --- 負のrotation ---
    cases.append(_case("negative_rotation", [0.5, -0.5, 0.0], -0.7854, UNIT_SIZE, 11, [2.2, 3.3, 1.1]))
    cases.append(_case("negative_rotation_large", [0.0, 0.0, 0.0], -2.9, UNIT_SIZE, 9, [-4.4, 6.6, -2.0]))

    # --- 複数zoom level(同一点を異なるzoomで) ---
    for zoom in (9, 10, 11):
        cases.append(_case(f"multi_zoom_{zoom}", [0.0, 0.0, 0.0], 0.5236, UNIT_SIZE, zoom, [7.77, -3.33, 1.23]))

    # --- voxel境界付近(rotation=0、正・負のインデックス) ---
    vs = UNIT_SIZE["11"]
    cases.append(_case("boundary_exact_positive", [0.0, 0.0, 0.0], 0.0, UNIT_SIZE, 11,
                        [vs * 10, vs * 20, vs * 3]))
    cases.append(_case("boundary_just_below", [0.0, 0.0, 0.0], 0.0, UNIT_SIZE, 11,
                        [vs * 10 - 1e-9, vs * 20 - 1e-9, vs * 3 - 1e-9]))
    cases.append(_case("boundary_just_above", [0.0, 0.0, 0.0], 0.0, UNIT_SIZE, 11,
                        [vs * 10 + 1e-9, vs * 20 + 1e-9, vs * 3 + 1e-9]))
    cases.append(_case("boundary_negative_exact", [0.0, 0.0, 0.0], 0.0, UNIT_SIZE, 11,
                        [-vs * 5, -vs * 7, -vs * 2]))
    cases.append(_case("boundary_negative_just_below", [0.0, 0.0, 0.0], 0.0, UNIT_SIZE, 11,
                        [-vs * 5 - 1e-9, -vs * 7 - 1e-9, -vs * 2 - 1e-9]))

    # --- voxel境界付近 × rotationあり(境界判定と回転式の両方を同時に検証) ---
    origin = [1.0, -2.0, 0.0]
    theta = 0.5236
    cases.append(_case("boundary_with_rotation_exact", origin, theta, UNIT_SIZE, 11,
                        _rotated_local_point(origin, theta, vs * 8, vs * -3, vs * 2)))
    cases.append(_case("boundary_with_rotation_just_below", origin, theta, UNIT_SIZE, 11,
                        _rotated_local_point(origin, theta, vs * 8 - 1e-9, vs * -3 - 1e-9, vs * 2)))
    theta_neg = -1.0472
    cases.append(_case("boundary_with_negative_rotation_exact", origin, theta_neg, UNIT_SIZE, 10,
                        _rotated_local_point(origin, theta_neg, UNIT_SIZE["10"] * -4, UNIT_SIZE["10"] * 6,
                                              UNIT_SIZE["10"] * -1)))

    return cases


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="Node.jsが見つからないため、JS移植の突き合わせテストをスキップします")
def test_js_port_matches_python_for_all_cases():
    cases = _build_cases()
    with tempfile.TemporaryDirectory() as tmp:
        fixture_path = Path(tmp) / "cases.json"
        fixture_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

        proc = subprocess.run(
            ["node", str(_HARNESS_PATH), str(fixture_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0 or proc.stdout, (
            f"node harnessの実行に失敗しました: stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        results = json.loads(proc.stdout)

    assert len(results) == len(cases)
    failures = [r for r in results if not r["pass"]]
    assert not failures, (
        "worldPointToLocalSpatialId()(JS)がPython実装(world_points_to_spatial_ids)と"
        f"一致しないケースがあります:\n" + json.dumps(failures, ensure_ascii=False, indent=2)
    )
    print(f"test_js_port_matches_python_for_all_cases: OK ({len(cases)}ケース全て一致)")


if __name__ == "__main__":
    test_js_port_matches_python_for_all_cases()
