@echo off
rem run_perf.bat - the settle / collider-bake guard.
rem
rem   0  unit    the bake detector, the settle constant and its override, and
rem              the prefab content comparator -- pure Python, no editor
rem   1  live    a real level imported and CHECKED: every mesh collider that
rem              was authored must have reached the prefab with baked geometry
rem
rem Why step 1 exists at all. A collider whose bake had not finished when the
rem prefab was serialized is written out fully configured with NO geometry: it
rem collides with nothing, the file saves cleanly, and the importer's own
rem mesh_colliders counter still reports it as authored. Measured on
rem L_Showcase: with the settle removed, 15 of 2501 vanished exactly this way
rem and every existing suite stayed green. Nothing but reading the saved bytes
rem can see it.
rem
rem Step 1 needs an EXPORTED REAL LEVEL, staged and AP-processed (default
rem Exports\L_Showcase, override with UEO3DE_EXPORT). It fails hard rather
rem than skipping when that content is absent, for the same reason run_m11
rem does: a guard that quietly tests nothing is worse than one that fails.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
call "%REPO%\Tests\paths.cmd"
if errorlevel 1 exit /b 2
set "PY=python"
set "JOLT=%O3DE_PROJECT_JOLT%"
if "%UEO3DE_EXPORT%"=="" set "UEO3DE_EXPORT=%REPO%\Exports\L_Showcase"

echo === 0/1  unit: bake detector, settle constant, prefab comparator ===
%PY% "%REPO%\Tests\perf\test_settle.py"
if %ERRORLEVEL% NEQ 0 goto :failed

echo === 1/1  live: a real level, every authored bake accounted for ===
if not exist "%UEO3DE_EXPORT%\manifest.json" (
  echo   no manifest at %UEO3DE_EXPORT% 1>&2
  echo   export a real level first ^(Tests\ue\export_level.bat^), then stage it: 1>&2
  echo     python Tests\m2\m2_stage.py --project "%JOLT%" --manifest "%UEO3DE_EXPORT%\manifest.json" --source-assets "%UEO3DE_EXPORT%\Assets" 1>&2
  goto :failed
)
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\perf\perf_bakes.py" "%REPO%\Tests\perf\results\perf_bakes_result.txt" "%JOLT%"
if %ERRORLEVEL% NEQ 0 (
  echo   see Tests\perf\results\perf_bakes_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
