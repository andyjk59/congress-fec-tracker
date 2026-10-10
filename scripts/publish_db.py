"""Publish one writer's tables to origin/main without clobbering the other's.

data/tracker.db is a single binary file written by two independent jobs --
the daily bills refresh (GitHub Actions) and the FEC refresh (a local
scheduled job, because it needs the gitignored raw fingerprint cache). Git
can't merge a binary file, and pushing one job's whole copy would revert the
other's newer data. So each job owns a disjoint set of tables, and publishing
means: take the *latest* origin/main database, overwrite only this owner's
tables from the local copy, commit, push. A rejected push (the other job
published in between) just re-runs against the new origin/main; the copy is a
full-table replace, so retrying is idempotent.
"""

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_REL = "data/tracker.db"

OWNED_TABLES = {
    "bills": ["bills", "bill_actions", "bill_subjects", "bill_cosponsors", "legislators", "bills_ingest_state"],
    "fec": [
        "fec_candidates", "fec_committees", "fec_ingest_state",
        "fec_committee_org_donors", "donor_retention", "fec_committee_industry_signal",
    ],
}

# Refuse to publish a table that shrank this much -- almost certainly a
# truncated or freshly-initialised local DB, not real data.
MIN_ROW_FRACTION = 0.5


def git(*args, cwd=ROOT, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def columns(conn, schema, table):
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def copy_tables(dest_path: Path, src_path: Path, tables: list[str]):
    conn = sqlite3.connect(dest_path)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (str(src_path),))
        for t in tables:
            shared = [c for c in columns(conn, "main", t) if c in columns(conn, "src", t)]
            cols = ", ".join(shared)
            old = conn.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
            new = conn.execute(f"SELECT COUNT(*) FROM src.{t}").fetchone()[0]
            if old and new < old * MIN_ROW_FRACTION:
                raise SystemExit(f"Refusing to publish: {t} would shrink from {old} to {new} rows")
            # Rewriting an identical table still changes the file's bytes and
            # would commit ~30 MB of nothing, so leave unchanged tables alone.
            differs = conn.execute(
                f"SELECT EXISTS (SELECT {cols} FROM main.{t} EXCEPT SELECT {cols} FROM src.{t}) "
                f"OR EXISTS (SELECT {cols} FROM src.{t} EXCEPT SELECT {cols} FROM main.{t})"
            ).fetchone()[0]
            if not differs:
                print(f"  {t}: unchanged ({new} rows)")
                continue
            conn.execute(f"DELETE FROM main.{t}")
            conn.execute(f"INSERT INTO main.{t} ({cols}) SELECT {cols} FROM src.{t}")
            print(f"  {t}: {old} -> {new} rows")
        conn.commit()
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()


def tidy_local_checkout():
    """Best effort: fast-forward this checkout to what was just pushed so the
    tracked database doesn't sit permanently 'modified'. Skipped if there's
    anything else to lose (other edits, unpushed commits, another branch)."""
    branch = git("rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    dirty = [line for line in git("status", "--porcelain", check=False).stdout.splitlines()
             if line.strip() and not line.endswith(DB_REL)]
    can_ff = git("merge-base", "--is-ancestor", "HEAD", "origin/main", check=False).returncode == 0
    if branch != "main" or dirty or not can_ff:
        return
    git("fetch", "origin", "main", check=False)
    git("checkout", "origin/main", "--", DB_REL, check=False)
    git("merge", "--ff-only", "origin/main", check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", choices=OWNED_TABLES, required=True)
    parser.add_argument("--message", default=None)
    parser.add_argument("--dry-run", action="store_true", help="merge and report, but don't commit or push")
    parser.add_argument("--attempts", type=int, default=4)
    args = parser.parse_args()
    message = args.message or f"chore: refresh {args.owner} data"

    snapshot = Path(tempfile.mkdtemp(prefix="publish_db_")) / "local.db"
    shutil.copy2(ROOT / DB_REL, snapshot)

    for attempt in range(1, args.attempts + 1):
        git("fetch", "origin", "main")
        worktree = Path(tempfile.mkdtemp(prefix="publish_wt_"))
        git("worktree", "add", "--detach", str(worktree), "origin/main")
        try:
            print(f"Attempt {attempt}: merging '{args.owner}' tables onto origin/main")
            copy_tables(worktree / DB_REL, snapshot, OWNED_TABLES[args.owner])

            git("add", DB_REL, cwd=worktree)
            if git("diff", "--cached", "--quiet", cwd=worktree, check=False).returncode == 0:
                print("No data changes to publish")
                return
            if args.dry_run:
                print("Dry run: would commit and push")
                return

            git("-c", "user.name=data-bot", "-c", "user.email=actions@github.com",
                "commit", "-m", message, cwd=worktree)
            push = git("push", "origin", "HEAD:main", cwd=worktree, check=False)
            if push.returncode == 0:
                print("Pushed")
                tidy_local_checkout()
                return
            print(f"Push rejected, retrying:\n{push.stderr.strip()}")
        finally:
            git("worktree", "remove", "--force", str(worktree), check=False)

    sys.exit(f"Could not publish after {args.attempts} attempts")


if __name__ == "__main__":
    main()
