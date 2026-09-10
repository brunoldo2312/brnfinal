@echo off
title Reativador Automatico - Moeda Bruno (BRN)
color 0A

:: Garante que o script rode na pasta onde ele foi salvo
cd /d "%~dp0"

echo ========================================================
echo       REATIVANDO ECOSSISTEMA DA MOEDA BRUNO (BRN)
echo ========================================================
echo.

:: Verifica se o Python esta instalado no PATH do Windows
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado! Certifique-se de que o Python esta instalado e marcado nas Variaveis de Ambiente.
    pause
    exit /b
)

echo [1/3] Iniciando o No Principal da Blockchain (Porta 6001)...
:: Abre uma nova janela para o No Principal
start "BRN - No Principal" cmd /k "python bruno_blockchain_real.py 6001"
echo Aguardando 5 segundos para o no carregar o banco de dados...
timeout /t 5 /nobreak >nul
echo.

echo [2/3] Iniciando o Explorador de Blocos (Porta 8080)...
:: Abre uma nova janela para o Explorador
start "BRN - Explorador de Blocos" cmd /k "python explorer.py"
echo Aguardando 3 segundos para o explorador iniciar...
timeout /t 3 /nobreak >nul
echo.

echo [3/3] Criando tunel publico com Ngrok (Porta 8080)...
:: Abre uma nova janela para o Ngrok
start "BRN - Ngrok Tunnel" cmd /k "ngrok http 8080"
echo.

echo ========================================================
echo  TUDO PRONTO! 
echo.
echo  1. As 3 janelas foram abertas.
echo  2. Va ate a janela do Ngrok e copie o link "https://...ngrok-free.app"
echo  3. Cole este link no formulario da exchange (Explorador de Blocos).
echo ========================================================
echo.
pause