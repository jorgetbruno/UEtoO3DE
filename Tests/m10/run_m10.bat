@echo off
rem run_m10.bat - the M10 acceptance run (plan v2.2, M10: UX, reporting,
rem incremental re-import).
rem
rem   0  pure     the re-import diff, offline and fast
rem   1  env      the gem is installed the way the editor needs it
rem   2  O3DE     the gem bootstrap put "Import UE Manifest..." in Tools
rem   3  O3DE     the import dialog, including the backend-dropdown rule
rem   4  UE       "Export Level to O3DE..." + the shared export path
rem   5  UE       two exports of one level, one actor moved between them
rem   6  O3DE     THE plan's test: re-import, entity count unchanged, exactly
rem                one transform differs; plus hand-edit conflicts and the
rem                control that proves preservation does something
rem   7  both     the whole pipeline headless, one exit code
rem
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
call "%REPO%\Tests\paths.cmd"
if errorlevel 1 exit /b 2
set "PY=python"
set "JOLT=%O3DE_PROJECT_JOLT%"

echo === 0/7  pure: the re-import diff ===
%PY% "%REPO%\Tests\m10\test_reimport.py"
if errorlevel 1 goto :failed

echo === 1/7  the gem is installed in the test project ===
rem Not a formality. A Python-only gem is resolvable by NAME long before the
rem editor will mount it; without the Registry entry install_gem.py writes,
rem bootstrap.py is silently never read and step 2 fails for a reason that
rem looks nothing like its cause.
%PY% "%REPO%\O3DE\Gems\UEImporter\install_gem.py" --project "%JOLT%" --check
if errorlevel 1 goto :failed

echo === 2/7  O3DE: Tools -^> Import UE Manifest... ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m10\m10_menu.py" "%REPO%\Tests\m10\results\m10_menu_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m10\results\m10_menu_result.txt
  goto :failed
)

echo === 3/7  O3DE: the import dialog ===
set "UEO3DE_EXPECT_BACKEND=jolt"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m10\m10_dialog.py" "%REPO%\Tests\m10\results\m10_dialog_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m10\results\m10_dialog_result.txt
  goto :failed
)
set "UEO3DE_EXPECT_BACKEND="

echo === 4/7  UE: Tools -^> Export Level to O3DE... ===
call "%REPO%\Tests\ue\run_ue_editor_python.bat" "%REPO%\Tests\m10\m10_ue_menu.py" "%REPO%\Tests\m10\results\m10_ue_menu_result.txt"
if errorlevel 1 (
  echo   see Tests\m10\results\m10_ue_menu_result.txt
  goto :failed
)

echo === 5/7  UE: two exports, one actor moved ===
call "%REPO%\Tests\ue\run_ue_editor_python.bat" "%REPO%\Tests\m10\m10_export_two_passes.py" "%REPO%\Tests\m10\results\m10_export_two_passes_result.txt"
if errorlevel 1 (
  echo   see Tests\m10\results\m10_export_two_passes_result.txt
  goto :failed
)

echo === 6/7  O3DE: re-import -- the plan's acceptance test ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m10\m10_acceptance.py" "%REPO%\Tests\m10\results\m10_acceptance_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m10\results\m10_acceptance_result.txt
  goto :failed
)

echo === 7/7  the whole pipeline, headless, one exit code ===
call "%REPO%\Tools\run_pipeline.bat" /Game/Maps/Fixture_02 "%REPO%\Exports\M10_pipeline" Fixture_02_M10 "%JOLT%"
if errorlevel 1 goto :failed

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
