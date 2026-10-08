@echo off
chcp 65001 >nul
cd /d "%~dp0"
git --version >nul 2>&1 || (echo Git o'rnatilmagan. Yuklab oling: https://git-scm.com/download/win & pause & exit /b)
if not exist "api\webhook.py" (echo Bu fayl telegram_auto papkasi ichida turishi kerak. & pause & exit /b)
echo.
echo GitHub'da YANGI, Private repo yarating va manzilini kiriting.
set /p REPO=Repo manzili, masalan https://github.com/Shakhzod9993/telegram-auto.git : 
if exist .git rmdir /s /q .git
git init
git config user.name "Shakhzod9993"
git config user.email "178584627+Shakhzod9993@users.noreply.github.com"
git add -A
git commit -m "Telegram auto bot (Vercel)"
git branch -M main
git remote add origin %REPO%
git push -u origin main --force
echo.
echo Tayyor. GitHub sahifasida api, bot, serverless papkalari ko'rinishi kerak.
pause
