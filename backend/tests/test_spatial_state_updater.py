"""
backend/tests/test_spatial_state_updater.py

spatial_state_updater.apply_scan_to_spatial_state()の回帰テスト。

この関数は、以前はbackend/server.py receive_registration_result()内に
インラインされていた処理(Step4)を、式を一切変えずに抽出しただけのもの。
ここでは「配線(オーケストレーション)が壊れていないか」だけを確認する
(alpha/beta更新式・状態遷移そのものの検証はspatial_state/test_spatial_state.py
の責務であり、ここでは重複しない)。

確認内容(CLAUDE.md「動作確認の手順」2〜4に対応):
- scan_jsonのhits + その面隣接がまとめて更新対象になること
- 複数回にわたって呼んでも、前回の状態がリセットされず蓄積されること
  (StateStoreを介した永続化・復元が正しく行われること)
- 戻り値が、今回処理したボクセルだけのUpdater内部表現(summary)であること
- hitsが空の場合は何も書き込まないこと

実データ(backend/data/)には一切書き込まない(一時ディレクトリを使う)。

実行方法(リポジトリルートから):
    python backend/tests/test_spatial_state_updater.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from spatial_state import Params  # noqa: E402
from spatial_state_updater import apply_scan_to_spatial_state  # noqa: E402
from state_store import StateStore  # noqa: E402


def test_hits_and_face_neighbors_are_both_processed():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp))
        params = Params()
        hits = {"9/5/5/5": 20}
        result = apply_scan_to_spatial_state("space-a", hits, 0.03, store, params)

        # ヒットした本人 + 面隣接6個 = 7ボクセルが処理対象になっているはず
        assert "9/5/5/5" in result
        expected_neighbors = {"9/6/5/5", "9/4/5/5", "9/5/6/5", "9/5/4/5", "9/5/5/6", "9/5/5/4"}
        assert expected_neighbors.issubset(result.keys())
        assert len(result) == 7


def test_state_accumulates_across_multiple_calls_via_state_store():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp))
        params = Params()
        hits = {"9/1/1/1": 20}

        result1 = apply_scan_to_spatial_state("space-b", hits, 0.03, store, params)
        n_obs_after_1 = result1["9/1/1/1"]["n_obs"]

        # 2回目呼び出し: 1回目の状態がリセットされず、n_obsが積み上がること
        result2 = apply_scan_to_spatial_state("space-b", hits, 0.03, store, params)
        n_obs_after_2 = result2["9/1/1/1"]["n_obs"]

        assert n_obs_after_2 == n_obs_after_1 + 1, (
            f"2回目呼び出しでn_obsが積み上がっていない(前回の状態が復元されていない疑い): "
            f"1回目={n_obs_after_1}, 2回目={n_obs_after_2}"
        )

        # 保存ファイルからも同じ状態が復元できること
        reloaded = store.load("space-b", params)
        assert reloaded.summary()["9/1/1/1"]["n_obs"] == n_obs_after_2


def test_miss_streak_increments_when_repeatedly_excluded_from_hits():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp))
        params = Params()

        # voxel "9/2/2/2" 自身は一度もhitせず、隣接 "9/3/2/2" のみをhitさせ続ける
        # (covered=Trueにするため、h_covを満たす近傍ヒットが要る)。
        hits = {"9/3/2/2": 20}
        for _ in range(params.M_min + 2):
            result = apply_scan_to_spatial_state("space-c", hits, 0.03, store, params)

        internal = store.load("space-c", params).voxels["9/2/2/2"]
        assert internal.miss_streak >= params.M_min, (
            f"M_min({params.M_min})回連続で証拠が無ければmiss_streakが増えるはずだが、"
            f"miss_streak={internal.miss_streak}"
        )


def test_empty_hits_returns_empty_and_does_not_persist():
    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp)
        store = StateStore(base_dir)
        params = Params()

        result = apply_scan_to_spatial_state("space-d", {}, None, store, params)
        assert result == {}
        assert not (base_dir / "space-d.json").exists()


def test_session_is_appended_not_overwritten():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp))
        params = Params()
        hits = {"9/0/0/0": 5}

        apply_scan_to_spatial_state("space-e", hits, 0.5, store, params, session={"session_id": "s1"})
        apply_scan_to_spatial_state("space-e", hits, 0.6, store, params, session={"session_id": "s2"})

        import json
        data = json.loads((Path(tmp) / "space-e.json").read_text(encoding="utf-8"))
        session_ids = [s["session_id"] for s in data["scan_session"]]
        assert session_ids == ["s1", "s2"]


if __name__ == "__main__":
    tests = [
        test_hits_and_face_neighbors_are_both_processed,
        test_state_accumulates_across_multiple_calls_via_state_store,
        test_miss_streak_increments_when_repeatedly_excluded_from_hits,
        test_empty_hits_returns_empty_and_does_not_persist,
        test_session_is_appended_not_overwritten,
    ]
    for t in tests:
        t()
        print(f"OK: {t.__name__}")
    print("ALL PASSED")
