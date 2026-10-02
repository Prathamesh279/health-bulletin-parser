@echo off
cd /d "%~dp0"
echo Installing any missing packages...
py -m pip install -r requirements.txt -q
echo.
echo Starting El Nino Health Parser...
echo The website will open in your browser.
echo Keep this window open while using the site.
echo.
py app.py
pause
