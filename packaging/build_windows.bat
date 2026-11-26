@echo off
setlocal enabledelayedexpansion
title RiffTag - build the Windows app
echo ==========================================================
echo  Builds RiffTag.exe from the current source, installs it
echo  to C:\RiffTagApp, registers the right-click "Open with"
echo  entry, and leaves a zip AND a Setup.exe on the share.
echo.
echo  Takes a few minutes. Leave the window open.
echo ==========================================================
echo.

REM ---------------------------------------------------- the share
if not exist B:\apps\rifftag\packaging\build-windows.ps1 (
    echo  *** B: is not mapped, or the project is not on it.
    echo  *** Run map_brain.bat first, then start this again.
    pause & exit /b 1
)

REM ---------------------------------------------------- close a running copy
REM robocopy cannot overwrite an exe that is running, and the failure looks
REM like a permissions problem rather than what it is.
taskkill /im RiffTag.exe /f >nul 2>&1

REM ---------------------------------------------------- copy to a local disk
REM Never build on B:. PyInstaller writes thousands of small files and the
REM share is slow enough that a stale __pycache__ once shipped the wrong
REM version number in a build.
echo [1/6] Copying the project to C:\RiffTag ...
robocopy B:\apps\rifftag C:\RiffTag /MIR /NFL /NDL /NJH /NJS /NP ^
    /XD build dist .git web .pytest_cache __pycache__ .wrangler >nul
if errorlevel 8 (echo  *** Copy failed. & pause & exit /b 1)

REM ---------------------------------------------------- build
echo [2/6] Building RiffTag.exe - this is the slow part ...
pushd C:\RiffTag
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
if errorlevel 1 (echo  *** Build failed - send Claude what it printed. & popd & pause & exit /b 1)

if not exist C:\RiffTag\dist\windows\RiffTag\RiffTag.exe (
    echo  *** No exe was produced. Send Claude the output above.
    popd & pause & exit /b 1
)

REM ---------------------------------------------------- install
echo [3/6] Installing to C:\RiffTagApp ...
robocopy C:\RiffTag\dist\windows\RiffTag C:\RiffTagApp /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (echo  *** Install failed. & popd & pause & exit /b 1)

REM ---------------------------------------------------- file associations
REM HKCU only, so no administrator prompt. This does NOT steal the default
REM handler for .mp3 - it only adds RiffTag to the "Open with" list.
echo [4/6] Registering the right-click "Open with" entry ...
powershell -ExecutionPolicy Bypass -File C:\RiffTag\packaging\register-windows.ps1 -Exe C:\RiffTagApp\RiffTag.exe

REM ---------------------------------------------------- zip for the website
echo [5/6] Zipping for the website ...
for /f "delims=" %%V in ('powershell -NoProfile -Command ^
    "[regex]::Match((Get-Content C:\RiffTag\rifftag\gui\about.py -Raw), 'VERSION\s*=\s*\"([^\"]+)\"').Groups[1].Value"') do set "VER=%%V"
if not defined VER set "VER=unknown"

REM Zip to a LOCAL path first, then copy. Compress-Archive writing straight
REM to the mapped B: drive is what failed on 2026-11-26 - the build and the
REM install both worked and only the zip never appeared. Copying the
REM finished file across afterwards is boring and reliable.
set "LOCAL=C:\RiffTag\rifftag-!VER!-windows.zip"
set "ZIP=B:\apps\rifftag\dist\rifftag-!VER!-windows.zip"
if exist "!LOCAL!" del /q "!LOCAL!"
powershell -NoProfile -Command ^
    "Compress-Archive -Path 'C:\RiffTag\dist\windows\RiffTag' -DestinationPath '!LOCAL!' -CompressionLevel Optimal -Force"
if exist "!LOCAL!" copy /y "!LOCAL!" "!ZIP!" >nul
popd

REM ------------------------------------------------- the real installer
REM Inno Setup is optional: if it is not installed the zip is still produced
REM and everything above has already worked. The .iss is GENERATED from
REM rifftag/desktopfile.py so the installer's file-type list cannot drift
REM from the Linux one.
echo [6/6] Building the installer ...
set "ISCC="
for %%C in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
            "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if exist %%C set "ISCC=%%~C"
)
if not defined ISCC (
    echo    Inno Setup not found - skipping the installer.
    echo    Install it from https://jrsoftware.org/isdl.php and run this again.
) else (
    pushd C:\RiffTag
    python packaging\make_iss.py C:\RiffTag\build\rifftag.iss !VER!
    if exist C:\RiffTag\build\rifftag.iss (
        "!ISCC!" /Q C:\RiffTag\build\rifftag.iss
        if exist "C:\RiffTag\dist\RiffTag-!VER!-Setup.exe" (
            copy /y "C:\RiffTag\dist\RiffTag-!VER!-Setup.exe" ^
                "B:\apps\rifftag\dist\RiffTag-!VER!-Setup.exe" >nul
            echo    Installer built and copied to the share.
        ) else (
            echo    *** ISCC did not produce a Setup.exe - send Claude the output.
        )
    ) else (
        echo    *** make_iss.py wrote nothing - send Claude the output.
    )
    popd
)

echo.
if exist "!ZIP!" (
    echo ==========================================================
    echo  Done. RiffTag !VER! is installed at C:\RiffTagApp.
    echo.
    echo  Test it: right-click a .wav or .mp3, Open with, RiffTag.
    echo  It should open that file's folder with the file selected.
    echo.
    echo  For the website, on the share at apps\rifftag\dist\:
    echo    rifftag-!VER!-windows.zip     ^(portable, unzip and run^)
    echo    RiffTag-!VER!-Setup.exe       ^(installer, if Inno Setup is here^)
    echo  Tell Claude they are there and he will upload them.
    echo ==========================================================
) else (
    echo  *** The app is installed but the zip was not written to B:.
    echo  *** Tell Claude - everything else worked.
)
echo.
pause
