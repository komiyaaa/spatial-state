# GUIシステム仕様書(現状版)

このドキュメントは、`local_space_prototype.html`を中心としたGUIと、
それを支える`backend/server.py`のAPI・データフローについて、**現時点での
実装内容**をまとめた仕様書である。設計の経緯・数式的な根拠は
`docs/stage1_formalization_v4.md`・`docs/spatial_id_design_memo_v2.md`を、
運用手順は`README.md`・`SETUP.md`を参照。本書はそれらと重複する内容を
最小限にし、「今、画面から何をすると、裏で何が起きるか」に焦点を当てる。

---

## 1. 全体構成

```
local_space_prototype.html ← GUIのエントリーポイント(単一HTMLファイル)
├── registration/                  追加モード(ラフレジストレーション)用JS
│   ├── registration-controller.js  追加モード画面全体の制御
│   ├── pointcloud-io.js            点群の読み込み・書き出し・サーバー送信
│   ├── rigid-transform.js          剛体変換(Z軸回りの位置合わせ)アルゴリズム
│   ├── spatial-hash.js             近傍探索用の空間ハッシュ
│   ├── pca.js                      平面フィッティング(エッジ特徴検出用)
│   └── edge-feature.js             エッジ特徴の精緻化
└── backend/
    ├── server.py                   Flaskサーバー。全APIエンドポイントを定義
    ├── registry.py                 建物・ローカル空間の永続化
    ├── space_definition_generator.py  ベースマップからの座標定義自動生成(PCA)
    ├── spatial_state/              段階1本体(3本柱統合モデル)の実装パッケージ(内部表現、無変更)
    ├── spatial_state_updater.py    Updater: scan_jsonのhits→spatial_state本体への配線(Registration/VGICP側)
    ├── state_store.py              Repository: 占有状態(spatial_state内部表現)の永続化
    ├── spatial_state_view.py       Presentation/Read Model: 内部表現→Viewer向け表示契約(presence/confidence/mobility)への変換
    ├── spatial_neighbors.py        空間IDの面隣接6方向を計算
    ├── space_definitions/*.json    ローカル空間ごとの座標定義(git管理)
    ├── buildings.json               建物一覧(git管理)
    ├── local_spaces.json            ローカル空間一覧(git管理)
    └── data/                        実行時に生成される中間データ(git管理外)
        ├── rough_registered/{space_id}/   ラフレジ結果(.ply)
        ├── precise_registered/{space_id}/ VGICP精密位置合わせ結果(.ply)
        ├── scan_json/{space_id}/          空間IDごとのヒット数(.json)
        ├── vgicp_logs/                    VGICPのfitness_scoreログ(.txt/.json)
        ├── tracker_state/{space_id}.json  占有状態(alpha/beta等)の永続化
        └── verify_output/{run_id}/        検証モードの一時出力(本番データと分離)

shared/                               複数Viewerで共有するJSモジュール
├── display-coordinates.js            raw座標(Z-up)⇔Three.js表示座標(Y-up)
├── local-spatial-id.js               Local Spatial ID→world座標(座標系2)
└── spatial-state-client.js           Spatial State APIのfetch+表示補助(意味判定は持たない)

integrated/
└── integrated-view.js                複数Local Spaceの統合表示(Nodal Information由来のtransform適用)

base_maps/
├── manifest.json                   ベースマップ一覧({id, label, file})
└── *.las                            ベースマップ本体(点群)
```

GUIは`http://localhost:8000/`(Flaskが`local_space_prototype.html`を配信)
にアクセスして使用する。バックエンドは単一プロセスのFlask開発用サーバー
(`server.py`)で、DBは使わずJSONファイルへの直接読み書きで永続化している。

---

## 2. 画面構成

GUIは大きく2画面(内部的には`<div>`の表示/非表示切り替え)で構成される。

### 画面1: 建物一覧 → ローカル空間一覧(`#screenList`)

左ペイン(建物一覧)と右ペイン(選択中の建物のローカル空間一覧)の2カラム。

