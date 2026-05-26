@echo off
:: Build py3r Analysis GUI as a standalone Windows executable.
:: Run from the repo root with the project venv active.
::
:: Requirements:
::   pip install pyinstaller
::   pip install -e .   (and py3r-behaviour installed)
::
:: Output: dist\py3r_analysis\py3r_analysis.exe

pyinstaller py3r_analysis_gui.spec --clean --noconfirm
if %ERRORLEVEL% neq 0 (
    echo.
    echo Build failed.
    exit /b %ERRORLEVEL%
)
echo.
echo Build complete: dist\py3r_analysis\py3r_analysis.exe
