@echo off
setlocal
cd /d "%~dp0"

REM ==== Token publico do Ngrok ====
set "NGROK_AUTHTOKEN=3J8xHeVX46aXrOZeXnKrVVFMLTr"

REM ==== Sobe o no BRN em uma janela separada ====
start "BRN Node 6001" cmd /k python bruno_blockchain_real.py 6001

REM ==== Espera o no subir ====
timeout /t 5 /nobreak >nul

REM ==== Abre o tunel publico para o explorer (porta 8080) ====
echo Abrindo tunel Ngrok para http://localhost:8080 ...
ngrok http 8080

pause
