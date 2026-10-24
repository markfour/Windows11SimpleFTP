# SimpleFTP — プロジェクト概要

Windows 11 向けの、シンプルなマテリアルデザイン（Material 3）風 FTP クライアント。
左に PC、右にサーバーの 2 ペインで、ファイルの転送・管理を行う。

- リポジトリ: https://github.com/markfour/Windows11SimpleFTP （public）
- 対象: Windows 11 のみ（開発・テストは Linux + Xvfb でも可能）
- 言語: Python 3.10+ / **標準ライブラリのみ**（tkinter, ftplib, ssl, ctypes）。外部パッケージは入れない
- 配布: ソース（`run.bat` で起動）または PyInstaller で単体 exe（`build_exe.bat`）

## ファイル構成

| ファイル | 役割 |
|---|---|
| `simple_ftp.py` | UI とアプリ本体（tkinter）。エントリーポイント `main()`、テスト用に `create_app()` |
| `ftp_core.py` | FTP 処理（UI 非依存）。`FtpClient`、`Entry`、LIST 解析、転送、キャンセル |
| `run.bat` | Python を探して起動（`py` → `python` → `python3`）。無ければ導入手順を表示 |
| `build_exe.bat` | PyInstaller で `dist\SimpleFTP.exe` を作成 |
| `README.md` | インストール方法のみ（使い方は書かない方針） |

## 設計

### ftp_core.py
- `FtpClient.connect()` … FTP / FTPS（明示的 TLS、`AUTH TLS` + `PROT P`）、パッシブ固定、文字コード UTF-8 / cp932。証明書検証は `verify=False` で無効化可（UI で確認ダイアログを出してから）
- `listdir()` … MLSD を優先し、非対応（500 系）なら LIST にフォールバック。LIST は UNIX 形式と IIS（Windows）形式を解析
- `upload()` / `download()` … フォルダーは再帰。ダウンロードは `.part` に書いてから `os.replace`
- キャンセル … `cancel_flag` を転送コールバック内で見て `Cancelled` を送出 → 制御接続が不定になるので `reconnect()` で張り直す

### simple_ftp.py
- **スレッド**: FTP 処理は 1 本のワーカースレッドで直列実行（`App.run(fn, ok, err)`）。結果は `queue` 経由で UI スレッドに戻し、`_poll()`（60ms ごと）でコールバック実行。**tkinter ウィジェットはワーカースレッドから触らない**
- **転送の進捗**: ワーカーは `transfer_info` タプルを書き換えるだけ、`_poll()` が読んで進捗バーとステータスに反映
- **独自ウィジェット**（すべて Canvas 描画）: `MButton`（filled / tonal / text / icon）、`MSwitch`、`LinearProgress`、`RoundCard`、`MField`、`Snackbar`、`Dialog`（`ask_text` / `confirm`）、`Tooltip`
- **見た目**: 色は先頭の定数（`PRIMARY = "#1A73E8"` など）。フォントは メイリオ 優先（日本語名「メイリオ」でも検索）。アイコンは Segoe Fluent Icons → Segoe MDL2 Assets → 代替文字。DPI スケールは `px()` で。Windows 11 のタイトルバー色は DWM API で背景色に合わせる
- **ファイル一覧**: `FilePane` が ttk.Treeview（`M.Treeview` スタイル）をラップ。フォルダー優先で並べ替え、ホバー行のハイライト、右クリックメニュー
- **Windows 固有処理（ctypes）**: DPAPI（パスワード暗号化）、`SHFileOperationW`（ごみ箱へ削除）、DPI 対応、AppUserModelID。Windows 以外では代替動作にフォールバックする

### 設定
- 保存先: `%APPDATA%\SimpleFTP\settings.json`
- 内容: host / port / user / tls / encoding / save_password / password / local_path
- パスワードは DPAPI で暗号化（`"dpapi:<base64>"`）。Windows 以外では `"b64:"`（開発用）
- `local_path` はフォルダー移動のたびに保存。復元時に存在しなければ親へさかのぼる。`""` はドライブ一覧

## 主な機能

接続（FTP/FTPS、文字コード、パスワード保存スイッチ）／2 ペイン表示・並べ替え／アップロード・ダウンロード（フォルダー再帰、進捗、キャンセル、上書き確認）／新規フォルダー・名前変更・削除（PC 側はごみ箱）／右クリック「開く」（サーバー側は `%TEMP%\SimpleFTP\` に落として開く。編集はサーバーに反映されない）／「エクスプローラーで開く」（PC 側のみ）／キーボード: Enter・ダブルクリック（フォルダーは移動、ファイルは転送）、Backspace、F2、F5、Del、Ctrl+A

## 開発メモ

- **bat ファイルは Shift_JIS（cp932）+ CRLF**。UTF-8 で保存すると文字化けする。`.gitattributes` で `*.bat binary` 指定済み
- 文言はすべて日本語。表記は「フォルダー」「ファイル」「このPC」「サーバー」
- Linux でのテスト: `uv python install 3.13` の Python（Tk 同梱）＋ `xvfb-run`。`create_app()` で起動して `root.after()` で操作を流し、`import -window root` でスクショ
- FTP の動作確認はローカルにテスト用サーバー（MLSD あり / なしの両方）を立てて行う
- 変更したら README に影響があるか確認（README はインストール方法のみ）

## 未対応・今後の候補

- エクスプローラーからのドラッグ＆ドロップ（tkinter 標準では不可。tkinterdnd2 などが必要）
- アクティブモード、SFTP
- 複数の接続先の保存（サイトマネージャー）
