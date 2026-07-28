@echo off
rem run_ue_python.bat - run a UE Editor Python script headlessly against the UEtoO3DEFixture project.
rem Usage: run_ue_python.bat <script.py>   (relative or absolute path)
rem Propagates the UnrealEditor-Cmd exit code.
setlocal EnableExtensions

call "%~dp0..\paths.cmd"
if errorlevel 1 exit /b 2

if "%~1"=="" (
    echo Usage: run_ue_python.bat ^<script.py^> 1>&2
    exit /b 2
)

set "SCRIPT=%~f1"
if not exist "%SCRIPT%" (
    echo Script not found: %SCRIPT% 1>&2
    exit /b 2
)

"%UE_EDITOR_CMD%" "%UE_PROJECT%" -run=pythonscript -script="%SCRIPT%" -unattended -nop4 -nullrhi
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
