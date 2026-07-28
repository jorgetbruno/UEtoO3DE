@echo off
rem run_ue_editor_python.bat - run a Python script in a FULL UE editor session.
rem
rem Usage: run_ue_editor_python.bat <script.py> [result-file]
rem
rem The commandlet runner (run_ue_python.bat, -run=pythonscript -nullrhi) cannot
rem host anything that touches the editor UI: ToolMenus, Slate dialogs and the
rem skeletal FBX exporter all need a real editor. This is the same invocation
rem export_fixture.bat uses, generalized.
rem
rem The editor's process exit code is not trustworthy under quit_editor, so CI
rem asserts on the RESULT line in the result file (plan constraint 10).
setlocal EnableExtensions

call "%~dp0..\paths.cmd"
if errorlevel 1 exit /b 2

if "%~1"=="" (
    echo Usage: run_ue_editor_python.bat ^<script.py^> [result-file] 1>&2
    exit /b 2
)

set "SCRIPT=%~f1"
if not exist "%SCRIPT%" (
    echo Script not found: %SCRIPT% 1>&2
    exit /b 2
)
if "%~2"=="" (
    set "RESULT=%~dp1results\%~n1_result.txt"
) else (
    set "RESULT=%~f2"
)
for %%D in ("%RESULT%") do if not exist "%%~dpD" mkdir "%%~dpD"
if exist "%RESULT%" del /q "%RESULT%"

"%UE_EDITOR%" "%UE_PROJECT%" -ExecutePythonScript="%SCRIPT%" -unattended -nop4 -nosplash

if not exist "%RESULT%" (
  echo RESULT FILE MISSING: %RESULT% 1>&2
  endlocal & exit /b 2
)
findstr /C:"RESULT: PASS" "%RESULT%" >nul
if errorlevel 1 (
  echo see %RESULT% 1>&2
  endlocal & exit /b 3
)
endlocal & exit /b 0
