@echo off
REM ─────────────────────────────────────────────────────────────────
REM  fetch_recent_mt.bat
REM  Malta 3-hour refresh pipeline — runs every 3 hours.
REM
REM  Step 1: check_all_candidates_mt.py  (incremental)
REM          Pull new Malta ads since last fetch.
REM  Step 2: check_removed_ads_mt.py
REM          Check unchecked/new ads for Meta removal.
REM  Step 3: make_summary_excel.py
REM          Refresh full summary workbook (CY + MT).
REM ─────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo [%date% %time%] Starting 3-hour Malta refresh...

echo.
echo -- Step 1: Fetching new Malta ads (incremental) --
python check_all_candidates_mt.py
if %ERRORLEVEL% neq 0 (
    echo ERROR in Step 1 -- aborting.
    exit /b 1
)

echo.
echo -- Step 2: Classifying new ads --
python classify_ads.py --country MT
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 2 -- continuing anyway.
)

echo -- Step 3: Checking for removed ads --
python check_removed_ads_mt.py
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 2 -- continuing anyway.
)

echo.
echo -- Step 4: Refreshing summary Excel --
python make_summary_excel.py
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 3 -- summary Excel not generated.
)

echo.
echo [%date% %time%] Malta 3-hour refresh complete.
