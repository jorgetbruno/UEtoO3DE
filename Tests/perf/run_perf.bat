@echo off
rem run_perf.bat - the settle / collider-bake guard.
rem
rem   0  unit    the bake detector, the settle constant and its override, the
rem              prefab content comparator, and WHO scales colliders (the
rem              scale-squaring guard) -- pure Python, no editor
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
%PY% "%REPO%\Tests\perf\test_convex.py"
if %ERRORLEVEL% NEQ 0 goto :failed
%PY% "%REPO%\Tests\perf\test_pxmesh.py"
if %ERRORLEVEL% NEQ 0 goto :failed
%PY% "%REPO%\Tests\perf\test_scale.py"
if %ERRORLEVEL% NEQ 0 goto :failed
%PY% "%REPO%\Tests\perf\test_install_gem.py"
if %ERRORLEVEL% NEQ 0 goto :failed
%PY% "%REPO%\Tests\perf\test_chunk_guard.py"
if %ERRORLEVEL% NEQ 0 goto :failed
%PY% "%REPO%\Tests\perf\test_gltf.py"
if %ERRORLEVEL% NEQ 0 goto :failed
%PY% "%REPO%\Tests\perf\test_glb_export.py"
if %ERRORLEVEL% NEQ 0 goto :failed
%PY% "%REPO%\Tests\perf\test_builder_present.py"
if %ERRORLEVEL% NEQ 0 goto :failed
%PY% "%REPO%\Tests\perf\test_frame_stats.py"
if %ERRORLEVEL% NEQ 0 goto :failed

echo === 1/1  live: a real level, every authored bake accounted for ===
if not exist "%UEO3DE_EXPORT%\manifest.json" (
  echo   no manifest at %UEO3DE_EXPORT% 1>&2
  echo   export a real level first ^(Tests\ue\export_level.bat^), then stage it: 1>&2
  echo     python Tests\m2\m2_stage.py --project "%JOLT%" --manifest "%UEO3DE_EXPORT%\manifest.json" --source-assets "%UEO3DE_EXPORT%\Assets" 1>&2
  goto :failed
)
rem The BAKE path is what this step guards, and both backends now prefer a
rem cooked asset wherever the sidecar asked for one -- so the level is staged
rem with cooking switched off, or the import would take the asset route and
rem the settle assertions would pass by testing nothing.
set "UEO3DE_JOLT_COOK=0"
set "UEO3DE_PHYSX_COOK=0"
%PY% "%REPO%\Tests\m2\m2_stage.py" --project "%JOLT%" --manifest "%UEO3DE_EXPORT%\manifest.json" --source-assets "%UEO3DE_EXPORT%\Assets" >nul
if %ERRORLEVEL% NEQ 0 (
  echo   staging %UEO3DE_EXPORT% into %JOLT% failed 1>&2
  goto :failed
)
"%O3DE_BIN%\AssetProcessorBatch.exe" --project-path="%JOLT%" --platforms=pc >nul 2>&1
if %ERRORLEVEL% NEQ 0 goto :failed
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\perf\perf_bakes.py" "%REPO%\Tests\perf\results\perf_bakes_result.txt" "%JOLT%"
set "LIVE_RC=%ERRORLEVEL%"

rem RESTORE THE STAGING BEFORE RETURNING, pass or fail.
rem
rem The staging above is SHARED STATE, not this suite's own: cooking off strips
rem the physics mesh groups from every sidecar of this level, and M7 imports
rem the SAME level, where the terrain's collision comes from a cooked product.
rem Measured, running the suites in order: M7's five terrain probes fell 500 m
rem through the world with "waiting for 0 cooked physics meshes" in its log --
rem a failure with no connection to anything M7 or the terrain code did, and
rem one that vanished when M7 ran alone. Ordering the suites around it would
rem only hide it; the leak is the bug.
rem
rem Restored on the failure path too, or one red run leaves the project broken
rem for every suite after it.
set "UEO3DE_JOLT_COOK="
set "UEO3DE_PHYSX_COOK="
%PY% "%REPO%\Tests\m2\m2_stage.py" --project "%JOLT%" --manifest "%UEO3DE_EXPORT%\manifest.json" --source-assets "%UEO3DE_EXPORT%\Assets" >nul
if %ERRORLEVEL% NEQ 0 (
  echo   WARNING: could not restore %UEO3DE_EXPORT% staging in %JOLT%; 1>&2
  echo   re-stage it before running M7 or its terrain will have no collision 1>&2
  goto :failed
)
"%O3DE_BIN%\AssetProcessorBatch.exe" --project-path="%JOLT%" --platforms=pc >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
  echo   WARNING: AssetProcessor failed while restoring the cooked products 1>&2
  goto :failed
)

if not "%LIVE_RC%"=="0" (
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
