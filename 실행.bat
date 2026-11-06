@echo off
chcp 65001 > nul
cd /d "%~dp0"
set TOON_BAT=1
set PY=
python -c "import sys" >nul 2>nul && set PY=python
if not defined PY (
  py -c "import sys" >nul 2>nul && set PY=py
)
if not defined PY (
  echo.
  echo [!] Python을 찾을 수 없습니다.
  echo     설치했다면 컴퓨터를 재시작한 뒤 다시 실행하세요.
  echo     설치: https://www.python.org/downloads
  echo.
  pause
  exit /b
)
echo 사용하는 Python: %PY%
%PY% webtoon_gui.py
echo.
echo 창을 닫으려면 아무 키나 누르세요.
pause > nul
