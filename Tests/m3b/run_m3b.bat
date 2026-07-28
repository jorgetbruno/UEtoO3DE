@echo off
rem run_m3b.bat - the M3b acceptance run (plan v2.2, M3b).
rem
rem The SAME adapter contract, asserted on BOTH backends:
rem   1. Jolt  project: detection + capabilities + shape rest heights +
rem      kinematic + trigger pass-through
rem   2. PhysX project: the identical assertions, plus the PhysX-specific
rem      honesty checks (no trimesh advertised, mesh collider refuses loudly)
rem
rem Each run pins UEO3DE_EXPECT_BACKEND, so a project that silently resolved
rem the OTHER backend fails instead of passing assertions about a backend
rem nobody is testing ("available != active", constraint 5).
rem
rem The test drives the adapter DIRECTLY and builds its entities in-session,
rem so it needs no staged assets or AP products in either project.
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
set "PY=python"
set "AP=C:\O3DE\26.05\bin\Windows\profile\Default\AssetProcessorBatch.exe"
set "JOLT=C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"
set "PHYSX=C:\Users\jorge\O3DE\Projects\UEtoO3DETest-PhysX"

rem Steps 2 and 4 import a REAL manifest, so both projects need the fixture
rem staged and its products built. Staging is idempotent and AP is a no-op
rem when nothing changed, so this is cheap on repeat runs -- and without it
rem the PhysX project fails in wait_for_asset (correctly, but confusingly)
rem rather than testing anything about the backend.
echo === 0/4  stage the fixture into both projects ===
%PY% "%REPO%\Tests\m2\m2_stage.py" --project "%JOLT%" >nul
if errorlevel 1 goto :failed
"%AP%" --project-path="%JOLT%" --platforms=pc >nul 2>&1
if errorlevel 1 goto :failed
%PY% "%REPO%\Tests\m2\m2_stage.py" --project "%PHYSX%" >nul
if errorlevel 1 goto :failed
"%AP%" --project-path="%PHYSX%" --platforms=pc >nul 2>&1
if errorlevel 1 goto :failed

echo === 1/4  Jolt project ===
set "UEO3DE_EXPECT_BACKEND=jolt"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_backend_smoke.py" "%REPO%\Tests\m3b\results\m3b_jolt_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_jolt_result.txt
  goto :failed
)

echo === 2/4  Jolt project: real manifest import ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_manifest_import.py" "%REPO%\Tests\m3b\results\m3b_jolt_import_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_jolt_import_result.txt
  goto :failed
)

echo === 3/4  PhysX project ===
set "UEO3DE_EXPECT_BACKEND=physx"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_backend_smoke.py" "%REPO%\Tests\m3b\results\m3b_physx_result.txt" "%PHYSX%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_physx_result.txt
  goto :failed
)

rem The step that matters most: a REAL manifest through physics_build on the
rem backend that cannot bake render-mesh colliders. Before M3b's review this
rem aborted the entire import, and nothing anywhere ran it.
echo === 4/4  PhysX project: real manifest import ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_manifest_import.py" "%REPO%\Tests\m3b\results\m3b_physx_import_result.txt" "%PHYSX%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_physx_import_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
