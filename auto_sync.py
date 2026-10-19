#!/usr/bin/env python3
"""
Auto Git Sync Watcher (Python)
Robust, safety-first Git auto-synchronizer.
Mitigates typical automated sync disadvantages:
1. Secret & sensitive file protection (pre-commit check + .gitignore).
2. Git lock handling (retries and graceful waits for index.lock).
3. Conflict isolation (avoids destructive rebase; restores state gracefully).
4. Configurable debounce (default 15s) to avoid micro-commit history pollution.
5. Custom commit messages or skip-ci prefix to save CI/CD build minutes.
"""

import os
import re
import sys
import time
import argparse
import subprocess
from datetime import datetime

DEFAULT_DEBOUNCE = 15  # 15s batches edits cleanly
DEFAULT_POLL_INTERVAL = 1.0
IGNORED_DIRS = {".git", ".dart_tool", "build", ".idea", ".vscode", "__pycache__", "node_modules", "dist", "out"}

# Potential secret patterns to prevent accidental leaks
SENSITIVE_PATTERNS = [
    re.compile(r"^.*\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"^.*(id_rsa|id_ed25519).*$", re.IGNORECASE),
    re.compile(r"^.*\.(pem|key|cert|pfx)$", re.IGNORECASE),
    re.compile(r"^.*(secret|credential|password).*\.(json|yaml|yml|txt|conf)$", re.IGNORECASE),
]

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

def wait_for_git_lock(repo_root, max_wait_seconds=5):
    """Wait if another Git process or IDE holds .git/index.lock."""
    lock_file = os.path.join(repo_root, ".git", "index.lock")
    waited = 0
    while os.path.exists(lock_file) and waited < max_wait_seconds:
        print("-> .git/index.lock is held by another process. Waiting...")
        time.sleep(1)
        waited += 1
    if os.path.exists(lock_file):
        print("[Warning] Git index is still locked. Skipping this sync cycle.")
        return False
    return True

def get_git_status():
    """Return porcelain status lines."""
    code, out, _ = run_cmd("git status --porcelain")
    return out if code == 0 else ""

def check_sensitive_files(status_output):
    """Detect if any staged or untracked file looks like a secret/credential."""
    blocked = []
    for line in status_output.splitlines():
        line = line.strip()
        if not line:
            continue
        # Status format: ' M file' or '?? file'
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            filepath = parts[1].strip('"')
            filename = os.path.basename(filepath)
            for pattern in SENSITIVE_PATTERNS:
                if pattern.match(filename):
                    blocked.append(filepath)
                    break
    return blocked

def sync_git(remote="origin", branch=None, skip_ci=True, custom_msg=""):
    """Perform safe pull -> commit -> push sequence with checks."""
    repo_root = get_repo_root()
    if not wait_for_git_lock(repo_root):
        return False

    if not branch:
        branch = get_current_branch()

    status = get_git_status()
    if not status:
        return False

    # Check for secrets before staging
    blocked = check_sensitive_files(status)
    if blocked:
        print(f"\n[Security Alert] Found potentially sensitive files in changes:")
        for b in blocked:
            print(f"  ! {b}")
        print("-> Sync aborted to prevent secret leakage. Add them to .gitignore if intended.")
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now}] Sync triggered! Verifying remote updates...")

    # Stash uncommitted changes safely before pulling upstream
    stash_tag = f"auto-sync-{int(time.time())}"
    run_cmd(f'git stash push -u -m "{stash_tag}"')

    _, stash_list, _ = run_cmd("git stash list")
    stashed = stash_tag in stash_list

    # Pull latest changes from remote
    pull_code, pull_out, pull_err = run_cmd(f"git pull --rebase {remote} {branch}")
    if pull_code != 0:
        print(f"[Warning] Pull failed or rebase conflict with remote: {pull_err or pull_out}")
        if stashed:
            print("-> Restoring local changes...")
            run_cmd("git stash pop")
        print("-> Manual conflict resolution required.")
        return False

    # Pop stashed changes back
    if stashed:
        pop_code, pop_out, pop_err = run_cmd("git stash pop")
        if pop_code != 0:
            print(f"[Warning] Conflict while re-applying local edits: {pop_err or pop_out}")
            print("-> Please resolve merge conflicts before auto-sync resumes.")
            return False

    # Re-check status after pull/pop
    status_after = get_git_status()
    if not status_after:
        print("-> Clean working tree after pull. Nothing new to commit.")
        return True

    # Secondary secret check
    blocked_after = check_sensitive_files(status_after)
    if blocked_after:
        print(f"[Security Alert] Potentially sensitive files detected: {blocked_after}. Aborting commit.")
        return False

    # Stage files
    print("-> Staging changes (git add -A)...")
    run_cmd("git add -A")

    _, diff_summary, _ = run_cmd("git diff --cached --name-status")
    diff_lines = [line.strip() for line in diff_summary.splitlines() if line.strip()]
    summary_text = ", ".join(diff_lines)
    if len(summary_text) > 100:
        summary_text = summary_text[:97] + "..."

    ci_flag = "[skip ci] " if skip_ci else ""
    if custom_msg:
        commit_msg = f"{ci_flag}{custom_msg} ({now})"
    else:
        commit_msg = f"{ci_flag}auto-sync: {now} - {summary_text}"

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
    parser = argparse.ArgumentParser(description="Auto Git Sync Watcher (Enhanced with safety guards)")
    parser.add_argument(
        "-d", "--debounce",
        type=float,
        default=DEFAULT_DEBOUNCE,
        help=f"Debounce delay in seconds (default: {DEFAULT_DEBOUNCE}s to batch changes)"
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
        help="Branch to sync (e.g. your personal backup branch: backup/jagadesh)"
    )
    parser.add_argument(
        "-m", "--message",
        type=str,
        default="",
        help="Custom commit message prefix"
    )
    parser.add_argument(
        "--no-skip-ci",
        action="store_true",
        help="Do NOT prefix commits with '[skip ci]' (default prefixes [skip ci] to save build quota)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run sync once and exit"
    )
    return parser.parse_args()

