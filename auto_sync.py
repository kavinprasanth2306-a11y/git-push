#!/usr/bin/env python3
"""
Auto Git Sync Watcher (Python)
Cross-platform watcher for the repository that automatically:
1. Detects file additions, edits, renames, and deletions.
2. Checks for and pulls remote changes safely with stash & rebase.
3. Stages and commits local changes with informative summaries.
4. Pushes to the upstream tracking branch.
"""

import os
import sys
import time
import argparse
import subprocess
from datetime import datetime

DEFAULT_DEBOUNCE = 3
DEFAULT_POLL_INTERVAL = 1.0
IGNORED_DIRS = {".git", ".dart_tool", "build", ".idea", ".vscode", "__pycache__", "node_modules"}

def run_cmd(cmd, cwd=None):
    """Run a shell command and return (returncode, stdout, stderr)."""
    res = subprocess.run(
        cmd,
        shell=True,
        text=True,
        capture_output=True,
        cwd=cwd
    )
    return res.returncode, res.stdout.strip(), res.stderr.strip()

def get_repo_root():
    """Retrieve top-level directory of current git repo."""
    code, out, _ = run_cmd("git rev-parse --show-toplevel")
    if code != 0 or not out:
        print("[Error] Not inside a git repository.")
        sys.exit(1)
    return os.path.abspath(out)

def get_current_branch():
    """Get active git branch name."""
    code, out, _ = run_cmd("git branch --show-current")
    if code == 0 and out:
        return out
    code, out, _ = run_cmd("git rev-parse --abbrev-ref HEAD")
    return out if (code == 0 and out) else "main"

def get_git_status():
    """Return porcelain status lines."""
    code, out, _ = run_cmd("git status --porcelain")
    return out if code == 0 else ""

def sync_git(remote="origin", branch=None):
    """Perform the pull -> stash pop -> commit -> push workflow safely."""
    if not branch:
        branch = get_current_branch()

    status = get_git_status()
    if not status:
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now}] Changes detected! Starting synchronization...")

    # Stash uncommitted changes safely before pulling upstream
    stash_tag = f"auto-sync-{int(time.time())}"
    print(f"-> Pulling upstream changes (git pull --rebase {remote} {branch})...")
    run_cmd(f'git stash push -u -m "{stash_tag}"')

    _, stash_list, _ = run_cmd("git stash list")
    stashed = stash_tag in stash_list

    pull_code, pull_out, pull_err = run_cmd(f"git pull --rebase {remote} {branch}")
    if pull_code != 0:
        print(f"[Warning] Pull failed or rebase collision: {pull_err or pull_out}")
        if stashed:
            print("-> Restoring stashed changes...")
            run_cmd("git stash pop")
        return False
    else:
        print("-> Upstream changes synchronized successfully.")

    if stashed:
        pop_code, pop_out, pop_err = run_cmd("git stash pop")
        if pop_code != 0:
            print(f"[Warning] Merge conflict during stash pop: {pop_err or pop_out}")
            print("-> Please resolve conflicts manually before auto-sync resumes.")
            return False

    # Check status again after pull and pop
    status_after = get_git_status()
    if not status_after:
        print("-> Clean working tree after pull. Nothing new to commit.")
        return True

    # Stage all changes
    print("-> Staging files (git add -A)...")
    run_cmd("git add -A")

    _, diff_summary, _ = run_cmd("git diff --cached --name-status")
    diff_lines = [line.strip() for line in diff_summary.splitlines() if line.strip()]
    summary_text = ", ".join(diff_lines)
    if len(summary_text) > 120:
        summary_text = summary_text[:117] + "..."

    commit_msg = f"auto-sync: {now} - {summary_text}"
    print(f"-> Committing: {commit_msg}")
    commit_code, _, commit_err = run_cmd(f'git commit -m "{commit_msg}"')
    if commit_code != 0:
        print(f"[Warning] Commit failed: {commit_err}")
        return False

    # Push to remote
    print(f"-> Pushing to {remote}/{branch}...")
    push_code, push_out, push_err = run_cmd(f"git push {remote} {branch}")
    if push_code == 0:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Pushed successfully to {remote}/{branch}!\n")
        return True
    else:
        print(f"[Warning] Push failed: {push_err or push_out}")
        return False

def scan_files(root):
    """Scan mtime of files, skipping ignored directories."""
    state = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            path = os.path.join(base, f)
            try:
                state[path] = os.path.getmtime(path)
            except OSError:
                pass
    return state

def parse_args():
    parser = argparse.ArgumentParser(description="Auto Git Sync Watcher")
    parser.add_argument(
        "-d", "--debounce",
        type=float,
        default=DEFAULT_DEBOUNCE,
        help=f"Debounce delay in seconds (default: {DEFAULT_DEBOUNCE})"
    )
    parser.add_argument(
        "-r", "--remote",
        type=str,
        default="origin",
        help="Git remote name (default: origin)"
    )
    parser.add_argument(
        "-b", "--branch",
        type=str,
        default=None,
        help="Branch to sync (default: active branch)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run sync once and exit (no daemon loop)"
    )
    return parser.parse_args()

def main():
    args = parse_args()
    repo_root = get_repo_root()
    os.chdir(repo_root)

    active_branch = args.branch or get_current_branch()

    print("=" * 60)
    print(" Auto Git Sync Watcher (Python)")
    print(f" Repository : {repo_root}")
    print(f" Remote     : {args.remote}")
    print(f" Branch     : {active_branch}")
    print(f" Debounce   : {args.debounce} seconds")
    if args.once:
        print(" Mode       : Single-run (--once)")
    else:
        print(" Mode       : Continuous Watcher (Ctrl+C to stop)")
    print("=" * 60)

    # Initial sync check on startup
    sync_git(remote=args.remote, branch=active_branch)

    if args.once:
        return

    last_state = scan_files(repo_root)
    pending_sync = False
    last_change_time = 0

    try:
        while True:
            time.sleep(DEFAULT_POLL_INTERVAL)

            # Check if untracked or modified files exist via quick filesystem mtime check
            current_state = scan_files(repo_root)
            if current_state != last_state:
                pending_sync = True
                last_change_time = time.time()
                last_state = current_state

            # Also check git status directly if pending, or debounce trigger reached
            if pending_sync and (time.time() - last_change_time >= args.debounce):
                pending_sync = False
                sync_git(remote=args.remote, branch=active_branch)
                last_state = scan_files(repo_root)

    except KeyboardInterrupt:
        print("\n[Auto Git Sync] Watcher stopped.")

if __name__ == "__main__":
    main()

