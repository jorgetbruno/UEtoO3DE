@echo off
rem run_m9.bat - the M9 stretch-features acceptance run (plan v2.2, M9).
rem
rem   1. pure: camera FOV conversion + decal projection remap (matrix
rem      identities, mutation-tested shapes) - no editor, ~1 s
rem   2. UE export of Fixture_02 (FULL editor: the spline bake runs
rem      GeometryScript on live components)
rem   3. offline artifacts: instance expansion, deformed spline FBX, LOD0
rem      flattening, decal/camera blocks, every M9 warning code
rem   4. stage + AssetProcessor
rem   5. editor: fresh import + component readbacks (decal sort key +
rem      material, converted vertical FOV, instance placement, spline model)
rem
rem Fixture_02 is built once by Tests\ue\build_fixture_02.py (idempotent,
rem FULL editor); this suite only exports and imports it, and fails hard
rem when the level is missing. CI asserts on THIS script's exit code.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
set "PY=python"
set "AP=C:\O3DE\26.05\bin\Windows\profile\Default\AssetProcessorBatch.exe"
set "PROJECT=C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"

echo === 1/5  pure conversion tests ===
%PY% "%REPO%\Tests\m9\test_m9_pure.py"
if errorlevel 1 goto :failed

echo === 2/5  UE export (Fixture_02, full editor) ===
call "%REPO%\Tests\ue\export_level.bat" "%REPO%\UE\UEtoO3DEFixture\UEtoO3DEFixture.uproject" /Game/Maps/Fixture_02 >nul 2>&1
if errorlevel 1 (
  echo   see Tests\ue\results\export_Fixture_02_result.txt
  goto :failed
)

echo === 3/5  offline artifacts ===
%PY% "%REPO%\Tests\m9\test_m9_artifacts.py"
if errorlevel 1 goto :failed

echo === 4/5  stage + Asset Processor ===
%PY% "%REPO%\Tests\m2\m2_stage.py" --manifest "%REPO%\Exports\Fixture_02\manifest.json" --source-assets "%REPO%\Exports\Fixture_02\Assets"
if errorlevel 1 goto :failed
"%AP%" --project-path="%PROJECT%" --platforms=pc >nul 2>&1
if errorlevel 1 goto :failed

echo === 5/5  import + readback acceptance (editor) ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m9\m9_acceptance.py" "%REPO%\Tests\m9\results\m9_acceptance_result.txt"
if errorlevel 1 (
  echo   see Tests\m9\results\m9_acceptance_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