def main():
    args = parse_args()
    repo_root = get_repo_root()
    os.chdir(repo_root)

    active_branch = args.branch or get_current_branch()
    skip_ci = not args.no_skip_ci

    print("=" * 65)
    print(" Auto Git Sync Watcher (Protected Mode)")
    print(f" Repository : {repo_root}")
    print(f" Remote     : {args.remote}")
    print(f" Branch     : {active_branch}")
    print(f" Debounce   : {args.debounce}s (prevents commit spam)")
    print(f" Skip CI    : {'Enabled [skip ci]' if skip_ci else 'Disabled'}")
    if args.once:
        print(" Mode       : Single-run (--once)")
    else:
        print(" Mode       : Continuous Watcher (Ctrl+C to stop)")
    print("=" * 65)

    # Initial sync check on startup
    sync_git(remote=args.remote, branch=active_branch, skip_ci=skip_ci, custom_msg=args.message)

    if args.once:
        return

    last_state = scan_files(repo_root)
    pending_sync = False
    last_change_time = 0

    try:
        while True:
            time.sleep(DEFAULT_POLL_INTERVAL)

            current_state = scan_files(repo_root)
            if current_state != last_state:
                pending_sync = True
                last_change_time = time.time()
                last_state = current_state

            if pending_sync and (time.time() - last_change_time >= args.debounce):
                pending_sync = False
                sync_git(remote=args.remote, branch=active_branch, skip_ci=skip_ci, custom_msg=args.message)
                last_state = scan_files(repo_root)

    except KeyboardInterrupt:
        print("\n[Auto Git Sync] Watcher stopped.")

if __name__ == "__main__":
    main()
