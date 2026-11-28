@echo off
title RiffTag - build the Windows release
REM Thin launcher. The work is in packaging\build-windows-all.ps1, and
REM everything it prints is teed to the share so a failed run leaves
REM evidence - two runs failed with nothing to read afterwards.
REM
REM You can skip this file entirely and run the same thing by hand:
REM   powershell -ExecutionPolicy Bypass -File B:\apps\rifftag\packaging\build-windows-all.ps1
if not exist B:\apps\rifftag\packaging\build-windows-all.ps1 (
    echo  *** B: is not mapped. Run "1 - Connect drives.bat" first.
    pause & exit /b 1
)
powershell -ExecutionPolicy Bypass -NoProfile -Command ^
  "& { B:\apps\rifftag\packaging\build-windows-all.ps1 } 2>&1 | Tee-Object -FilePath B:\apps\rifftag\dist\build_log.txt"
echo.
pause
