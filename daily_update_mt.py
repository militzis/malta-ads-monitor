"""
daily_update_mt.py — Malta daily pipeline runner.

Runs every day to:
  1. Fetch new ads since last run (incremental)
  2. Check 2026 ads for Meta removals (Playwright)
  3. Rebuild the combined Excel
  4. Push updated DB to GitHub so Streamlit refreshes

Usage:
    python daily_update_mt.py
    python daily_update_mt.py --skip-removed   # skip removal check (faster)
    python daily_update_mt.py --skip-push      # don't push to GitHub
"""

import sys, os, subprocess, argparse
from datetime import date

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))

def run(cmd, label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    result = subprocess.run(
        [sys.executable] + cmd,
        cwd=BASE,
    )
    if result.returncode != 0:
        print(f"\n⚠️  '{label}' exited with code {result.returncode}")
        return False
    return True


def git_push():
    print(f"\n{'='*60}")
    print(f"  Pushing DB to GitHub")
    print(f"{'='*60}")
    cmds = [
        ["git", "add", "politician_ads_mt.db"],
        ["git", "commit", "-m", f"Daily update {date.today().isoformat()}"],
        ["git", "push"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode != 0:
            # "nothing to commit" is fine
            if "nothing to commit" in r.stdout or "nothing to commit" in r.stderr:
                print("  No DB changes to push.")
                return True
            print(f"  Git error: {r.stderr.strip()}")
            return False
    print("  ✅ Pushed to GitHub — Streamlit will redeploy automatically.")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-removed', action='store_true',
                        help='Skip the Playwright removal check')
    parser.add_argument('--skip-push',    action='store_true',
                        help='Skip git push to GitHub')
    args = parser.parse_args()

    print(f"\n🗳️  Malta Daily Update — {date.today().isoformat()}")

    # ── Step 1: Fetch new ads ──────────────────────────────────────────────────
    ok = run(["check_all_candidates_mt.py"], "Step 1/3 — Fetch new Malta ads (incremental)")
    if not ok:
        print("\n❌ Fetch failed — check your META_ACCESS_TOKEN in .env")
        sys.exit(1)

    # ── Step 2: Check for removals ────────────────────────────────────────────
    if not args.skip_removed:
        run(
            ["check_removed_ads_mt.py", "--since", "2026-01-01", "--concurrency", "3"],
            "Step 2/3 — Check for removed ads (Playwright)"
        )
    else:
        print("\n  Skipping removal check (--skip-removed)")

    # ── Step 3: Rebuild Excel ─────────────────────────────────────────────────
    run(["make_combined_excel_mt.py"], "Step 3/3 — Rebuild combined Excel")

    # ── Step 4: Push to GitHub ────────────────────────────────────────────────
    if not args.skip_push:
        git_push()
    else:
        print("\n  Skipping GitHub push (--skip-push)")

    print(f"\n✅ Done — {date.today().isoformat()}\n")


if __name__ == '__main__':
    main()
