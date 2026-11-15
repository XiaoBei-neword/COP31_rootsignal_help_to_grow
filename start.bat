@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 根脉 RootSignal

echo.
echo   根脉 RootSignal · 小农户气候决策工具
echo   本地运行版，Ctrl+C 停止
echo   --------------------------------------------------

rem ---------------------------------------------------------------
rem 1. 找一个能用的 Python（3.9 以上）
rem    优先项目内虚拟环境 .venv，其次 py 启动器，最后 PATH 与常见安装目录
rem ---------------------------------------------------------------
set "PY="

if exist ".venv\Scripts\python.exe" (
  set PY=".venv\Scripts\python.exe"
  goto :got_python
)

for %%C in ("py -3.12" "py -3.13" "py -3" "py") do (
  if not defined PY (
    %%~C -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=%%~C"
  )
)

if not defined PY (
  for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
  ) do (
    if not defined PY if exist %%P set "PY=%%P"
  )
)

if not defined PY (
  for %%C in ("python" "python3") do (
    if not defined PY (
      %%~C -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
      if not errorlevel 1 set "PY=%%~C"
    )
  )
)

if not defined PY (
  echo   [x] 没找到可用的 Python，需要 3.9 或以上版本。
  echo.
  echo       请先安装 Python 3.12：https://www.python.org/downloads/
  echo       安装时勾选 "Add python.exe to PATH"，装完再双击本文件。
  goto :fail
)

:got_python
set "PYVER="
%PY% -c "import sys;print(sys.version.split()[0])" > "%TEMP%\rootsignal_pyver.txt" 2>nul
if exist "%TEMP%\rootsignal_pyver.txt" set /p PYVER=<"%TEMP%\rootsignal_pyver.txt"
del "%TEMP%\rootsignal_pyver.txt" >nul 2>nul
echo   Python：%PY%  v%PYVER%

rem ---------------------------------------------------------------
rem 2. 依赖：缺了就自动安装，默认源失败时换清华镜像
rem ---------------------------------------------------------------
%PY% -c "import flask, requests, dotenv" >nul 2>nul
if errorlevel 1 (
  echo   [i] 首次运行，正在安装依赖 Flask / requests / python-dotenv ...
  %PY% -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (
    echo   [!] 默认源安装失败，改用清华镜像重试 ...
    %PY% -m pip install --disable-pip-version-check -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  )
  %PY% -c "import flask, requests, dotenv" >nul 2>nul
  if errorlevel 1 (
    echo   [x] 依赖安装失败。请手动执行下面这行，成功后重新双击本文件：
    echo       %PY% -m pip install -r requirements.txt
    goto :fail
  )
  echo   [ok] 依赖已就绪
)

rem ---------------------------------------------------------------
rem 3. 演示账号：缺失才补，已存在则跳过，不会重复写记录
rem ---------------------------------------------------------------
%PY% seed_demo.py --if-missing

rem ---------------------------------------------------------------
rem 4. 端口 5000：已经在跑就直接开浏览器；被别的程序占用才停下来
rem ---------------------------------------------------------------
netstat -ano | findstr ":5000 " | findstr "LISTENING" >nul 2>nul
if errorlevel 1 goto :port_free

echo   [i] 端口 5000 已被占用，确认是不是根脉自己 ...
powershell -NoProfile -Command "if((Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:5000/login').StatusCode -eq 200){exit 0}else{exit 1}" >nul 2>nul
if errorlevel 1 (
  echo   [!] 占用 5000 的不是根脉：先关掉它再双击本文件。
  goto :fail
)
echo   [ok] 根脉已经在运行，不用重复启动。
if not defined NO_BROWSER start "" "http://127.0.0.1:5000/"
goto :eof

:port_free
rem ---------------------------------------------------------------
rem 5. 启动服务并打开浏览器
rem ---------------------------------------------------------------
echo.
echo   启动后浏览器会自动打开 http://127.0.0.1:5000
echo   登录：demo_a / demo1234
echo   提示：这个窗口关掉，网页就打不开了（Ctrl+C 停止服务）
echo.
if not defined NO_BROWSER (
  start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:5000/'"
)

%PY% app.py
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
  echo.
  echo   [x] 服务启动失败，退出码 %CODE% 。常见原因：
  echo       - 端口 5000 被占用：关掉占用它的程序再试
  echo       - 依赖缺失：执行 %PY% -m pip install -r requirements.txt
  echo       - 具体报错见上面的输出
  goto :fail
)

echo.
echo   服务已停止。
goto :eof

:fail
echo.
pause
exit /b 1
