@echo off
rem export_level.bat - export any level from any UE 5.8 project.
rem
rem Usage:
rem   export_level.bat <path\to\Project.uproject> <MapPackagePath> [OutputDir]
rem
rem Example:
rem   export_level.bat "D:\Gamedev\unreal\EasternProvince\Eastern_Province.uproject" ^
rem                    /Game/EasternProvince/Levels/L_Overview
rem
rem The map argument is a UE PACKAGE path (/Game/...), not a file path:
rem   Content\EasternProvince\Levels\L_Overview.umap
rem     -> /Game/EasternProvince/Levels/L_Overview
rem
rem Output defaults to Exports\<level name>\ in this repo.
rem Nothing is installed into the target project; the exporter is added to
rem sys.path by the script itself.
rem
rem FULL EDITOR SESSION, not a commandlet (since M7): terrain export samples
rem the Landscape's heightfield collision with line traces, and a commandlet
rem has NO physics scene -- traces return nothing, and the render-target
rem route outright crashes (measured, Tests/ue/probe_m7_*.py).
rem -ExecutePythonScript runs after the editor loads; export_level.py quits
rem the editor when done. The editor's process exit code is meaningless under
rem quit_editor, so this script asserts on the RESULT line in the result file
rem -- the same contract run_o3de_python.bat uses. CI asserts on THIS
rem script's exit code (plan constraint 10).
setlocal EnableExtensions

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage

set "UPROJECT=%~f1"
set "UEO3DE_MAP=%~2"
set "UEO3DE_OUT=%~3"
set "SCRIPT=%~dp0export_level.py"

if not exist "%UPROJECT%" (
    echo Project not found: %UPROJECT% 1>&2
    exit /b 2
)

rem Level name = last segment of the package path; the result file follows it.
for %%S in ("%UEO3DE_MAP:/=\%") do set "LEVEL_NAME=%%~nxS"
set "RESULT=%~dp0results\export_%LEVEL_NAME%_result.txt"
if exist "%RESULT%" del /q "%RESULT%"

rem GeometryScripting is required by the exporter's bake (LANE_B.md) and is
rem not enabled by default in most projects; -EnablePlugins turns it on for
rem this run only, without touching the target .uproject.
"D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe" "%UPROJECT%" -ExecutePythonScript="%SCRIPT%" -EnablePlugins=GeometryScripting -unattended -nop4 -nosplash

if not exist "%RESULT%" (
  echo RESULT FILE MISSING: %RESULT% 1>&2
  endlocal & exit /b 2
)
findstr /C:"RESULT: PASS" "%RESULT%" >nul
if errorlevel 1 (
  echo see %RESULT% 1>&2
  endlocal & exit /b 3
)
endlocal & exit /b 0

:usage
echo Usage: export_level.bat ^<Project.uproject^> ^<\/Game\/Map\/Package\/Path^> [OutputDir] 1>&2
exit /b 2
