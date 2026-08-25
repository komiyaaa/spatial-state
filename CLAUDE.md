# CLAUDE.md — spatial-state: 段階1本体(3本柱統合モデル)の統合作業

## この作業の位置づけ

このリポジトリの `backend/server.py` は、現状こう書いてある(README.md「現状の制約・今後の課題」より引用):

> 空間ID格子でのボクセル化は、点ごとのヒット数を集計するところまで。
> ログオッズ更新・確信度κ・4状態モデルへの反映は未統合

**この「未統合」の部分を埋めるのが、今回の作業。** 具体的には、`convert_to_scan_json()` が出力する「空間ID→ヒット数」のJSONを、時間をまたいで蓄積し、占有状態(OCCUPIED/DECAYED/SCAN_COVERED/NEVER_OBSERVED)・確信度・動的度合いへと更新するロジックを追加する。

このロジック自体(数式・検証済みのPython実装)は、別途 `spatial_state/` パッケージとして作成済み。**今回の作業は「新規に理論を作る」のではなく「既存の検証済みパッケージを、このリポジトリの実際のデータ形式に接続する」こと**が中心になる。

参照ドキュメント(リポジトリに含めて渡す想定):
- `docs/stage1_formalization_v4.md` — 定式化全体(数式・全ての検証結果)
- `docs/point_cloud_requirements_full_report.md` — 点群データ要件の詳細検証(12実験)
- `docs/spatial_id_design_memo_v2.md` — **DBスキーマ・ER図・更新アルゴリズムの確定版**(旧`spatial_id_design_memo.md`を刷新したもの。手順2の永続化層は、このスキーマにそのまま対応させること)
- `spatial_state/` — 定式化の実装済みパッケージ(このまま使う)
- `evaluation_pipeline/` — 実データでの評価パイプライン(参考: 別プロジェクト用に書いたもの。データ形式がこのリポジトリと異なるため、直接は使えないが、設計の参考にする)

**注意**: リポジトリに既存の `docs/spatial_id_design_memo.md`(2026-08-06版)は、
`log_odds`・`mobility_flag`という、v4以前の古いデータモデルを前提にしている。
**`spatial_id_design_memo_v2.md`が、これを置き換える確定版。** 作業前に、
旧ファイルをリネーム(例: `spatial_id_design_memo_old.md`)するか削除し、
`spatial_id_design_memo_v2.md`を正式な設計書として扱うこと。

---

## 最初に必ずやること: 現状の再確認

**以下は、このリポジトリを一度読んだ上での理解であり、古くなっている可能性がある。作業を始める前に、必ず自分で以下を再確認すること。**

1. `backend/server.py` の `run_vgicp()` と `convert_to_scan_json()` の中身を実際に読み、この文書の記述と食い違いがないか確認する
2. `backend/data/scan_json/` に、実際に生成されたJSONサンプルがあれば見る(無ければ、一度サーバーを起動して既存のテストデータで生成してみる)
3. `git log` で、この文書作成後に変更が無いか確認する

---

## 現状のデータフロー(server.py より)

```
Web UI(追加モード)
  → POST /api/registration-results
  → rough_registered/{space_id}/{filename}.ply に保存
  → run_vgicp()
      - base_maps/manifest.json から space_id に対応するベースマップ(.las)を特定
      - 複数ボクセルサイズ(0.2〜2.0m)を試し、fitness_scoreが最良のものを採用
      - fitness_score は best_score 変数に入っているが、
        【現状】VGICP_LOG_DIR のテキストログに書くだけで、後段には渡っていない
      - 位置合わせ済み点群を precise_registered/{space_id}/{filename}.ply に保存
  → convert_to_scan_json()
      - backend/space_definitions/*.json から座標定義(origin, rad, unit-size)を取得
      - 点群を空間ID("{zoom}/{f}/{x}/{y}"形式の文字列)に変換
      - 空間IDごとのヒット点数を集計
      - scan_json/{space_id}/{filename}.json に書き出す
      - 【現状】ここで終わり。時間をまたいだ蓄積が無い(毎回、独立したJSONを吐くだけ)
```

**重要**: 空間IDの形式は `"{zoom_level}/{f}/{x}/{y}"`(例: `"9/12/34/-5"`)という文字列。`spatial_state` パッケージの `voxel_id` は単なる文字列キーとして扱う設計なので、この形式のままキーとして使ってよい(`v_x_y_z` のような別形式に変換する必要はない)。

---

## 実装すべきこと(優先順位順)

