"""
backend/tests/test_spatial_network_force_layout_js.py

spatial-network/force-layout.js の createForceSimulation() を、Node.js経由で
自己完結型に検証する(_spatial_network_force_layout_check.mjs参照)。
決定的な初期配置、有限tick数以内の収束(settled=true)、settled後に
無限ループしないこと、node drag(setNodePosition/releaseNode)後に
ローカルへ再収束することを確認する。

実行方法(backendディレクトリから):
    python -m pytest tests/test_spatial_network_force_layout_js.py -v
Node.js(spatial-network/force-layout.jsの実行に必要)が必要。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).resolve().parent / "_spatial_network_force_layout_check.mjs"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="Node.jsが見つからないため、force-layout.jsのテストをスキップします")
def test_force_layout_js_assertions():
    proc = subprocess.run(
        ["node", str(_HARNESS_PATH)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.stdout, f"node harnessの実行に失敗しました: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    results = json.loads(proc.stdout)

    failures = [r for r in results if not r["pass"]]
    assert not failures, (
        "spatial-network/force-layout.jsのアサーションが失敗しました:\n"
        + json.dumps(failures, ensure_ascii=False, indent=2)
    )
    assert proc.returncode == 0
    print(f"test_force_layout_js_assertions: OK ({len(results)}ケース全て一致)")


if __name__ == "__main__":
    test_force_layout_js_assertions()
