@echo off
rem run_m8.bat - the M8 skeletal-mesh + animation acceptance run (plan v2.2, M8).
rem
rem   1. pure: skel_build (the Rz180 frame correction as a matrix identity,
rem      the component plans) - no editor, ~1 s
rem   2. offline: manifest contract, FBX intermediates (skin/curves split,
rem      mirror-Y), and the PRODUCTS - every manifest bone name in the .actor
rem      bytes (the plan's bone-count assertion; EMotionFX exposes no bus to
rem      Python in 26.05), joint tracks in the .motion files, and the
rem      skeletal Lane B rule at the position-buffer byte level
rem   3. editor: fresh import, Rz180 in the authored rotations, component
rem      wiring readback, and PLAYBACK as frame-capture pixel deltas (the
rem      waving canary's frames must differ, the bind-pose control's must
rem      not; measured edit-mode noise floor is exactly zero)
rem
rem Prereqs: Tests\m2\run_m2.bat ran (fixture exported, staged, AP-processed).
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
call "%REPO%\Tests\paths.cmd"
if errorlevel 1 exit /b 2
set "PY=python"
set "PROJECT=%O3DE_PROJECT_JOLT%"

echo === 1/3  pure skel_build tests ===
%PY% "%REPO%\Tests\m8\test_skel_build.py"
if errorlevel 1 goto :failed

echo === 2/3  offline artifacts (manifest, FBX, products) ===
%PY% "%REPO%\Tests\m8\test_m8_artifacts.py" "%PROJECT%"
if errorlevel 1 goto :failed

echo === 3/3  import + playback acceptance (editor) ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m8\m8_acceptance.py" "%REPO%\Tests\m8\results\m8_acceptance_result.txt"
if errorlevel 1 (
  echo   see Tests\m8\results\m8_acceptance_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
