# SimpleFTP

Windows 11 向けの、シンプルなマテリアルデザイン風 FTP クライアントです。
Python の標準ライブラリ（tkinter / ftplib）だけで動くので、追加パッケージは不要です。

## インストール

### 1. Python を入れる

1. https://www.python.org/downloads/ から Windows 版（3.10 以上）をダウンロードして実行
2. インストール画面の一番下 **「Add python.exe to PATH」にチェック** を入れて「Install Now」

コマンドで入れる場合:

```
winget install Python.Python.3.13
```

### 2. SimpleFTP をダウンロード

このページ右上の **Code → Download ZIP** でダウンロードし、好きな場所に展開します。

Git を使う場合:

```
git clone https://github.com/markfour/Windows11SimpleFTP.git
```

### 3. 起動

展開したフォルダーの `run.bat` をダブルクリックします。

### exe にする（任意）

`build_exe.bat` をダブルクリックすると `dist\SimpleFTP.exe` ができます（初回は PyInstaller を自動でインストールします）。
できた exe は単体で動くので、デスクトップなど好きな場所に置いて使えます。Python が入っていない PC でも動きます。

### アップデート

新しい版をダウンロードしてファイルを上書きします（exe を使っている場合は `build_exe.bat` で作り直し）。
設定は別の場所に保存されているので、上書きしても消えません。

### アンインストール

フォルダー（または exe）を削除します。設定も消す場合は `%APPDATA%\SimpleFTP` フォルダーも削除してください。

## メモ

- 接続先・パスワード・最後に開いたフォルダーは `%APPDATA%\SimpleFTP\settings.json` に保存されます。
- パスワードは Windows の暗号化機能（DPAPI）で暗号化して保存するため、同じ PC の同じ Windows ユーザーでしか読み出せません。
- 普通の FTP は通信が暗号化されません。サーバーが対応していれば FTPS を使ってください。
