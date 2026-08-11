# セットアップ手順

このリポジトリには、`venv`(仮想環境)自体は含まれていません
(サイズが大きく、大部分は`pip install`で再現できるため)。
**`pygicp`(fast_gicp)関連のビルド済みバイナリだけは、再現が難しいため
`backend/vendor/pygicp-win-cp38/`にそのまま同梱しています。**

## 前提

- Windows(pygicp関連バイナリは`win_amd64`向けにビルド済み)
- Python 3.8系(`pygicp.cp38-win_amd64.pyd`の`cp38`はPython 3.8を指す。
  別バージョンのPythonを使う場合、このバイナリはそのままでは使えないため、
  自前でpygicp/fast_gicpをビルドし直す必要がある)

## 手順

```powershell
cd backend

# 1. 仮想環境を作る
python -m venv venv

# 2. requirements.txtにあるもの(pip install可能なもの)を入れる
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. pygicp関連(pip install不可、ビルド済みバイナリをそのままコピー)を入れる
Copy-Item vendor\pygicp-win-cp38\* venv\Lib\site-packages\ -Force

# 4. 動作確認
.\venv\Scripts\python.exe -c "import pygicp; import flask; import open3d; print('全部OK')"

# 5. 起動
.\venv\Scripts\python.exe server.py
```

`http://localhost:8000` にアクセスすれば、これまで通り動きます。

## なぜvenvを丸ごと入れていないか

`venv`全体は1GBを超えており、GitHubの1ファイル100MB制限にも抵触しうる
サイズです。中身のほとんど(numpy・scipy・open3d・flask等)は
`requirements.txt`から`pip install`すれば誰でも再現できるため、
バックアップの必要がありません。

`pygicp`(fast_gicp)だけは例外です。PyPIに無く、Windows上でのソースからの
ビルドには追加のビルドツールが必要になることが多いため、
**動作確認済みのビルド済みバイナリ(約29MB)を`backend/vendor/`に
そのまま保存**しています。