### 1. `spatial_state/` パッケージを、このリポジトリに配置する

`backend/` 直下に `spatial_state/` パッケージ一式(`params.py`, `core.py`, `voxel.py`, `tracker.py`, `__init__.py`)をそのままコピーする。中身は変更しない(検証済みのため)。

### 2. 永続化層を新設する: `backend/state_store.py`

**現状、最大の欠落はこれ。** `SpatialStateTracker` はメモリ上のオブジェクトなので、サーバーを再起動したら消える。space_idごとに、`SpatialStateTracker` の内部状態を永続化する層が要る。

**フィールド構成は `docs/spatial_id_design_memo_v2.md` §1 の ER図で確定済み。** 具体的には、以下の2テーブルに対応させること。

- **`SPATIAL_VOXEL`**: 各ボクセルの `alpha, beta, alpha_mu, beta_mu, n_obs(observation_count), miss_streak, ever_evidenced, was_ever_occupied_candidate, state, confidence_flag`
- **`LABEL_FITNESS_HISTORY`**: `SpatialStateTracker.fitness_history`(ラベルごとの`fitness_score`蓄積)。1レコード = 1ラベル×1セッションの`fitness_score`

```python
# backend/state_store.py の設計方針(擬似コード)
# テーブル定義は spatial_id_design_memo_v2.md §1 と対応させること

class StateStore:
    """space_idごとに、SpatialStateTrackerの状態をJSONファイルへ
    保存・復元する。1ファイル = 1 space_id とする
    (data/tracker_state/{space_id}.json)。
    ファイル内部は、SPATIAL_VOXEL相当のレコード配列 +
    LABEL_FITNESS_HISTORY相当のレコード配列、の2セクションに分ける。
    (SQLiteに載せ替える場合も、このJSON構造がそのままテーブル設計になる)"""

    def load(self, space_id: str) -> SpatialStateTracker:
        # ファイルが無ければ、新規のTrackerを返す
        # あれば、JSONから各VoxelStateのフィールドを復元する
        ...

    def save(self, space_id: str, tracker: SpatialStateTracker) -> None:
        # trackerの全voxelとfitness_history(ラベルごとの相場)をJSONに書き出す
        ...
```

**VoxelStateはdataclassなので、`dataclasses.asdict()`でほぼそのままシリアライズできる。** Enum(`State`, `ConfidenceFlag`)は`.value`で文字列化してから保存し、復元時に`State(value)`で戻す。

### 3. `convert_to_scan_json()` を書き換え、`fitness_score` を後段に渡す

**現状の重大な欠落**: `run_vgicp()` 内の `best_score` (fitness_score)が、テキストログに書かれるだけで、`convert_to_scan_json()` に渡っていない。これでは `w_fit`(§2.7)が機能しない。

**この`best_score`は、`spatial_id_design_memo_v2.md`の`SCAN_SESSION.patch_fitness_score`に対応する。** つまり、渡すだけでなく、`state_store.py`(手順2)が復元・保存するセッション情報の一部として、永続化されるべき値である。

`run_vgicp()` の返り値、または `receive_registration_result()` 内での呼び出し方を変更し、`best_score` を `convert_to_scan_json()` まで引き渡すこと。

```python
# run_vgicp() の返り値を (output_path, best_score) のタプルにするか、
# あるいはログファイルと同様に、precise_registered と並べて
# fitness_score だけを書いた小さなJSON/txtを1つ吐き、
# convert_to_scan_json() 側でそれを読む、という形でもよい。
# (どちらでも良いが、フォールバック時(VGICPスキップ時)は
#  fitness_score=None として扱うこと)
```

### 4. 近傍関係の定義を新設する: `backend/spatial_neighbors.py`

**現状、近傍(プーリング用)の概念が一切無い。** 空間ID `"{zoom}/{f}/{x}/{y}"` から、面隣接(6方向: f±1, x±1, y±1)の空間IDを計算する関数を作る。

```python
def face_neighbors(spatial_id: str) -> list[str]:
    zoom, f, x, y = spatial_id.split("/")
    zoom, f, x, y = zoom, int(f), int(x), int(y)
    deltas = [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]
    return [f"{zoom}/{f+df}/{x+dx}/{y+dy}" for df, dx, dy in deltas]
```

