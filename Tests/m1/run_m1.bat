@echo off
rem run_m1.bat - the full M1 acceptance run (plan v2.2, M1).
rem
rem   1. export Fixture_01 from UE 5.8 headless        -> manifest.json + FBX
rem   2. pure-Python property tests for Lane A/naming  (no editor)
rem   3. validator self-test (prove it rejects bad documents)
rem   4. schema + golden-file + property assertions on the export
rem
rem CI asserts on THIS script's exit code, never on console text (plan
rem constraint 10): a suite that aborts in teardown can still print PASSED
rem lines above the failure.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
call "%REPO%\Tests\paths.cmd"
if errorlevel 1 exit /b 2
set "PY=python"

echo === 1/4  UE export ===
rem Full editor since M8: skeletal FBX export asserts in commandlets.
call "%REPO%\Tests\ue\export_fixture.bat" >nul 2>&1
if errorlevel 1 goto :failed_export

echo === 2/4  Lane A / naming property tests ===
%PY% "%REPO%\Tests\m1\test_lane_a.py"
if errorlevel 1 goto :failed

echo === 3/4  validator self-test ===
%PY% "%REPO%\Tests\m1\validate_manifest.py" --self-test
if errorlevel 1 goto :failed

echo === 4/4  M1 acceptance ===
%PY% "%REPO%\Tests\m1\test_m1_acceptance.py"
if errorlevel 1 goto :failed

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed_export
echo.
echo RESULT: FAIL (UE export step; see Tests\ue\results\export_fixture_result.txt)
endlocal & exit /b 1

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
