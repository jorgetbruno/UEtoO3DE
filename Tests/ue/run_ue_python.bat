@echo off
rem run_ue_python.bat - run a UE Editor Python script headlessly against the UEtoO3DEFixture project.
rem Usage: run_ue_python.bat <script.py>   (relative or absolute path)
rem Propagates the UnrealEditor-Cmd exit code.
setlocal EnableExtensions

if "%~1"=="" (
    echo Usage: run_ue_python.bat ^<script.py^> 1>&2
    exit /b 2
)

set "SCRIPT=%~f1"
if not exist "%SCRIPT%" (
    echo Script not found: %SCRIPT% 1>&2
    exit /b 2
)

"D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "D:\Gamedev\UEtoO3DE\UE\UEtoO3DEFixture\UEtoO3DEFixture.uproject" -run=pythonscript -script="%SCRIPT%" -unattended -nop4 -nullrhi
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
