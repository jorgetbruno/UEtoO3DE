@echo off
rem paths.cmd -- load Tests/paths.config into environment variables.
rem
rem `call "<repo>\Tests\paths.cmd"` from any runner .bat. After it returns,
rem UE_EDITOR / UE_PROJECT / O3DE_BIN / O3DE_PROJECT_JOLT / ... are set.
rem
rem Precedence: an ALREADY-DEFINED environment variable wins, so CI overrides a
rem single value without touching the file:
rem     set O3DE_PROJECT_JOLT=C:\ci\Jolt && Tests\m2\run_m2.bat
rem
rem `if not defined %%A` is what makes that work without delayed expansion:
rem it takes a variable NAME, so no `!...!` is needed. The obvious-looking
rem `if "!%%A!"==""` would require `setlocal EnableDelayedExpansion`, whose
rem matching `endlocal` then discards every variable this file just set --
rem which is the whole point of it.
rem
rem Fails loudly when the config is missing rather than falling back to
rem baked-in defaults. Defaults are how the previous arrangement worked, and a
rem machine without those exact drive letters got a confusing failure deep
rem inside an editor run instead of one line here.

set "UEO3DE_CONFIG=%~dp0paths.config"
if not exist "%UEO3DE_CONFIG%" (
  echo. 1>&2
  echo MISSING %UEO3DE_CONFIG% 1>&2
  echo   Copy Tests\paths.config.template to Tests\paths.config and edit it to 1>&2
  echo   point at your UE install, O3DE install and test projects. 1>&2
  echo. 1>&2
  exit /b 2
)

rem eol=# skips comments; delims== splits KEY=VALUE; tokens=1,* keeps a value
rem containing '=' intact.
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%UEO3DE_CONFIG%") do (
  if not "%%~A"=="" if not defined %%A set "%%A=%%B"
)

if not defined UE_EDITOR goto :incomplete
if not defined UE_PROJECT goto :incomplete
if not defined O3DE_BIN goto :incomplete
if not defined O3DE_PROJECT_JOLT goto :incomplete
exit /b 0

:incomplete
echo %UEO3DE_CONFIG% does not define all of UE_EDITOR, UE_PROJECT, O3DE_BIN, 1>&2
echo O3DE_PROJECT_JOLT -- compare it against Tests\paths.config.template. 1>&2
exit /b 2
