@echo off
rem run_m11.bat - the M11 acceptance run (plan v2.2, M11: hardening + docs).
rem
rem   0  docs   every warning code documented with its severity, both
rem             directions, no phantom rows -- enforced, not promised
rem   1  real   one REAL level ported end to end, with the figures the plan
rem             asks to have recorded
rem
rem Step 1 needs an EXPORTED REAL LEVEL, staged and AP-processed (default
rem Exports\L_Showcase, override with UEO3DE_EXPORT). Like run_m7.bat, this
rem fails hard rather than skipping when that content is absent: a hardening
rem milestone that quietly tests nothing is worse than one that fails.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
set "PY=python"
set "JOLT=C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"
if "%UEO3DE_EXPORT%"=="" set "UEO3DE_EXPORT=%REPO%\Exports\L_Showcase"

echo === 0/1  docs: the warning catalogues and MAPPING.md agree ===
%PY% "%REPO%\Tests\m11\test_docs.py"
if errorlevel 1 goto :failed

echo === 1/1  a real level, end to end, with figures ===
if not exist "%UEO3DE_EXPORT%\manifest.json" (
  echo   no manifest at %UEO3DE_EXPORT% 1>&2
  echo   export a real level first ^(Tests\ue\export_level.bat^), then stage it: 1>&2
  echo     python Tests\m2\m2_stage.py --project "%JOLT%" --manifest "%UEO3DE_EXPORT%\manifest.json" --source-assets "%UEO3DE_EXPORT%\Assets" 1>&2
  goto :failed
)
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m11\m11_realworld.py" "%REPO%\Tests\m11\results\m11_realworld_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m11\results\m11_realworld_result.txt
  goto :failed
)

echo.
echo RESULT: PASS
echo   figures: Tests\m11\results\figures.md
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
