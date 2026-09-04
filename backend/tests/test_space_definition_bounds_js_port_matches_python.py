"""
backend/tests/test_space_definition_bounds_js_port_matches_python.py

shared/space-definition-bounds.js の computeProvisionalBounds()(JS移植版)が、
backend/space_definition_generator.py の _apply_z_axis_rotation_only()
(explicit rotation_rad指定時の経路、正)と完全に一致することを、Node.js経由で
自動検証する。

rotation=0・正/負・小角度/90°付近・様々な部屋形状(正方形に近い/横長/縦長/
天井が高い(Z extent > XY footprint))を含める(2026-09-03、Local Space生成
rotation preview追加時のユーザー指示)。
_apply_z_axis_rotation_only()の回転規約(標準的な回転行列、det=+1)は、
shared/local-spatial-id.jsが実装するLocal Spatial ID座標系の回転規約
(det=-1、Y軸反転込み)とは別物であり、統一・修正しない
(shared/space-definition-bounds.js のモジュールdocstring参照)。

実行方法(backendディレクトリから):
    python -m pytest tests/test_space_definition_bounds_js_port_matches_python.py -v
Node.js(shared/space-definition-bounds.jsの実行に必要)が必要。
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from space_definition_generator import _apply_z_axis_rotation_only  # noqa: E402

_HARNESS_PATH = Path(__file__).resolve().parent / "_space_definition_bounds_js_check.mjs"


def _room_points(sx, sy, sz, nx=6, ny=6, nz=4, seed=0, offset=(0.0, 0.0, 0.0)):
    rng = np.random.default_rng(seed)
    xs = np.linspace(0, sx, nx)
    ys = np.linspace(0, sy, ny)
    zs = np.linspace(0, sz, nz)
    pts = np.array(np.meshgrid(xs, ys, zs)).T.reshape(-1, 3).astype(np.float64)
    pts += rng.normal(0, 0.001, size=pts.shape)  # 完全に平面/軸並行な退化ケースを避ける
    pts += np.array(offset)
    return pts


def _case(label, points, rotation_rad) -> dict:
    result = _apply_z_axis_rotation_only(points, rotation_rad=rotation_rad)
    expected = {
        "degree": float(result["degree"]),
        "rad": float(result["rad"]),
        "bounds": np.asarray(result["bounds"]).tolist(),
        "origin": np.asarray(result["origin"]).tolist(),
        "length": float(result["length"]),
        "height": float(result["height"]),
    }
    return {
        "label": label,
        "points": points.tolist(),
        "rotation_rad": rotation_rad,
        "expected": expected,
    }


def _build_cases() -> list:
    cases = []

    square_room = _room_points(4.0, 4.0, 3.0, seed=1)
    cases.append(_case("rotation_zero_square_room", square_room, 0.0))
    cases.append(_case("rotation_small_positive", square_room, math.radians(5.0)))
    cases.append(_case("rotation_small_negative", square_room, math.radians(-5.0)))

    wide_room = _room_points(10.0, 3.0, 2.5, seed=2)  # x方向に横長
    cases.append(_case("wide_room_rotation_30deg", wide_room, math.radians(30.0)))
    cases.append(_case("wide_room_rotation_minus_60deg", wide_room, math.radians(-60.0)))

    tall_room = _room_points(2.0, 2.5, 15.0, nz=10, seed=3)  # Z extentがXY footprintを超える
    cases.append(_case("tall_room_rotation_90deg", tall_room, math.radians(90.0)))
    cases.append(_case("tall_room_rotation_minus_120deg", tall_room, math.radians(-120.0)))

    offset_room = _room_points(6.0, 8.0, 3.0, seed=4, offset=(100.0, -250.0, 5.0))  # 原点から離れた実座標
    cases.append(_case("offset_room_rotation_170deg", offset_room, math.radians(170.0)))
    cases.append(_case("offset_room_rotation_minus_170deg", offset_room, math.radians(-170.0)))

    real_like_room = _room_points(20.04, 45.66, 3.0, nx=8, ny=12, seed=5, offset=(-12.286, -0.570, 0.0))
    cases.append(_case("real_like_room_rotation_91_46deg", real_like_room, math.radians(91.46125499674353)))

    return cases


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="Node.jsが見つからないため、JS移植の突き合わせテストをスキップします")
def test_js_port_matches_python_for_all_cases():
    cases = _build_cases()
    with tempfile.TemporaryDirectory() as tmp:
        fixture_path = Path(tmp) / "cases.json"
        fixture_path.write_text(json.dumps(cases), encoding="utf-8")

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
        "computeProvisionalBounds()(JS)がPython実装(_apply_z_axis_rotation_only)と"
        f"一致しないケースがあります:\n" + json.dumps(failures, ensure_ascii=False, indent=2)
    )
    print(f"test_js_port_matches_python_for_all_cases: OK ({len(cases)}ケース全て一致)")


if __name__ == "__main__":
    test_js_port_matches_python_for_all_cases()
