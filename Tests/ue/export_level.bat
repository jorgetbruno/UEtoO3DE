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

"D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "%UPROJECT%" -run=pythonscript -script="%SCRIPT%" -unattended -nop4 -nullrhi
set "EC=%ERRORLEVEL%"
endlocal & exit /b %EC%

:usage
echo Usage: export_level.bat ^<Project.uproject^> ^<\/Game\/Map\/Package\/Path^> [OutputDir] 1>&2
exit /b 2
