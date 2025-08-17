@echo off
REM Script para iniciar o servidor do projeto ALFA-TASK (Versão Robusta)

TITLE Servidor ALFA-TASK

echo Ativando ambiente virtual...
call venv\Scripts\activate

echo Iniciando servidor Flask...
echo Para parar o servidor, feche esta janela.
echo.

REM Executa o python.exe especifico do ambiente virtual
venv\Scripts\python.exe app.py

REM O 'pause' so sera executado se o servidor falhar ao iniciar.
echo.
echo O servidor foi encerrado ou falhou ao iniciar.
pause