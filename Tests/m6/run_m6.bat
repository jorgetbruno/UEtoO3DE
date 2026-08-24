@echo off
rem run_m6.bat - the full M6 acceptance run (plan v2.2, M6: environment).
rem
rem   1. offline: the sky/fog/post-process mapping rules -- what is authored,
rem      what is deliberately NOT authored, and every approximation reported
rem   2. editor: the environment components in the SAVED prefab, including
rem      "exactly one Physical Sky" (two fight over the same sky) and the
rem      enable flags that decide whether a post-process component does
rem      anything at all
rem
rem Prereqs: the M2 chain (export, stage, AP, import) has run - run_m2.bat.
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
call "%REPO%\Tests\paths.cmd"
if errorlevel 1 exit /b 2
set "PY=python"

echo === 1/2  offline environment mapping rules ===
%PY% "%REPO%\Tests\m6\test_env_build.py"
if errorlevel 1 goto :failed

echo === 2/3  environment in the saved prefab (editor) ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m6\m6_acceptance.py" "%REPO%\Tests\m6\results\m6_acceptance_result.txt"
if errorlevel 1 (
  echo   see Tests\m6\results\m6_acceptance_result.txt
  goto :failed
)

echo === 3/3  the imported level RENDERS A PICTURE (editor) ===
rem The check written after a level imported pure white while every
rem structural assertion passed -- and then sat referenced by NO runner,
rem guarding nothing, until an external review noticed. Control first: an
rem all-white capture and a capture that never happened look identical.
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m6\m6_level_renders.py" "%REPO%\Tests\m6\results\m6_level_renders_result.txt"
if errorlevel 1 (
  echo   see Tests\m6\results\m6_level_renders_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