| UI要素 | 説明 |
|---|---|
| 建物検索ボックス | 建物名・不動産番号の部分一致で建物一覧を絞り込む |
| 建物一覧クリック | その建物のローカル空間一覧をAPIから取得して右ペインに表示 |
| 「＋ 建物」ボタン | 建物追加フォームの表示/非表示を切り替える |
| ローカル空間カードクリック | 画面2(詳細)へ遷移し、ビューワモードで開く |
| 「＋ ローカル空間」ボタン | ローカル空間追加フォームの表示/非表示を切り替える(建物未選択時はアラートで案内) |

#### 2.1 建物を追加する

「＋ 建物」→ フォームに入力 → 「建物を追加」。

| 項目 | 必須/任意 | 内容 |
|---|---|---|
| 建物名 | 必須 | 表示名。`POST /api/buildings`の`name` |
| 不動産番号 | 任意 | 空なら「未設定」として保存される |
| 住所 | 任意 | |

送信すると`POST /api/buildings`が呼ばれ、成功したらその建物が自動的に
選択状態になり(`selectBuilding()`)、右ペインに切り替わる。
`building_id`は建物名から自動生成される(§5.2参照)。

#### 2.2 ローカル空間を追加する(建物追加からの一気通貫フロー)

建物を選択した状態で「＋ ローカル空間」→ フォームに入力 →
「アップロードして作成」。**この1回の送信で、座標定義の自動生成から
ベースマップ登録まで完了する**(バックエンド側は`POST /api/local-spaces`
1本で処理、§4.2参照)。

| 項目 | 必須/任意 | 内容 |
|---|---|---|
| 特定コード | 必須 | 建物内でこのローカル空間を識別する短いコード(例: `G003`)。`base_maps/{コード}.las`・`space_definitions/{コード}.json`のファイル名にそのまま使われる |
| 階数 | 任意(既定1) | 表示・整理用の数値。ローカル空間一覧は階ごとにグルーピングされる |
| ベースマップファイル | 必須 | `.las`または`.ply`の点群ファイル(レーザースキャナ等で計測した、対象空間内部の3D点群)。位置合わせ前の生データでよい |
| 基準ボクセルサイズ(詳細設定) | 任意(既定0.1m) | 空間IDの最も細かいズームレベルでのボクセル1辺の長さ。通常は変更不要 |

送信すると、ファイルはブラウザ側で`ArrayBuffer`として読み込まれ、生バイトの
まま`POST /api/local-spaces`へ送信される(クライアント側でのパースは
行わない)。処理中はボタンが「生成中…」表示になり、完了後にローカル空間
一覧が自動更新される。

### 画面2: ローカル空間詳細(`#screenDetail`)

タブで「ビューワモード」「追加モード(ラフレジストレーション)」を切り替える。

#### 2.3 ビューワモード

- 開くと`GET /api/spatial-state/{space_id}`を呼び、そのローカル空間の
  全ボクセルの表示状態(Read Model: `presence`・`confidence`・`mobility`。
  §4.2参照)を取得して3D描画する(Three.js、InstancedMesh)。Spatial State
  Updaterの内部表現(`state`・`confidence_flag`・`p_occ`・`kappa`・`mu`等)は
  `shared/spatial-state-client.js`・`local_space_prototype.html`のどちらにも
  一切登場しない。
- **計測データがまだ無い空間(APIが空を返す)場合は、デモ用のモックボクセル
  (`generateVoxelsForSpace()`)にフォールバックする。** 新規作成直後の
  ローカル空間は、追加モードで一度も計測データを送っていなければこの
  モック表示になる。
- 色分けは「状態」(PRESENT/ABSENT、UNOBSERVEDは未描画)と「動的/静的」
  (STATIC/DYNAMIC/PENDING)の2モードを切り替え可能。「動的/静的」の判定
  (バックエンドの連続値`mu`からの変換)は`backend/spatial_state_view.py`
  (Presentation/Read Model層)が行い、フロントには結果の`mobility`だけが
  渡される(§6の既知の制約を参照)。
- ボクセルにマウスオーバーすると、空間ID・presence・mobilityをツールチップ表示。

#### 2.4 追加モード(ラフレジストレーション)

