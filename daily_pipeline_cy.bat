@echo off
REM ─────────────────────────────────────────────────────────────────
REM  daily_pipeline_cy.bat
REM  Cyprus daily monitoring pipeline — runs every morning.
REM
REM  Step 1: fetch_by_page_ids_cy.py   — refresh all ad data (~40 min)
REM  Step 2: classify_ads.py --country CY  — classify any new unclassified ads
REM  Step 3: check_removed_ads_cy.py   — detect newly removed ads
REM  Step 4: daily_report_cy.py        — generate daily Excel report
REM  Step 5: make_summary_excel.py     — full summary workbook (7 sheets)
REM ─────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo [%date% %time%] Starting daily Cyprus pipeline...

echo.
echo ── Step 1: Fetching new ads (fetch_by_page_ids_cy.py) ──
python fetch_by_page_ids_cy.py
if %ERRORLEVEL% neq 0 (
    echo ERROR in Step 1 — aborting.
    exit /b 1
)

echo.
echo ── Step 2: Classifying new/unclassified ads (classify_ads.py) ──
echo    (runs before removal check so NO ads are already filtered out)
python classify_ads.py --country CY
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 2 — continuing anyway.
)

echo.
echo ── Step 3: Checking for removed ads (check_removed_ads_cy.py) ──
echo    (active-only, re-checks every 7 days)
python check_removed_ads_cy.py
if %ERRORLEVEL% neq 0 (
    echo ERROR in Step 3 — aborting.
    exit /b 1
)

echo.
echo ── Step 4: Generating daily report ──
python daily_report_cy.py
if %ERRORLEVEL% neq 0 (
    echo ERROR in Step 4.
    exit /b 1
)

echo.
echo ── Step 5: Generating summary workbook (make_summary_excel.py) ──
python make_summary_excel.py
if %ERRORLEVEL% neq 0 (
    echo WARNING in Step 5 — summary Excel not generated.
)

echo.
echo [%date% %time%] Daily pipeline complete.
