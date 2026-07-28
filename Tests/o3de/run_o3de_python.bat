@echo off
rem run_o3de_python.bat - run a Python script in the headless O3DE editor.
rem
rem Usage: run_o3de_python.bat <script.py> [result-file] [project-path]
rem
rem Generalizes the runner pattern established by run_s0_1.bat in M0. The
rem editor splits --runpythonargs on spaces, so the result path must not
rem contain any; forward slashes keep AZ::StringFunc::Tokenize happy.
rem
rem Exit-code contract (Global Constraint 10) - CI asserts on THIS code:
rem   editor exit code != 0            -> propagated verbatim
rem   result file missing              -> 2 (no verdict is a failure)
rem   result file lacks "RESULT: PASS" -> 3
setlocal EnableExtensions

if "%~1"=="" (
    echo Usage: run_o3de_python.bat ^<script.py^> [result-file] [project-path] 1>&2
    exit /b 2
)

call "%~dp0..\paths.cmd"
if errorlevel 1 exit /b 2

set "EDITOR=%O3DE_BIN%\Editor.exe"
set "SCRIPT=%~f1"
set "SCRIPT_DIR=%~dp0"

if "%~2"=="" (
    set "RESULT=%SCRIPT_DIR%results\%~n1_result.txt"
) else (
    set "RESULT=%~f2"
)
if "%~3"=="" (
    set "PROJECT=%O3DE_PROJECT_JOLT%"
) else (
    set "PROJECT=%~3"
)

if not exist "%SCRIPT%" (
    echo Script not found: %SCRIPT% 1>&2
    exit /b 2
)
for %%D in ("%RESULT%") do if not exist "%%~dpD" mkdir "%%~dpD"
if exist "%RESULT%" del /q "%RESULT%"

set "RESULT_PY=%RESULT:\=/%"

"%EDITOR%" --project-path="%PROJECT%" -BatchMode -autotest_mode --runpython "%SCRIPT%" --runpythonargs "%RESULT_PY%"
set "EC=%ERRORLEVEL%"

echo Editor exit code: %EC%
if not "%EC%"=="0" (
  endlocal & exit /b %EC%
)
if not exist "%RESULT%" (
  echo RESULT FILE MISSING: %RESULT% 1>&2
  endlocal & exit /b 2
)
findstr /C:"RESULT: PASS" "%RESULT%" >nul
if errorlevel 1 (
  endlocal & exit /b 3
)
endlocal & exit /b 0
