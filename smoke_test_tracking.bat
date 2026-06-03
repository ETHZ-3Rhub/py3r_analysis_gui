@echo off
setlocal

set REPO=C:\Users\Student1\Documents\GitHub\py3r_analysis_gui
set PYTHON=%REPO%\tracking_env\Scripts\python.exe
set TRACK=%REPO%\app\trackers\track.py
set MODELS=C:\Users\Student1\Documents\GitHub\BohacekLabPoseModels\pose_estimation
set ENV_MODEL=%MODELS%\environment\environment_main
set MOUSE_MODEL=%MODELS%\mouse\mouse_top_main
set VIDEO_DIR=D:\py3r_analysis_input_tests\e
set OUT_DIR=D:\py3r_analysis_input_tests\e_tracking_output

echo --- Checking paths ---
if not exist "%PYTHON%"       echo MISSING: %PYTHON%
if not exist "%TRACK%"        echo MISSING: %TRACK%
if not exist "%ENV_MODEL%"    echo MISSING: %ENV_MODEL%
if not exist "%MOUSE_MODEL%"  echo MISSING: %MOUSE_MODEL%
if not exist "%VIDEO_DIR%"    echo MISSING: %VIDEO_DIR%
echo.

mkdir "%OUT_DIR%" 2>nul

echo --- Running tracking on all videos in %VIDEO_DIR% ---
for %%F in ("%VIDEO_DIR%\*.mp4" "%VIDEO_DIR%\*.avi" "%VIDEO_DIR%\*.mov" "%VIDEO_DIR%\*.mkv") do (
    echo.
    echo Tracking: %%F
    "%PYTHON%" "%TRACK%" "%%F" "%OUT_DIR%\%%~nF.csv" "%ENV_MODEL%:oft" "%MOUSE_MODEL%:mouse_top"
    echo Exit code: %ERRORLEVEL%
)

echo.
echo --- Done. Output in %OUT_DIR% ---
pause