`registration-controller.js`が構築する画面。既存のベースマップ
(`base_maps/manifest.json`から選択)と、新しく計測した点群ファイル
(Target/Source)を読み込み、それぞれの3Dビューワ上で対応する2点を
クリックで指定すると、Z軸回りの剛体変換(`rigid-transform.js`)で
ラフな位置合わせを計算・プレビューできる。採用すると、変換後の点群が
PLYとして組み立てられ、`POST /api/registration-results`へ送信される
(ヘッダで`space_id`を指定)。この後の精密位置合わせ(VGICP)・空間ID化・
占有状態の更新は、全てサーバー側で自動的に行われる(§3参照)。

---

## 3. データフロー(計測データ受信後のパイプライン)

```
追加モードでの採用(ラフレジ結果)
  → POST /api/registration-results (X-Space-Id, X-Filename ヘッダ、bodyはPLYテキスト)
  → [Step1] data/rough_registered/{space_id}/{filename}.ply に保存
  → [Step2] run_vgicp()
       - base_maps/manifest.json から space_id に対応するベースマップ(.las)を特定
       - 複数ボクセルサイズ(0.2〜2.0m)を試し、fitness_scoreが最良のものを採用
       - 精密位置合わせ済み点群を data/precise_registered/{space_id}/{filename}.ply に保存
       - fitness_score(全ボクセルサイズ分)を data/vgicp_logs/{space_id}_{stem}.txt / .json に記録
       - ベースマップが見つからない・依存ライブラリ未インストール等の場合は、
         ラフレジ結果をそのまま使うフォールバック動作(fitness_score=None)
  → [Step3] convert_to_scan_json()
       - backend/space_definitions/*.json から座標定義(origin, rad, unit-size)を取得
       - 点群を空間ID("{zoom}/{f}/{x}/{y}"形式の文字列)に変換し、ID毎のヒット数を集計
       - fitness_scoreも含めて data/scan_json/{space_id}/{filename}.json に保存
  → [Step4] Spatial State Updater(backend/spatial_state_updater.py)に反映
       - ヒットした空間ID + その面隣接(spatial_neighbors.face_neighbors)を対象に、
         SpatialStateTracker.update_voxel() を実行(占有確率P_occ・確信度κ・
         動的度合いμ を更新し、state/confidence_flagを確定)
       - 更新後の状態を data/tracker_state/{space_id}.json に永続化
         (SPATIAL_VOXEL/LABEL_FITNESS_HISTORY/SCAN_SESSION の3セクション)
  → [Step5] Presentation / Read Model(backend/spatial_state_view.py)で、
       Updaterの内部表現(state/confidence_flag/mu/kappa等)を、Viewer/
       Integrated View向けの安定した表示契約(presence/confidence/mobility)
       へ変換する(2026-09-02追加。将来Updaterの内部表現が変わっても、
       この変換だけ直せばViewer側は無変更で済むようにするための層)
  → レスポンスJSON(fitness_score・今回処理したボクセルのサマリ[Read Model
    経由]を含む)を返す
```

3Dビューワ・Integrated Viewは、`GET /api/spatial-state/{space_id}`が返す
Read Model(presence/confidence/mobility)だけを読み出して表示する(§2.3・
§4.2)。Spatial State Updaterの内部表現(alpha/beta/mu/kappa/state/
confidence_flag)を、Viewer/Integrated Viewが直接参照することはない。

### 検証モード

`X-Verify-Mode: 1`ヘッダを付けて`POST /api/registration-results`を送ると、
上記の本番フロー(per-space_idフォルダ・更新エンジンへの反映)を一切通らず、
`data/verify_output/{run_id}/`に`rough.ply`・`precise.ply`・`fitness.json`
だけを書き出して終わる。VGICPパラメータの検証・パイプライン単体の動作確認用
(GUIからは呼ばれない。現状はAPIを直接叩く用途のみ)。

---

## 4. API仕様

すべて`backend/server.py`が提供する。ベースURLは`http://localhost:8000`。

### 4.1 建物・ローカル空間

