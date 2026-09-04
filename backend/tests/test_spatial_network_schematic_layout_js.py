"""
backend/tests/test_spatial_network_schematic_layout_js.py

spatial-network/schematic-layout.js の computeSchematicLayout() を、
Node.js経由で自己完結型に検証する(_spatial_network_schematic_layout_
check.mjs参照)。閉路(cycle)・冗長edge(平行辺)を含む入力でもクラッシュ
せず、tree構造を仮定せずに座標を確定できること、同一入力を2回実行すると
出力が完全一致する(論文の図として使うために必須の決定性)ことを確認する。

実行方法(backendディレクトリから):
    python -m pytest tests/test_spatial_network_schematic_layout_js.py -v
Node.js(spatial-network/schematic-layout.jsの実行に必要)が必要。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).resolve().parent / "_spatial_network_schematic_layout_check.mjs"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="Node.jsが見つからないため、schematic-layout.jsのテストをスキップします")
def test_schematic_layout_js_assertions():
    proc = subprocess.run(
        ["node", str(_HARNESS_PATH)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.stdout, f"node harnessの実行に失敗しました: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    results = json.loads(proc.stdout)

    failures = [r for r in results if not r["pass"]]
    assert not failures, (
        "spatial-network/schematic-layout.jsのアサーションが失敗しました:\n"
        + json.dumps(failures, ensure_ascii=False, indent=2)
    )
    assert proc.returncode == 0
    print(f"test_schematic_layout_js_assertions: OK ({len(results)}ケース全て一致)")


if __name__ == "__main__":
    test_schematic_layout_js_assertions()
