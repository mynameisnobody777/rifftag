@echo off
setlocal
title RiffTag - remove the manual (zip) install
echo ==========================================================
echo  Removes the hand-installed copy at C:\RiffTagApp and the
echo  file associations register-windows.ps1 made.
echo.
echo  Do this BEFORE testing RiffTag-x.y-Setup.exe, so the test
echo  is of the installer and not of leftovers. Both write the
echo  same registry keys, so with both present there is no way
echo  to tell which one is doing the work.
echo.
echo  Leaves C:\RiffTag alone - that is the build scratch copy
echo  and build_windows.bat needs it.
echo ==========================================================
echo.
choice /c YN /m "Remove the manual install"
if errorlevel 2 exit /b 0
echo.

taskkill /im RiffTag.exe /f >nul 2>&1

echo [1/3] Unregistering the file associations ...
if exist C:\RiffTag\packaging\register-windows.ps1 (
    powershell -ExecutionPolicy Bypass -File C:\RiffTag\packaging\register-windows.ps1 -Unregister
) else (
    echo    register-windows.ps1 not found at C:\RiffTag - removing the keys directly.
    reg delete "HKCU\Software\Classes\RiffTag.AudioFile" /f >nul 2>&1
    reg delete "HKCU\Software\Classes\Applications\RiffTag.exe" /f >nul 2>&1
)

echo [2/3] Deleting C:\RiffTagApp ...
if exist C:\RiffTagApp (
    rmdir /s /q C:\RiffTagApp
    if exist C:\RiffTagApp (
        echo    *** Could not delete it - is RiffTag still open?
    ) else (
        echo    Removed.
    )
) else (
    echo    Not there already.
)

echo [3/3] Removing desktop and Start Menu shortcuts ...
del /q "%USERPROFILE%\Desktop\RiffTag.lnk" >nul 2>&1
del /q "%PUBLIC%\Desktop\RiffTag.lnk" >nul 2>&1
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\RiffTag.lnk" >nul 2>&1
rmdir /s /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\RiffTag" >nul 2>&1

echo.
echo ==========================================================
echo  Done. Nothing of RiffTag should be left except C:\RiffTag
echo  (the build copy).
echo.
echo  Now run:  B:\apps\rifftag\dist\RiffTag-0.3-Setup.exe
echo ==========================================================
echo.
pause
