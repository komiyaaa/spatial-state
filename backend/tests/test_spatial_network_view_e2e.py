"""
backend/tests/test_spatial_network_view_e2e.py

Spatial Network View(spatial-network/*.js)の、実ブラウザ(headless Chromium)
経由のE2Eテスト。使い捨てBuilding/Local Space/NodalConnection/NodalEndpointを
作り(test_server_nodal_api_e2e.pyの_patch_server_repos/_add_space/_postを
再利用)、実サーバ(ephemeral port、werkzeug.serving.make_serverで直接起動、
app.run()のdebug reloaderは経由しない)を起動してPlaywrightで検証する。

このリポジトリで初めての「実ブラウザを操作するE2E」(既存のtest_server_
nodal_api_e2e.pyはFlaskのtest_client()のみでJSは一切実行しない)。

確認内容:
- 閉路(S1-S2-S3-S1)・冗長edge(S1-S2をもう1本)を含んでいてもクラッシュせず、
  正しいnode/edge数で描画される(tree構造を仮定していないことの実機確認)。
- 異なるglobal_spatial_idを持つ2つの独立したGlobal anchorが、別々のノードの
  ままであり、両者を直接結ぶ幽霊edgeが存在しない(以前はGLOBAL型connection
  を全て単一ノードへ誤って集約していたバグの回帰確認)。
- Network View⇔Layout Viewの切替、Export SVGのダウンロード発火(Layout View
  のみ有効)、Network View(3D)でのnode drag後の再収束が、実ブラウザ上で
  動作する。
- 【2026-09-25追加】Network Viewが3D(WebGL canvas、`three-network-
  renderer.js`)として描画されること、LOCAL nodeのidentity色とstatus色が
  別々の値であること(色を混同していないこと)。
- 【2026-09-25追加】Layout Viewで、S1(root)から直接分岐しているS2・S3が
  同じX(depth)・異なるYに配置され(一直線に並ばず分岐して見える)こと
  (実データG002↔T207/G002↔T208で発覚した「一直線に見える」問題の回帰
  確認。このfixtureのS1-S2-S3-S1閉路は、S1から見るとS2・S3が共にdepth1の
  直接の子になるため、同じ形の分岐チェックに使える)。

実行方法(backendディレクトリから):
    python -m pytest tests/test_spatial_network_view_e2e.py -v
Playwright(pip install playwright済み、`playwright install chromium`実行済み)
が必要。Chromiumが無い環境ではskipする。Node.jsは不要(ブラウザ内で
spatial-network/*.jsがESモジュールとしてそのまま動く)。
"""
from __future__ import annotations

import re
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402
from repositories.building_repository import BuildingRepository  # noqa: E402
from test_server_nodal_api_e2e import _patch_server_repos, _add_space, _post  # noqa: E402


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ServerThread(threading.Thread):
    """server.appを、app.run()のdebug reloaderを経由せずephemeral portで
    直接起動する(Playwrightが実際にHTTPで到達できるoriginが必要なため、
    Flask test_client()では代替できない)。"""

    def __init__(self, app, port):
        super().__init__(daemon=True)
        from werkzeug.serving import make_server
        self._srv = make_server("127.0.0.1", port, app, threaded=True)

    def run(self):
        self._srv.serve_forever()

    def shutdown(self):
        self._srv.shutdown()


