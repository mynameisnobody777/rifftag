@echo off
setlocal enabledelayedexpansion
title RiffTag - zip the built Windows app for the website
echo ==========================================================
echo  Zips C:\RiffTag\dist\windows\RiffTag and puts the zip on
echo  the share, for upload to the website.
echo.
echo  Only needed if build_windows.bat built the app but could
echo  not write the zip.
echo ==========================================================
echo.

set "SRC=C:\RiffTag\dist\windows\RiffTag"
if not exist "%SRC%\RiffTag.exe" (
    echo  *** Nothing built at %SRC%
    echo  *** Run build_windows.bat first.
    pause & exit /b 1
)

if not exist B:\apps\rifftag\dist (
    echo  *** B:\apps\rifftag\dist is not reachable.
    echo  *** Run map_brain.bat, then start this again.
    pause & exit /b 1
)

for /f "delims=" %%V in ('powershell -NoProfile -Command ^
    "[regex]::Match((Get-Content C:\RiffTag\rifftag\gui\about.py -Raw), 'VERSION\s*=\s*\"([^\"]+)\"').Groups[1].Value"') do set "VER=%%V"
if not defined VER set "VER=unknown"
echo Version: !VER!
echo.

REM Zip to a LOCAL path first. Compress-Archive writing straight to a mapped
REM network drive is the step that failed on 2026-11-26: the build and the
REM install both worked and only the zip never appeared on B:. Copying the
REM finished file across afterwards is boring and reliable.
set "LOCAL=C:\RiffTag\rifftag-!VER!-windows.zip"
if exist "!LOCAL!" del /q "!LOCAL!"

echo [1/2] Zipping to !LOCAL! - a couple of minutes, 118 MB of Qt ...
powershell -NoProfile -Command "Compress-Archive -Path '%SRC%' -DestinationPath '!LOCAL!' -CompressionLevel Optimal -Force"
if not exist "!LOCAL!" (
    echo  *** The zip was not created. Send Claude anything printed above.
    pause & exit /b 1
)

echo [2/2] Copying to the share ...
copy /y "!LOCAL!" "B:\apps\rifftag\dist\rifftag-!VER!-windows.zip" >nul
if errorlevel 1 (
    echo  *** Could not copy to B:. The zip is still at !LOCAL!
    pause & exit /b 1
)

echo.
echo ==========================================================
echo  Done. On the share at:
echo    apps\rifftag\dist\rifftag-!VER!-windows.zip
echo  Tell Claude it is there and he will upload it.
echo ==========================================================
echo.
pause
