# spatial-state

空間ID(Local Spatial ID)ベースの、屋内デジタルツイン永続状態モデルのプロトタイプ。

スマホ・LiDAR計測から得た点群を、建物内のローカル空間ごとに位置合わせし、
空間ID単位で「占有/消失」「静的/動的」の状態を管理するシステムの、
UI・バックエンドの試作。

## できること

- **建物一覧 → ローカル空間一覧 → 3Dビューワ**というブラウザUI
- **ビューワモード**: 空間IDベースのボクセル状態(占有・消失・動的/静的)を3D表示
- **追加モード**: ラフレジストレーション(新規計測データをベースマップに手動で位置合わせ)
- バックエンド: ラフレジ結果を受け取り、**VGICP(fast_gicp)による精密位置合わせ →
  空間ID格子でのボクセル化(JSON化)**まで自動で行うパイプライン

## クイックスタート

環境構築の詳細は [`SETUP.md`](./SETUP.md) を参照してください。概要は以下の通りです。

```powershell
cd backend
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item vendor\pygicp-win-cp38\* venv\Lib\site-packages\ -Force
.\venv\Scripts\python.exe server.py
```

`http://localhost:8000` にアクセスすると、UIが開きます。

## フォルダ構成

```
spatial-state/
├── local_space_prototype.html   ← フロントのエントリーポイント
├── registration/                 ← ラフレジストレーション用モジュール(JS)
│   ├── rigid-transform.js        ← 剛体変換アルゴリズム本体
│   ├── spatial-hash.js           ← ボクセルバケット近傍探索
│   ├── pca.js                    ← 平面フィッティング(エッジ特徴検出用)
│   ├── edge-feature.js           ← エッジ特徴の精緻化
│   ├── pointcloud-io.js          ← 点群の読み込み・出力
│   └── registration-controller.js ← 追加モードのUI・3Dビューワ制御
├── base_maps/                    ← ベースマップの実体・一覧(manifest.json)
├── backend/
│   ├── server.py                 ← 受信・VGICP・JSON化のパイプライン
│   ├── requirements.txt
│   ├── space_definitions/        ← 各ローカル空間の座標定義JSON
│   ├── vendor/pygicp-win-cp38/   ← pygicp等、ビルド済みバイナリ(再現困難なため同梱)
│   └── data/                     ← 実行時に生成される中間データ(Git管理外)
└── docs/
    ├── spatial_id_design_memo.md      ← データモデル・更新アルゴリズムの設計
    └── system_architecture_memo.md    ← システム構成・ディレクトリ構成の設計
```

## 設計ドキュメント

- [`docs/spatial_id_design_memo.md`](./docs/spatial_id_design_memo.md):
  空間IDのスキーマ、事前分布(構造ラベル)の考え方、更新アルゴリズムの叩き台
- [`docs/system_architecture_memo.md`](./docs/system_architecture_memo.md):
  システム全体のデータフロー、ディレクトリ構成、開発/実運用環境の考え方

## 現状の制約・今後の課題

- `backend/space_definitions/`のマッチングは、`space_id`とファイル内`id`の
  前方一致という暫定ルール(将来DBの参照に差し替える想定)
- ズームレベルは今は固定値(`DEFAULT_ZOOM_LEVEL`)。ローカル空間ごとの
  基準ズームレベル管理は未実装
- 空間ID格子でのボクセル化は、点ごとのヒット数を集計するところまで。
  ログオッズ更新・確信度κ・4状態モデルへの反映は未統合