| メソッド・パス | 説明 | リクエスト | レスポンス |
|---|---|---|---|
| `GET /api/buildings` | 建物一覧を取得 | なし | `{"buildings": [{building_id, real_estate_number, name, address}, ...]}` |
| `POST /api/buildings` | 建物を追加 | JSON body: `{name, real_estate_number?, address?}` | `{"status":"ok","building": {...}}` / 400(`name`未指定) |
| `GET /api/buildings/<building_id>/local-spaces` | 建物配下のローカル空間一覧 | なし | `{"building_id", "local_spaces": [{space_id, building_id, tokutei_code, floor, zoom_level, registered_at}, ...]}` |
| `POST /api/local-spaces` | ローカル空間を新規作成(座標定義の自動生成込み) | ヘッダ: `X-Building-Id`(必須), `X-Tokutei-Code`(必須), `X-Floor`(既定1), `X-Filename`, `X-Base-Unit-Size`(既定0.1)。body: ベースマップの生バイト(.las/.ply) | `{"status":"ok","local_space":{...},"space_definition_path","base_map_path","space_definition_summary":{degree,rad,height,origin,unit_size_levels}}` / 400・404・500 |

### 4.2 計測データの登録・状態取得

| メソッド・パス | 説明 | リクエスト | レスポンス |
|---|---|---|---|
| `POST /api/registration-results` | ラフレジ結果を受信し、VGICP→空間ID化→占有状態更新まで実行 | ヘッダ: `X-Space-Id`, `X-Filename`, (任意)`X-Verify-Mode`。body: PLYテキスト | 本番時: `{"status":"ok","space_id","rough_registered_path","precise_registered_path","scan_json_path","fitness_score","voxel_summary"}`(`voxel_summary`はRead Model形式、下記参照)。検証モード時: `{"status":"ok","mode":"verify","run_id","verify_output_dir","rough_path","precise_path","fitness_score","fitness_json_path"}` |
| `GET /api/spatial-state/<space_id>` | そのローカル空間の全ボクセルの現在の表示状態一覧(Read Model) | なし | `{"space_id", "voxels": {spatial_id: {presence, confidence, mobility}, ...}}`。`presence`: `PRESENT`\|`ABSENT`\|`UNOBSERVED`、`confidence`: `HIGH`\|`LOW`、`mobility`: `STATIC`\|`DYNAMIC`\|`PENDING`(暫定閾値、要較正)。**これはSpatial State Updaterの内部表現(alpha/beta/mu/kappa/state/confidence_flag)そのものではなく、backend/spatial_state_view.pyが変換した表示契約である**(将来Updaterの内部表現が変わっても、この語彙は維持される想定) |

### 4.3 静的配信

| メソッド・パス | 説明 |
|---|---|
| `GET /` | `local_space_prototype.html`を配信 |
| `GET /base_maps/manifest.json` 等 | `BASE_DIR`(リポジトリルート)配下が静的ファイルとしてそのまま配信される |

---

## 5. データモデル

### 5.1 建物(BUILDING) — `backend/buildings.json`

```json
{ "building_id": "ichigaya_tamachi", "real_estate_number": "未設定", "name": "市ヶ谷田町校舎", "address": "" }
```

### 5.2 ローカル空間(LOCAL_SPACE) — `backend/local_spaces.json`

```json
{ "space_id": "ichigaya_tamachi-G002", "building_id": "ichigaya_tamachi", "tokutei_code": "G002", "floor": 1, "zoom_level": 9, "registered_at": "2026-07-20T09:00:00" }
```

`space_id`は常に`"{building_id}-{tokutei_code}"`。`building_id`は建物名を
slug化して生成し(英字を含まない場合はuuidにフォールバック)、既存と
衝突する場合は連番を付与する。

### 5.3 座標定義(SPACE_DEFINITION) — `backend/space_definitions/{tokutei_code}.json`

```json
{
  "id": "G002",
  "degree": 91.46, "rad": 1.596,
  "height": 3.0,
  "origin": [x, y, z],
  "unit-size": { "0": 51.2, "1": 25.6, "...": "...", "9": 0.1 },
  "bounds": [[x,y,z], "...8頂点..."]
}
```

`POST /api/local-spaces`実行時に`space_definition_generator.py`が
自動生成する(PCAで建物の向きを検出→水平方向のバウンディング正方形を
算出→`unit-size`の等比数列を、ズームレベル9(`DEFAULT_ZOOM_LEVEL`)が
必ず含まれるように生成)。鉛直方向(`f`軸)のズーム別サイズはこのファイルには
含まれず、`server.py`の`_derive_vertical_unit_size()`が`bounds`のZ範囲から
リクエストの都度動的に導出する。