@pytest.mark.skipif(
    not _chromium_available(),
    reason="Playwright Chromiumが見つからないため、Spatial Network ViewのE2Eをスキップします",
)
def test_spatial_network_view_cycles_redundant_edges_and_distinct_global_anchors():
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        space_def_dir = _patch_server_repos(tmp)
        server.building_repo = BuildingRepository(Path(tmp) / "buildings.json")
        building = server.building_repo.create(name="B1")
        building_id = building.building_id
        assert building_id == "b1", building_id  # _add_spaceの呼び出しと一致させるため

        space1 = _add_space(space_def_dir, building_id, "S1")
        space2 = _add_space(space_def_dir, building_id, "S2")
        space3 = _add_space(space_def_dir, building_id, "S3")

        client = server.app.test_client()

        def make_local_connection(space_a, space_b):
            body, status = _post(client, "/api/nodal-connections", {
                "building_id": building_id,
                "endpoint_space_a": {"type": "LOCAL", "space_id": space_a},
                "endpoint_space_b": {"type": "LOCAL", "space_id": space_b},
            })
            assert status == 200, body
            return body["connection"]["connection_id"]

        # 閉路: S1-S2, S2-S3, S3-S1
        make_local_connection(space1, space2)
        make_local_connection(space2, space3)
        make_local_connection(space3, space1)
        # 冗長edge: S1-S2をもう1本(同じspace pairを結ぶ2本目のconnection)
        make_local_connection(space1, space2)

        def make_global_anchor_connection(space_id, global_spatial_id):
            conn_body, status = _post(client, "/api/nodal-connections", {
                "building_id": building_id,
                "endpoint_space_a": {"type": "LOCAL", "space_id": space_id},
                "endpoint_space_b": {"type": "GLOBAL"},
            })
            assert status == 200, conn_body
            connection_id = conn_body["connection"]["connection_id"]
            local_ep, _ = _post(client, "/api/nodal-endpoints", {
                "type": "LOCAL", "space_id": space_id, "local_spatial_id": "0/0/0/0",
            })
            global_ep, _ = _post(client, "/api/nodal-endpoints", {
                "type": "GLOBAL", "global_spatial_id": global_spatial_id,
            })
            corr_body, corr_status = _post(client, f"/api/nodal-connections/{connection_id}/correspondences", {
                "node_a_id": local_ep["endpoint"]["endpoint_id"], "node_b_id": global_ep["endpoint"]["endpoint_id"],
            })
            assert corr_status == 200, corr_body
            return connection_id

        # 2つの独立したGlobal anchor(異なるglobal_spatial_id、別々のLocal Spaceから)
        make_global_anchor_connection(space1, "16/0/58000/25000")
        make_global_anchor_connection(space3, "16/0/58999/25999")

        resolve_body, resolve_status = _post(client, "/api/spatial-resolution/resolve", {"building_id": building_id})
        assert resolve_status == 200, resolve_body

        port = _free_port()
        server_thread = _ServerThread(server.app, port)
        server_thread.start()
        base_url = f"http://127.0.0.1:{port}/"

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                console_errors = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda exc: console_errors.append(str(exc)))

                page.goto(base_url, timeout=15000)
                page.wait_for_selector("#buildingList .building-item", timeout=10000)
                page.fill("#buildingSearch", "B1")
                page.wait_for_timeout(200)
                page.click(".building-item")
                page.wait_for_timeout(200)
                page.click("#btnOpenSpatialNetworkView")

                # --- Network View(3D、既定モード)が描画されることを確認 ---
                page.wait_for_selector("[data-testid='spatial-network-3d-canvas']", timeout=10000)
                page.wait_for_timeout(1500)  # force simulationの収束待ち

                canvas3d = page.locator("[data-testid='spatial-network-3d-canvas']")
                node_count = int(canvas3d.get_attribute("data-node-count"))
                edge_count = int(canvas3d.get_attribute("data-edge-count"))
                # nodes: S1, S2, S3 + 2つの独立したGLOBAL anchor = 5
                assert node_count == 5, f"node_count={node_count}"
                # edges: 3(閉路) + 1(冗長edge) + 2(anchor) = 6(tree/非treeを区別せず全件)
                assert edge_count == 6, f"edge_count={edge_count}"

                # identity色とstatus色が別チャンネルであること(混同していないこと)。
                # window.__spatialNetworkViewDebugはE2E検証用フック
                # (integrated-view.jsのwindow.__integratedViewLastResultと同じ位置づけ)。
                debug_info = page.evaluate("window.__spatialNetworkViewDebug.getNodeDebugInfo()")
                local_nodes_debug = [n for n in debug_info if n["kind"] == "LOCAL"]
                assert len(local_nodes_debug) == 3, local_nodes_debug
                for n in local_nodes_debug:
                    assert n["identityColor"] is not None, n
                    assert n["statusColor"] is not None, n
                    assert n["identityColor"] != n["statusColor"], (
                        f"identity色とstatus色が同じ値になっています(混同): {n}"
                    )
                global_nodes_debug = [n for n in debug_info if n["kind"] == "GLOBAL"]
                assert len(global_nodes_debug) == 2, global_nodes_debug

                # Export SVGはNetwork View中は無効化されていること
                # (3D WebGLシーンをSVGとして書き出すことは意味を持たないため)。
                assert page.locator("#snExportBtn").is_disabled()

                # node dragが例外なく完了し、再収束すること
                # (force simulationがsettleするまでrenderer.update()が
                # 毎フレーム走るため、drag前に十分待つ)。
                canvas_box = canvas3d.bounding_box()
                assert canvas_box is not None
                cx = canvas_box["x"] + canvas_box["width"] / 2
                cy = canvas_box["y"] + canvas_box["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.mouse.move(cx + 80, cy + 40, steps=5)
                page.mouse.up()
                page.wait_for_timeout(2500)  # releaseNode()後の再収束待ち

                # --- Layout Viewへ切替: 分岐(branching)確認 ---
                page.click(".dtw-tab[data-mode='layout']")
                page.wait_for_selector("[data-testid='spatial-network-svg']", timeout=10000)
                page.wait_for_timeout(300)

                svg = page.locator("[data-testid='spatial-network-svg']")
                assert int(svg.get_attribute("data-node-count")) == 5
                assert int(svg.get_attribute("data-edge-count")) == 6

                global_node_count = page.locator("[data-node-kind='GLOBAL']").count()
                assert global_node_count == 2, f"global_node_count={global_node_count}(2つのGLOBAL anchorが集約されずに残っているはず)"

                # GLOBALノード同士を直接結ぶ幽霊edgeが存在しないこと
                # (異なるGlobal anchorを単一ノードへ集約すると、本来存在しない
                # pathが生じてしまう、という回帰確認)。
                edge_lines = svg.locator(".sn-edge")
                phantom_count = 0
                for i in range(edge_lines.count()):
                    line = edge_lines.nth(i)
                    if line.get_attribute("data-source-kind") == "GLOBAL" and line.get_attribute("data-target-kind") == "GLOBAL":
                        phantom_count += 1
                assert phantom_count == 0, "GLOBALノード同士を直接結ぶ幽霊edgeが存在します"

                # S1(root)から直接分岐しているS2・S3が、同じX(depth)・
                # 異なるYに配置されること(一直線に並ばないこと)。
                # S1-S2-S3-S1の閉路は、S1から見るとS2・S3が共にdepth1の
                # 直接の子になるため、実データG002↔T207/G002↔T208と同じ
                # 形の分岐チェックに使える。
                def node_transform(node_id):
                    t = page.locator(f".sn-node[data-node-id='{node_id}']").get_attribute("transform")
                    m = re.match(r"translate\(([-\d.]+),([-\d.]+)\)", t)
                    assert m, t
                    return float(m.group(1)), float(m.group(2))

                s1_x, s1_y = node_transform(space1)
                s2_x, s2_y = node_transform(space2)
                s3_x, s3_y = node_transform(space3)
                assert s2_x == s3_x, f"S2/S3のXが一致しません(分岐して見えるはず): s2_x={s2_x}, s3_x={s3_x}"
                assert s2_y != s3_y, f"S2/S3のYが同じで、直線に見える可能性があります: s2_y={s2_y}, s3_y={s3_y}"
                assert s1_x != s2_x, "S1(root)が子と同じdepth列に並んでいます"
                cross = (s2_x - s1_x) * (s3_y - s1_y) - (s3_x - s1_x) * (s2_y - s1_y)
                assert abs(cross) > 1e-6, "S1/S2/S3が一直線に並んでいます(branchingが視覚的に分からない)"

                # Export SVG(Layout Viewでは有効)
                assert not page.locator("#snExportBtn").is_disabled()
                with page.expect_download() as dl_info:
                    page.click("#snExportBtn")
                download = dl_info.value
                assert download.suggested_filename.endswith(".svg"), download.suggested_filename

                assert console_errors == [], f"consoleエラーが発生しました: {console_errors}"
                browser.close()
        finally:
            server_thread.shutdown()
            server_thread.join(timeout=5)

    print("test_spatial_network_view_cycles_redundant_edges_and_distinct_global_anchors: OK")


if __name__ == "__main__":
    test_spatial_network_view_cycles_redundant_edges_and_distinct_global_anchors()
    print()
    print("全テスト成功。")
