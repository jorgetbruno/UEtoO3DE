@echo off
rem run_m5.bat - the full M5 acceptance run (plan v2.2, M5: lights).
rem
rem   1. offline: the pure conversion + ordering rules (UE units -> candela,
rem      "Intensity mode" written before "Intensity" -- the directional light
rem      CONVERTS on a mode change, so the reverse order silently rescales
rem      every sun in the level)
rem   2. editor: the lights in the SAVED prefab, per light type and per
rem      intensity-unit mode, against expectations recomputed independently
rem      from the manifest
rem
rem Prereqs: the M2 chain (export, stage, AP, import) has run - run_m2.bat.
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
set "PY=python"

echo === 1/2  offline conversion + ordering rules ===
%PY% "%REPO%\Tests\m5\test_light_build.py"
if errorlevel 1 goto :failed

echo === 2/2  lights in the saved prefab (editor) ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m5\m5_acceptance.py" "%REPO%\Tests\m5\results\m5_acceptance_result.txt"
if errorlevel 1 (
  echo   see Tests\m5\results\m5_acceptance_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
