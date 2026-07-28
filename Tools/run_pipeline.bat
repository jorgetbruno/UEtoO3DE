@echo off
rem run_pipeline.bat - the whole thing, headless, one exit code (plan M10).
rem
rem   UE level  ->  manifest + FBX + TGA  ->  Asset Processor  ->  O3DE prefab
rem
rem Usage:
rem   run_pipeline.bat <MapPath> <ExportDir> [PrefabName] [Project] [Backend]
rem
rem   run_pipeline.bat /Game/Maps/Fixture_01 D:\Exports\Fixture_01
rem   run_pipeline.bat /Game/Maps/L_Showcase D:\Exports\Showcase Showcase ^
rem                    C:\Users\me\O3DE\Projects\MyProject physx
rem
rem This is the CI mode the plan asks for, and it is also the honest answer to
rem "does the tool work end to end" -- every acceptance suite in this repo
rem tests one milestone's slice, and only this runs the path a user's level
rem actually takes.
rem
rem Exit code is the contract (constraint 10): 0 only if the export wrote a
rem RESULT: PASS, the Asset Processor succeeded, and the import finished with
rem no ERROR-severity records. NO step is allowed to be advisory.
rem
rem Paths must not contain spaces: the editor tokenizes --runpythonargs on
rem whitespace and a quoted value cannot survive it.
setlocal EnableExtensions

for %%R in ("%~dp0..") do set "REPO=%%~fR"
set "UE=D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"
set "UPROJECT=%REPO%\UE\UEtoO3DEFixture\UEtoO3DEFixture.uproject"
set "O3DE_BIN=C:\O3DE\26.05\bin\Windows\profile\Default"

rem Forward slashes, and not as a style preference. When -ExecutePythonScript
rem carries ARGUMENTS, UE unescapes backslashes in the value: this repo's own
rem path, D:\Gamedev\UEtoO3DE\Tools\ue_export.py, arrived at the interpreter as
rem D:\GamedevtoO3DE\Tools_export.py -- "\U" and "\ue" consumed as escape
rem sequences. It fails as "Could not load Python file" naming a path nobody
rem typed. The no-argument form (see export_fixture.bat) is unaffected, which
rem is why this only shows up here.
set "REPO_FS=%REPO:\=/%"

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
set "MAP=%~1"
set "OUTDIR=%~2"
set "PREFAB=%~3"
set "PROJECT=%~4"
set "BACKEND=%~5"
if "%PROJECT%"=="" set "PROJECT=C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"

set "OUTDIR_FS=%OUTDIR:\=/%"
set "EXPORT_RESULT=%OUTDIR%\export_result.txt"
set "IMPORT_REPORT=%OUTDIR%\import_report.txt"

echo === 1/3  export %MAP% from UE ===
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
if exist "%EXPORT_RESULT%" del /q "%EXPORT_RESULT%"
"%UE%" "%UPROJECT%" -ExecutePythonScript="%REPO_FS%/Tools/ue_export.py --map=%MAP% --out=%OUTDIR_FS% --result=%OUTDIR_FS%/export_result.txt" -unattended -nop4 -nosplash
rem The editor's exit code is meaningless under quit_editor; the RESULT line
rem is the verdict.
if not exist "%EXPORT_RESULT%" (
  echo   export produced no result file 1>&2
  goto :failed
)
findstr /C:"RESULT: PASS" "%EXPORT_RESULT%" >nul
if errorlevel 1 (
  echo   export failed; see %EXPORT_RESULT% 1>&2
  goto :failed
)

echo === 2/3  build assets (Asset Processor) ===
rem Every product the import references must exist before the import asks the
rem catalogue for it. import_level also waits per asset, but a cold cache
rem without this step turns a 3-minute run into a timeout.
"%O3DE_BIN%\AssetProcessorBatch.exe" --project-path="%PROJECT%" --platforms=pc
if %ERRORLEVEL% NEQ 0 (
  echo   Asset Processor failed with exit code %ERRORLEVEL% 1>&2
  goto :failed
)

echo === 3/3  import into %PROJECT% ===
set "ARGS=--manifest=%OUTDIR%\manifest.json --report=%IMPORT_REPORT%"
if not "%PREFAB%"=="" set "ARGS=%ARGS% --prefab=%PREFAB%"
if not "%BACKEND%"=="" set "ARGS=%ARGS% --backend=%BACKEND%"
rem Delete the report first: without this, a run that dies before writing one
rem leaves the PREVIOUS run's report on disk, and the only artefact a caller
rem inspects afterwards describes an import that did not happen.
if exist "%IMPORT_REPORT%" del /q "%IMPORT_REPORT%"
"%O3DE_BIN%\Editor.exe" --project-path="%PROJECT%" -BatchMode -autotest_mode --runpython "%REPO%\Tools\o3de_import.py" --runpythonargs "%ARGS%"
rem NEQ 0, not `errorlevel 1`. `if errorlevel N` is a signed >= test, so it is
rem FALSE for every negative exit code -- which is exactly what a crashed
rem editor returns (access violation -1073741819, heap corruption
rem -1073740940, stack overrun -1073740791). A crash would have passed as
rem success, and the pipeline's whole purpose is that it cannot.
if %ERRORLEVEL% NEQ 0 (
  echo   import failed with exit code %ERRORLEVEL%; see %IMPORT_REPORT% 1>&2
  goto :failed
)
rem An exit code of 0 is necessary but not sufficient: assert the artefact.
if not exist "%IMPORT_REPORT%" (
  echo   import exited 0 but wrote no report -- treating as failure 1>&2
  goto :failed
)

echo.
echo RESULT: PASS
echo   manifest: %OUTDIR%\manifest.json
echo   report:   %IMPORT_REPORT%
endlocal & exit /b 0

:usage
echo Usage: run_pipeline.bat ^<MapPath^> ^<ExportDir^> [PrefabName] [Project] [Backend] 1>&2
endlocal & exit /b 2

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
