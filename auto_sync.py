#!/usr/bin/env python3
"""
Auto Git Sync Watcher (Python)
Cross-platform watcher for the repository that automatically:
1. Detects file additions, edits, renames, and deletions.
2. Checks for and pulls any remote changes first (`git pull --rebase`).
3. Stages and commits local changes.
4. Pushes to the remote branch.
"""

import os
import sys
import time
import subprocess
from datetime import datetime

DEBOUNCE_SECONDS = 3
IGNORED_DIRS = {".git", ".dart_tool", "build", ".idea"}

def run_cmd(cmd, check=True):
    res = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()

def get_current_branch():
    code, out, _ = run_cmd("git branch --show-current")
    if code == 0 and out:
        return out
    code, out, _ = run_cmd("git rev-parse --abbrev-ref HEAD")
    return out if code == 0 else "main"

def get_repo_root():
    code, out, _ = run_cmd("git rev-parse --show-toplevel")
    if code != 0:
        print("[Error] Not inside a git repository.")
        sys.exit(1)
    return os.path.abspath(out)

def sync_git():
    branch = get_current_branch()
    code, status, _ = run_cmd("git status --porcelain")
    if not status:
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now}] Changes detected! Starting synchronization...")

    # Stash uncommitted changes safely before pulling
    print(f"-> Pulling any upstream changes (git pull --rebase origin {branch})...")
    run_cmd("git stash push -u -m 'auto-sync-temp-stash'")
    _, stash_list, _ = run_cmd("git stash list")
    needs_pop = "auto-sync-temp-stash" in stash_list

    code, pull_out, pull_err = run_cmd(f"git pull --rebase origin {branch}")
    if code != 0:
        print(f"[Warning] Pull failed or conflict detected: {pull_err or pull_out}")
        if needs_pop:
            run_cmd("git stash pop")
        return
    else:
        print("-> Upstream changes pulled successfully.")

    if needs_pop:
        code, pop_out, pop_err = run_cmd("git stash pop")
        if code != 0:
            print(f"[Warning] Conflict when restoring local changes: {pop_err or pop_out}")
            return

    # Check status again
    code, status_after, _ = run_cmd("git status --porcelain")
    if not status_after:
        print("-> Clean working tree after pull. Nothing new to commit.")
        return

    # Add and Commit
    print("-> Staging files (git add -A)...")
    run_cmd("git add -A")

    _, diff_summary, _ = run_cmd("git diff --cached --name-status")
    diff_line = ", ".join(diff_summary.splitlines())[:100]
    commit_msg = f"auto-sync: {now} - {diff_line}"
    print(f"-> Committing: {commit_msg}")
    code, _, err = run_cmd(f'git commit -m "{commit_msg}"')
    if code != 0:
        print(f"[Warning] Commit failed: {err}")
        return

    # Push
    print(f"-> Pushing to origin/{branch}...")
    code, push_out, push_err = run_cmd(f"git push origin {branch}")
    if code == 0:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Pushed successfully to origin/{branch}!\n")
    else:
        print(f"[Warning] Push failed: {push_err or push_out}")

def scan_files(root):
    state = {}
    for base, dirs, files in os.walk(root):
        # Exclude ignored dirs
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            path = os.path.join(base, f)
            try:
                state[path] = os.path.getmtime(path)
            except OSError:
                pass
    return state

def main():
    repo_root = get_repo_root()
    os.chdir(repo_root)
    branch = get_current_branch()

    print("=" * 60)
    print(" Auto Git Sync Watcher (Python)")
    print(f" Repository : {repo_root}")
    print(f" Branch     : {branch}")
    print(f" Debounce   : {DEBOUNCE_SECONDS} seconds")
    print(" Press Ctrl+C to terminate.")
    print("=" * 60)

    # Initial check
    sync_git()
    last_state = scan_files(repo_root)
    pending_sync = False
    last_change_time = 0

    try:
        while True:
            time.sleep(1)
            current_state = scan_files(repo_root)
            if current_state != last_state:
                pending_sync = True
                last_change_time = time.time()
                last_state = current_state

            if pending_sync and (time.time() - last_change_time >= DEBOUNCE_SECONDS):
                pending_sync = False
                sync_git()
                last_state = scan_files(repo_root)

    except KeyboardInterrupt:
        print("\nAuto Git Sync Watcher stopped.")

if __name__ == "__main__":
    main()
