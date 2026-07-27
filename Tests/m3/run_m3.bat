@echo off
rem run_m3.bat - the full M3 acceptance run (plan v2.2, M3).
rem
rem   1. backend detection unit tests (offline; the never-guess rules)
rem   2. seam guard (no physics component names outside adapters/)
rem   3. smoke import (editor): import the fixture with physics through the
rem      adapter, simulate in game mode, assert dynamic rest at the analytic
rem      height within adapter.contact_offset()-derived tolerance, static
rem      floor still, kinematic hovering, trigger pass-through, mesh-collider
rem      bake stopping a probe ball
rem   4. JoltPhysics gem regression: AzTestRunner on both test DLLs -- exit
rem      code and zero failures, no pinned counts (they change as the gem
rem      evolves; plan M3)
rem
rem Prereqs: Fixture_01 exported and staged, AP run (run_m2.bat covers it).
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
set "PY=python"
set "RUNNER=C:\O3DE\26.05\bin\Windows\profile\Default\AzTestRunner.exe"
set "GEMBIN=C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt\build\windows\bin\profile"

echo === 1/4  backend detection unit tests ===
%PY% "%REPO%\Tests\m3\test_backend_detection.py"
if errorlevel 1 goto :failed

echo === 2/4  seam guard ===
%PY% "%REPO%\Tests\m3\test_seam_guard.py"
if errorlevel 1 goto :failed

echo === 3/4  smoke import + simulation (editor) ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3\m3_smoke_import.py" "%REPO%\Tests\m3\results\m3_smoke_import_result.txt"
if errorlevel 1 (
  echo   see Tests\m3\results\m3_smoke_import_result.txt
  goto :failed
)

echo === 4/4  JoltPhysics gem regression ===
"%RUNNER%" "%GEMBIN%\JoltPhysics.Tests.dll" AzRunUnitTests >nul 2>&1
if errorlevel 1 (
  echo   JoltPhysics.Tests.dll failed
  goto :failed
)
"%RUNNER%" "%GEMBIN%\JoltPhysics.Editor.Tests.dll" AzRunUnitTests >nul 2>&1
if errorlevel 1 (
  echo   JoltPhysics.Editor.Tests.dll failed
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
