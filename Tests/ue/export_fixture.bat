@echo off
rem export_fixture.bat - export Fixture_01 in a FULL editor session.
rem
rem A commandlet cannot run this since M8: the skeletal canaries export
rem through UE's native FBX exporter, which walks render objects that do not
rem exist under -nullrhi (Assertion failed: MeshObject, measured in
rem probe_m8_skeletal.py). Same contract as export_level.bat: the editor's
rem process exit code is meaningless under quit_editor, so this script
rem asserts on the RESULT line in the result file. CI asserts on THIS
rem script's exit code (plan constraint 10).
setlocal EnableExtensions

set "RESULT=%~dp0results\export_fixture_result.txt"
if exist "%RESULT%" del /q "%RESULT%"

"D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe" "D:\Gamedev\UEtoO3DE\UE\UEtoO3DEFixture\UEtoO3DEFixture.uproject" -ExecutePythonScript="%~dp0export_fixture.py" -unattended -nop4 -nosplash

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
