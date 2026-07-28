@echo off
rem S0.1 spike runner (M0): prefab authoring from Python in O3DE 26.05.
rem
rem Runs the headless editor with the spike script, propagates the editor
rem exit code as this script's exit code, and types the result file.
rem
rem Exit-code contract (Global Constraint 10):
rem   - editor exit code != 0          -> this .bat exits with that same code
rem   - result file missing            -> exit 2 (no verdict = failure)
rem   - result file lacks "RESULT: PASS" -> exit 3 (belt-and-braces; the spike
rem     script itself already exits non-zero on FAIL via os._exit(1))
rem CI must assert on this .bat's exit code, never on console text.

setlocal
set "EDITOR=%O3DE_BIN%\Editor.exe"
set "PROJECT=%O3DE_PROJECT_JOLT%"
set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%s0_1_prefab_spike.py"
set "RESULT=%SCRIPT_DIR%results\s0_1_result.txt"

if not exist "%SCRIPT_DIR%results" mkdir "%SCRIPT_DIR%results"

rem The editor splits --runpythonargs on spaces, so the path must not contain
rem spaces; forward slashes keep AZ::StringFunc::Tokenize happy.
set "RESULT_PY=%RESULT:\=/%"

"%EDITOR%" --project-path="%PROJECT%" -BatchMode -autotest_mode --runpython "%SCRIPT%" --runpythonargs "%RESULT_PY%"
set "EC=%ERRORLEVEL%"

echo.
echo Editor exit code: %EC%
echo ----- result file (%RESULT%) -----
if exist "%RESULT%" (type "%RESULT%") else (echo RESULT FILE MISSING)
echo ------------------------------------

if not "%EC%"=="0" (
  endlocal
  exit /b %EC%
)
if not exist "%RESULT%" (
  endlocal
  exit /b 2
)
findstr /C:"RESULT: PASS" "%RESULT%" >nul
if errorlevel 1 (
  endlocal
  exit /b 3
)
endlocal
exit /b 0
