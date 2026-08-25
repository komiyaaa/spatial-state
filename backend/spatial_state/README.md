# spatial_state パッケージ

`stage1_formalization_v3.md` の定式化(3本柱統合、ベータ分布ベース)を、
そのままPythonで実装したもの。

## ファイル構成

| ファイル | 内容 | 対応する節 |
|---|---|---|
| `params.py` | パラメータ定義(生成時に理論制約を自動検証) | §5.3.1 |
| `core.py` | 状態を持たない純粋関数(w_cnt, is_peak等) | §2.1 |
| `voxel.py` | 1ボクセル分の状態を管理するVoxelState | §2〜§3 |
| `tracker.py` | 複数ボクセル+ラベルごとの品質相場を管理 | §2.7 |
| `example_usage.py` | 使用例 | - |
| `test_spatial_state.py` | これまで検算してきた性質の再現テスト(全7件) | - |

## インストール不要、そのまま使う

このディレクトリごと、既存のプロジェクトにコピーして使う。
外部ライブラリへの依存は無い(標準ライブラリのみ)。

```python
from spatial_state import Params, SpatialStateTracker

params = Params(gamma=0.8, n0=3.0, kappa_th=0.5, n0_mu=3.0, M_min=2)
tracker = SpatialStateTracker(params)

# 1スキャンごとに、観測された各ボクセルに対して呼ぶ
state, flag = tracker.update_voxel(
    voxel_id="v_12_5_3",
    c_self=25,                          # このスキャンでの生点数
    neighbor_ids=["v_11_5_3"],          # 近傍ボクセルID(参考情報)
    neighbor_counts=[3],                # 近傍の生点数(ピーク判定・プーリングに使用)
    covered=True,                       # 今回、このあたりが実際にスキャン範囲内だったか
    patch_fitness=0.028,                # VGICPのfitness_score(無ければNone)
    structural_label="wall",            # 構造ラベル(無ければNone)
)

print(state, flag)                             # State.OCCUPIED, ConfidenceFlag.CONFIRMED 等
print(tracker.get_p_occ("v_12_5_3"))           # 存在確率
print(tracker.get_kappa("v_12_5_3"))           # 確信度
print(tracker.get_mu("v_12_5_3"))              # 動的度合い(柱2)
```

## 動作確認

```bash
# 1つ上のディレクトリから、パッケージとして実行する
cd ..
python3 -m spatial_state.test_spatial_state   # 検証テスト(全7件)
python3 -m spatial_state.example_usage        # 使用例
```

## 実データへの組み込みで、必要な作業

このコードは「1回分の観測を、1つのボクセルに反映する」ところまでを
実装している。実際のパイプラインに繋ぐには、以下を別途用意する必要がある。

1. **点群→ボクセルグリッド変換**: スキャンした点群から、各ボクセルの
   `c_self`(点数)を求める処理(既存の`core/voxel_grid.py`等を流用可能)
2. **近傍探索**: 各ボクセルの近傍ボクセルIDと、その点数を求める処理
3. **`covered`判定**: 近傍のヒット密度から、「今回このあたりが
   スキャン範囲内だったか」を判定する処理(§2.3)
4. **`patch_fitness`の取得**: VGICPの実行結果から、fitness_scoreを
   取り出す処理(既存の`server.py`のVGICP実行部分と連携)
5. **構造ラベルの割り当て**: ベースマップから、各ボクセルの構造ラベル
   (壁面・床面等)を求める処理、および事前分布(alpha0, beta0)の算出
   (§5.4。`tracker.set_prior()`で設定する)

## パラメータ較正時の注意

**`Params`を生成する際、以下の制約が自動的にチェックされる(§5.3.1)。**

```
n0 < (1 - kappa_th) / (kappa_th * (1 - gamma))
```

これを満たさない組み合わせを渡すと、生成時に`ValueError`が送出される。
これは、どれだけ観測を重ねても状態が永久にCONFIRMEDへ到達できない、
という致命的なパラメータ不整合を、実行前に検出するための仕組み。
