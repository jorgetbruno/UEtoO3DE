@echo off
rem run_all.bat - every acceptance suite, in one command, with a summary.
rem
rem WHY THIS EXISTS. "All tests pass" has meant "I remembered to run thirteen
rem runners", and a suite nobody remembers is a suite that silently rots -- the
rem same failure mode the M3 stale-binary guard exists for, one level up. This
rem runs them all, keeps going after a failure (so one broken suite does not
rem hide the state of the other twelve), and prints a table at the end.
rem
rem The exit code is the verdict: 0 only if every suite passed.
rem
rem   Tests\run_all.bat            every suite
rem   Tests\run_all.bat quick      the ones that need no editor (seconds)
rem   Tests\run_all.bat m3 m3b     just those
rem
rem Each suite's own console output is left on screen: when something fails,
rem the detail is right there rather than in a log this script would have to
rem re-open. Suites are ordered cheapest-first, so a broken repo fails fast
rem even though the run continues.
setlocal EnableExtensions EnableDelayedExpansion

set "REPO=%~dp0.."
call "%REPO%\Tests\paths.cmd"
if errorlevel 1 exit /b 2

rem Cheapest first. `quick` stops after the offline ones.
set "QUICK=perf"
set "FULL=perf m1 m2 m3 m3b m4 m5 m6 m7 m8 m9 m10 m11"

if "%~1"=="" (
  set "SUITES=%FULL%"
) else if /I "%~1"=="quick" (
  set "SUITES=%QUICK%"
) else (
  set "SUITES=%*"
)

set "FAILED="
set "PASSED="
set "COUNT=0"

for %%S in (%SUITES%) do (
  set "RUNNER=%REPO%\Tests\%%S\run_%%S.bat"
  if not exist "!RUNNER!" (
    echo.
    echo ### %%S: no runner at !RUNNER!
    set "FAILED=!FAILED! %%S(missing)"
  ) else (
    echo.
    echo ###############################################################
    echo ### %%S
    echo ###############################################################
    call "!RUNNER!"
    if errorlevel 1 (
      set "FAILED=!FAILED! %%S"
    ) else (
      set "PASSED=!PASSED! %%S"
    )
    set /a COUNT+=1
  )
)

echo.
echo ===============================================================
echo   suites run: %COUNT%
if not "%PASSED%"=="" echo   PASS:%PASSED%
if not "%FAILED%"=="" echo   FAIL:%FAILED%
echo ===============================================================
echo.

if not "%FAILED%"=="" (
  echo RESULT: FAIL
  endlocal & exit /b 1
)
echo RESULT: PASS
endlocal & exit /b 0
