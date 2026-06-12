@echo off
:: Build py3r Analysis GUI as a standalone Windows executable.
:: Run from the repo root with the project venv active.
::
:: Requirements:
::   pip install pyinstaller
::   pip install -e .   (and py3r-behaviour installed)
::
:: Output: dist\py3r_analysis\py3r_analysis.exe

if not exist "vendor\uv.exe" (
    echo Downloading uv.exe...
    mkdir vendor 2>nul
    powershell -Command "Invoke-WebRequest -Uri https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip -OutFile vendor\uv.zip"
    powershell -Command "Expand-Archive -Path vendor\uv.zip -DestinationPath vendor -Force"
    del vendor\uv.zip vendor\uvw.exe vendor\uvx.exe 2>nul
    if not exist "vendor\uv.exe" (
        echo.
        echo Failed to download uv.exe.
        exit /b 1
    )
)

pyinstaller py3r_analysis_gui.spec --clean --noconfirm
if %ERRORLEVEL% neq 0 (
    echo.
    echo Build failed.
    exit /b %ERRORLEVEL%
)

echo.
echo Copying model weights...
xcopy "..\BohacekLabPoseModels\pose_estimation" "dist\py3r_analysis\models" /E /I /Y
if %ERRORLEVEL% neq 0 (
    echo.
    echo Failed to copy model weights.
    exit /b %ERRORLEVEL%
)

echo.
echo Build complete: dist\py3r_analysis\py3r_analysis.exe