### 5.4 占有状態(SPATIAL_VOXEL等) — `backend/data/tracker_state/{space_id}.json`

```json
{
  "space_id": "...",
  "spatial_voxel": [
    { "spatial_id": "9/1/12/34", "z":9, "f":1, "x":12, "y":34,
      "alpha": 1.2, "beta": 0.05, "alpha_mu": 3.5, "beta_mu": 0.1,
      "n_obs": 4, "miss_streak": 0, "ever_evidenced": true,
      "was_ever_occupied_candidate": true,
      "state": "OCCUPIED", "confidence_flag": "CONFIRMED" }
  ],
  "label_fitness_history": [ { "label": "WALL", "fitness_score": 0.03 } ],
  "scan_session": [ { "session_id", "recorded_at", "source_ply", "patch_fitness_score" } ]
}
```

`state_store.py`が`spatial_state.SpatialStateTracker`のメモリ上の状態を
このJSONに保存・復元する。数式的な意味(alpha/beta、confidence_flag等)は
`docs/stage1_formalization_v4.md`を参照。

### 5.5 ベースマップ一覧 — `base_maps/manifest.json`

```json
[ { "id": "G002", "label": "G002", "file": "G002.las" } ]
```

`id`は`space_id`の部分文字列として検索される(`_find_base_map_path`)ため、
`tokutei_code`と一致させておく必要がある(`POST /api/local-spaces`が
自動でそう登録する)。

---

## 6. 既知の制約・注意点

- **PCAによる自動回転検出の向きは実行のたびに変わりうる。** `space_definition_generator.py`は点群のPCA第1主成分から建物の向き(`degree`/`rad`)を求めるが、主成分の符号は数学的に不定であるため、生成される向きが90°/180°単位でずれることがある。空間ID自体の計算は自己整合的に正しいが、「北がどちらか」という向きの基準は保証されない。
- **pygicpとscikit-learnの読み込み順序に注意。** Windows環境で、scikit-learn(PCA用)をpygicp(VGICP用)より先にプロセス内で読み込むと、VGICP実行時にセグメンテーション違反でクラッシュする既知の競合がある。`server.py`冒頭で`pygicp`を先にimportすることで回避している(この順序を変更しないこと)。
- **「動的/静的」表示の閾値は暫定値。** バックエンドの`mu`(連続値、0〜1)を`STATIC`/`DYNAMIC`/`PENDING`に変換する閾値(0.4/0.6、`backend/spatial_state_view.py`の`_mobility_hint()`。2026-09-02にフロントエンドの`muToMobilityFlag()`から移設)は仮の値で、実データでの較正が必要(`docs/spatial_id_design_memo_v2.md` §4)。
- **`covered`(スキャン範囲内か)の判定は簡易版。** 自身または面隣接がヒットしたかどうかで判定しており、スキャン全体のバウンディングボックスに基づくより正確な判定への差し替えは未実装(`server.py`内にTODOコメントあり)。
- **構造ラベル・事前分布は未実装。** 壁面・床面などのラベルに応じた事前分布(`spatial_state`の`set_prior()`)は、ベースマップ側にラベル情報が無いため使われていない。全ボクセルは中立に近い初期値(alpha=beta=0.05)から始まる。
- **建物・ローカル空間の削除/編集機能は無い。** 追加のみ実装。誤って作成した場合は`backend/buildings.json`・`backend/local_spaces.json`・`base_maps/manifest.json`・`backend/space_definitions/*.json`を直接編集する必要がある。
- **単一プロセス・ロック無しの簡易実装。** `registry.py`・`state_store.py`ともにファイルロックを持たないため、複数リクエストが同時に同じ建物・空間を更新すると競合しうる(単一利用者のローカルプロトタイプという前提)。

---

## 7. 起動方法(概要)

詳細は`SETUP.md`を参照。

```powershell
cd backend
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item vendor\pygicp-win-cp38\* venv\Lib\site-packages\ -Force
.\venv\Scripts\python.exe server.py
```

`http://localhost:8000` にアクセスするとGUIが開く。
