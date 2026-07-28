@echo off
rem run_m7.bat - the M7 terrain acceptance run (plan v2.2, M7).
rem
rem   1. offline: the terrain contract in the manifest (identity-transform
rem      entity over a '#terrain' asset, collision source none), the sample
rem      file, the FBX intermediate and the heightmap side artifact
rem   2. editor: fresh import + five spheres dropped at the exported sample
rem      points; each must NOT fall through the terrain, and >= 3 must rest
rem      on the surface within the adapter's measured contact tolerance
rem
rem M7 CANNOT run against the fixture: creating a Landscape in a scripted
rem session is impossible (the engine asserts !IsRunningCommandlet() on
rem spawn -- measured, Tests/ue/probe_m7_create.py) and the editor-UI
rem creation path cannot be automated. The suite therefore takes an export
rem directory [arg 1, default Exports\L_Showcase] that must contain a level
rem with a Landscape, and FAILS HARD when it is missing -- a terrain suite
rem that silently passes without terrain would hide exactly the regressions
rem it exists to catch.
rem
rem Prereqs: the level was exported (Tests\ue\export_level.bat), staged
rem (Tests\m2\m2_stage.py --manifest ...) and processed (AssetProcessorBatch).
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
call "%REPO%\Tests\paths.cmd"
if errorlevel 1 exit /b 2
set "PY=python"
if "%~1"=="" (
  set "EXPORT_DIR=%REPO%\Exports\L_Showcase"
) else (
  set "EXPORT_DIR=%~f1"
)

echo === 1/2  offline terrain artifacts ===
%PY% "%REPO%\Tests\m7\test_m7_artifacts.py" "%EXPORT_DIR%"
if errorlevel 1 goto :failed

echo === 2/2  sphere drop on the imported terrain (editor) ===
set "UEO3DE_M7_EXPORT=%EXPORT_DIR%"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m7\m7_acceptance.py" "%REPO%\Tests\m7\results\m7_acceptance_result.txt"
if errorlevel 1 (
  echo   see Tests\m7\results\m7_acceptance_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
