@echo off
rem run_m2.bat - the full M2 acceptance run (plan v2.2, M2).
rem
rem   1. UE export         manifest.json + one FBX per unique mesh GUID,
rem                        each checked against Lane A as it is written
rem   2. stage             copy the FBX into the project + write .assetinfo
rem   3. AssetProcessor    build the products
rem   4. import (editor)   wait_for_asset, create entities, save the prefab
rem   5. acceptance (editor) reopen the SAVED prefab and assert transforms
rem   6. artifacts         mirror check, dedup, sidecars, import report
rem
rem Pass --cold to delete the staged tree and its cache products first. Plan
rem constraint 10 wants the pipeline run at least nightly against a cold cache,
rem because an importer that only passes on the second run is exactly what
rem wait_for_asset exists to prevent.
rem
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
set "PY=python"
set "AP=C:\O3DE\26.05\bin\Windows\profile\Default\AssetProcessorBatch.exe"
set "PROJECT=C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"
set "COLD="
if /I "%~1"=="--cold" set "COLD=--cold"

echo === 1/6  UE export (manifest + FBX) ===
rem Full editor since M8: skeletal FBX export asserts in commandlets.
call "%REPO%\Tests\ue\export_fixture.bat" >nul 2>&1
if errorlevel 1 (
  echo   see Tests\ue\results\export_fixture_result.txt
  goto :failed
)

echo === 2/6  stage into the project %COLD% ===
%PY% "%REPO%\Tests\m2\m2_stage.py" --project "%PROJECT%" %COLD%
if errorlevel 1 goto :failed

echo === 3/6  Asset Processor ===
"%AP%" --project-path="%PROJECT%" --platforms=pc >nul 2>&1
if errorlevel 1 goto :failed

echo === 4/6  import into a prefab (editor) ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m2\m2_import.py" "%REPO%\Tests\m2\results\m2_import_result.txt" "%PROJECT%"
if errorlevel 1 (
  echo   see Tests\m2\results\m2_import_result.txt
  goto :failed
)

echo === 5/6  acceptance against the saved prefab (editor) ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m2\m2_acceptance.py" "%REPO%\Tests\m2\results\m2_acceptance_result.txt" "%PROJECT%"
if errorlevel 1 (
  echo   see Tests\m2\results\m2_acceptance_result.txt
  goto :failed
)

echo === 6/6  artifact checks ===
%PY% "%REPO%\Tests\m2\test_m2_artifacts.py" "%PROJECT%"
if errorlevel 1 goto :failed

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
