@echo off
chcp 65001 > nul
cd /d "%~dp0"
python webtoon_downloader.py
if errorlevel 9009 (
  echo.
  echo Python이 설치되어 있지 않습니다. https://www.python.org/downloads 에서 설치하세요.
  echo 설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요.
  pause
)