**【最重要の注意】** `spatial_state/params.py` の `n0` のデフォルト値(0.5)は、**面隣接6個を前提にした、近傍プーリングによる希釈込みの値**。これは机上検証(`stage1_formalization_v4.md` §5.3.1)で「近傍を考慮しないと$\kappa$が永久に確定閾値に届かない」という致命的な不具合が見つかり、修正した経緯がある。**このリポジトリで実際に使う近傍数(6に統一するか、`r_pool`を変えるか)を決めたら、必ず`Params.validate()`が通ることを確認すること。** 途中で近傍数を変えたら、`n0`の再検証が必須。

### 5. 更新エンジンを、`convert_to_scan_json()` の後段に接続する

```python
# server.py の receive_registration_result() 内、
# convert_to_scan_json() の後に追加するイメージ

state_store = StateStore(DATA_DIR / "tracker_state")
tracker = state_store.load(space_id)

scan_data = json.loads(scan_json_path.read_text(encoding="utf-8"))
hits = scan_data["hits"]  # {spatial_id: count}

# このスキャンで直接ヒットしたボクセル + その近傍も処理対象にする
voxels_to_process = set(hits.keys())
for sid in list(hits.keys()):
    voxels_to_process.update(face_neighbors(sid))

for sid in voxels_to_process:
    c_self = hits.get(sid, 0)
    neighbor_ids = face_neighbors(sid)
    neighbor_counts = [hits.get(n, 0) for n in neighbor_ids]
    covered = (c_self > 0) or (sum(1 for c in neighbor_counts if c > 0) >= params.h_cov)

    tracker.update_voxel(
        voxel_id=sid,
        c_self=c_self,
        neighbor_ids=neighbor_ids,
        neighbor_counts=neighbor_counts,
        covered=covered,
        patch_fitness=fitness_score,  # 手順3で渡せるようにした値
        structural_label=None,  # 構造ラベルは現状未実装(手順7参照)
    )

state_store.save(space_id, tracker)
```

**「covered(このあたりが今回のスキャン範囲内だったか)」の判定は、上記は簡易版。** 本来は、スキャン全体のバウンディングボックスに対する判定の方が正確な場合もある。既存の `_world_to_spatial_ids` が使っているクロップ範囲(`bbox`)などを流用できないか検討すること。

### 6. APIレスポンス・フロントへの状態の反映

`receive_registration_result()` のレスポンスJSON、および3Dビューワ(`local_space_prototype.html` / `registration/registration-controller.js`)が、ボクセルごとの `state`(OCCUPIED/DECAYED/SCAN_COVERED/NEVER_OBSERVED)・`confidence_flag`(CONFIRMED/PENDING)を表示できるようにする。現状のビューワが「占有・消失・動的/静的」の表示をどう実装しているか(README「できること」に記載あり)を確認し、そのデータソースを、更新エンジンの出力に差し替える。

### 7. 構造ラベル・事前分布(任意、優先度低)

現状、`base_maps/manifest.json` にも `space_definitions/*.json` にも、壁面・床面などの構造ラベルは無い。§5.4の事前分布機能は「無くても動く」設計になっているので、**今回は無理に実装しなくてよい。** 実装する場合は、ベースマップ(.las)側に何らかのラベル情報を追加する必要があり、別途データ整備が要る。将来実装する際は、`spatial_id_design_memo_v2.md`の`STRUCTURAL_LABEL_POLICY`テーブル(`prior_alpha0, prior_beta0, prior_pseudo_count`)にそのまま対応させること。

---

## 動作確認の手順

1. `spatial_state/test_spatial_state.py` が、配置後もそのまま通ることを確認する(`python -m spatial_state.test_spatial_state`)
2. 実際に1回、疑似的なリクエストを `POST /api/registration-results` に送り、`tracker_state/{space_id}.json` が生成されることを確認する
3. 同じ空間IDに対して、複数回(最低3〜4回)リクエストを送り、`alpha, beta` が回を追うごとに正しく蓄積されていく(前回の値がリセットされない)ことを確認する
4. 意図的に、ある領域だけ点群を含めないリクエストを送り、`miss_streak` が増え、`M_min`回連続で初めて負の証拠が発生することを確認する(§2.3.1)

---

## この文書の限界

- 実際の `backend/data/` 配下のサンプルJSON・実際のVGICPログは見ていない(このzipに含まれる時点のリポジトリ構造のみを見て書いている)
- フロント側(`registration-controller.js`)の現在の描画ロジックの詳細until読んでいないため、手順6は方針レベルの記述に留めている。着手前に、必ず該当ファイルを読むこと
- `n0`・`gamma`・`M_min`等の較正値は、机上検証(合成データ)に基づく初期値。実データでの較正(`docs/point_cloud_requirements_full_report.md` の方法論を参照)は、別途必要
