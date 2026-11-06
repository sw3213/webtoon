@echo off
chcp 65001 > nul
cd /d "%~dp0"
where python >nul 2>nul && (python webtoon_downloader.py & goto :eof)
where py >nul 2>nul && (py webtoon_downloader.py & goto :eof)
echo.
echo Python을 찾을 수 없습니다. 설치 후 컴퓨터를 재시작해 보세요.
echo 설치: https://www.python.org/downloads
pause
