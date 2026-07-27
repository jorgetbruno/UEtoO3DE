@echo off
rem run_m4.bat - the full M4 acceptance run (plan v2.2, M4).
rem
rem   1. offline artifacts: synthetic TGA channel-split unit test, manifest
rem      material_data + warning codes, exported texture files, staged
rem      .material JSON (flipY, Cutout/Blended, Split alpha, references)
rem   2. editor: material assignments in the SAVED prefab (converted ->
rem      expected azmaterial; unmapped -> no Material component)
rem
rem Prereqs: the M2 chain (export, stage, AP, import) has run - run_m2.bat.
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
set "PY=python"

echo === 1/2  offline artifact checks ===
%PY% "%REPO%\Tests\m4\test_m4_artifacts.py"
if errorlevel 1 goto :failed

echo === 2/2  material assignments in the saved prefab (editor) ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m4\m4_acceptance.py" "%REPO%\Tests\m4\results\m4_acceptance_result.txt"
if errorlevel 1 (
  echo   see Tests\m4\results\m4_acceptance_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
