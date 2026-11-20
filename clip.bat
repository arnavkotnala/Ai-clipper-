@echo off
rem Double-click, paste a YouTube (or Twitch / Kick) link, press Enter. The clips open in a folder when done.
rem Or:  clip.bat "https://www.youtube.com/watch?v=..."   (extra options: see scripts\clip.ps1)
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "scripts\clip.ps1" %*
echo.
pause
