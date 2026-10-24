@echo off
chcp 932 >nul
cd /d "%~dp0"
set "PY="
where py >nul 2>&1 && py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY python3 --version >nul 2>&1 && set "PY=python3"
if not defined PY goto :nopython
%PY% -c "import tkinter" >nul 2>&1 || goto :notk
start "" %PY% simple_ftp.py
exit /b 0
:notk
echo tkinter が見つかりません。python.org 版の Python を入れ直し、
echo インストール時に「tcl/tk and IDLE」にチェックが入っていることを確認してください。
pause
exit /b 1
:nopython
echo.
echo Python が見つかりませんでした。
echo.
echo   1. https://www.python.org/downloads/ から Windows 版をダウンロード
echo   2. インストール画面の一番下「Add python.exe to PATH」にチェックを入れて Install
echo      （または: winget install Python.Python.3.13）
echo   3. インストール後、このファイルをもう一度ダブルクリック
echo.
start "" https://www.python.org/downloads/
pause
exit /b 1
