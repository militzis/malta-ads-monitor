@echo off
REM ─────────────────────────────────────────────────────────────────
REM  fetch_recent_cy.bat
REM  Cyprus 3-hour refresh pipeline — runs every 3 hours.
REM
REM  Step 1: fetch_by_page_ids_cy.py --since TODAY
REM          Pull any new ads started today for all known YES pages.
REM  Step 2: classify_ads.py --country CY
REM          Classify newly inserted unclassified ads.
REM  Step 3: check_removed_ads_cy.py
REM          Check unchecked/new ads for Meta removal (smart — skips
REM          ads already confirmed active within 7 days).
REM  Step 4: daily_report_cy.py --hours 3
REM          Generate report covering the last 3 hours only.
REM  Step 5: make_summary_excel.py
REM          Refresh full summary workbook.
REM ─────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo [%date% %time%] Starting 3-hour Cyprus refresh...

echo.
echo -- Step 1: Fetching new ads (today only) --
python fetch_by_page_ids_cy.py --since %date:~6,4%-%date:~3,2%-%date:~0,2%
if %ERRORLEVEL% neq 0 (
    echo ERROR in Step 1 -- aborting.
    exit /b 1
)

echo.
echo -- Step 2: Classifying new ads --
python classify_ads.py --country CY
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 2 -- continuing anyway.
)

echo.
echo -- Step 3: Checking for removed ads --
python check_removed_ads_cy.py
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 3 -- continuing anyway.
)

echo.
echo -- Step 4: Generating 3-hour report --
python daily_report_cy.py --hours 3
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 4 -- continuing anyway.
)

echo.
echo -- Step 5: Refreshing summary Excel --
python make_summary_excel.py
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 5 -- summary Excel not generated.
)

echo.
echo [%date% %time%] 3-hour refresh complete.
