"""
backend/tests/test_spatial_network_graph_model_js.py

spatial-network/graph-model.js の buildGraphModel() を、Node.js経由で
自己完結型に検証する(_spatial_network_graph_model_check.mjs参照)。

このロジックには対応するPython実装が無い(frontend専用のグラフ構築)ため、
worldPointToLocalSpatialId()のようなcross-language parityチェックではなく、
既知のfixtureに対する期待値をJS側にハードコードした自己完結型アサーション。

最重要の確認内容: 異なるglobal_spatial_idを持つ2つの独立したGLOBAL
endpointが、単一の"GLOBAL"ノードへ誤って集約されないこと(2026-09-25、
本来存在しないedge/pathがグラフ上に生じることを防ぐための回帰テスト)。

実行方法(backendディレクトリから):
    python -m pytest tests/test_spatial_network_graph_model_js.py -v
Node.js(spatial-network/graph-model.jsの実行に必要)が必要。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).resolve().parent / "_spatial_network_graph_model_check.mjs"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="Node.jsが見つからないため、graph-model.jsのテストをスキップします")
def test_graph_model_js_assertions():
    proc = subprocess.run(
        ["node", str(_HARNESS_PATH)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.stdout, f"node harnessの実行に失敗しました: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    results = json.loads(proc.stdout)

    failures = [r for r in results if not r["pass"]]
    assert not failures, (
        "spatial-network/graph-model.jsのアサーションが失敗しました:\n"
        + json.dumps(failures, ensure_ascii=False, indent=2)
    )
    assert proc.returncode == 0
    print(f"test_graph_model_js_assertions: OK ({len(results)}ケース全て一致)")


if __name__ == "__main__":
    test_graph_model_js_assertions()
