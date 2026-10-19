# Auto Git Sync Watcher (Protected Mode)

Cross-platform watcher scripts that monitor your repository for changes, pull upstream updates safely (via `stash` + `git pull --rebase`), and commit/push your modifications automatically with enterprise-grade guardrails.

## Built-In Protections (Disadvantage Mitigations)

1. **Secret & Credential Leak Guard**:
   - Both scripts inspect changed files prior to staging. Any attempt to commit `.env`, private keys (`.pem`, `id_rsa`), or credential files triggers a security alert and aborts the sync.
   - Includes a default [`.gitignore`](file:///e:/projects/Apps/connected/foodwasteapp/scripts/.gitignore).
2. **Commit Flood Prevention**:
   - Default debounce is set to **15 seconds** (configurable), allowing multiple rapid edits to batch into a single clean commit.
3. **CI/CD Quota Protection**:
   - Automatically prefixes commits with `[skip ci]`, avoiding wasted GitHub Actions/CI build minutes on autosaves.
4. **Git Lock Conflict Handling**:
   - Actively checks for `.git/index.lock` and waits gracefully instead of crashing when your IDE or terminal executes git commands simultaneously.
5. **Safe Branch Targeting**:
   - Syncs to your active branch or a designated backup branch (e.g. `backup/jagadesh`) so your `main` branch remains clean for pull requests.

---

## Available Scripts

### 1. PowerShell Script (Recommended on Windows)
- **File**: [`auto_sync.ps1`](file:///e:/projects/Apps/connected/foodwasteapp/scripts/auto_sync.ps1)
- Uses Windows `System.IO.FileSystemWatcher` (event-driven, zero polling overhead).
- **Run continuously**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\auto_sync.ps1
  ```
- **Sync to an isolated backup branch**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\auto_sync.ps1 -Branch backup/jagadesh
  ```
- **Run once and exit**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\auto_sync.ps1 -Once
  ```
- **Custom options**:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\auto_sync.ps1 -DebounceSeconds 20 -Remote origin -Branch main -CommitMessage "wip: autosave"
  ```

### 2. Python Script (Cross-Platform)
- **File**: [`auto_sync.py`](file:///e:/projects/Apps/connected/foodwasteapp/scripts/auto_sync.py)
- Runs on Windows, macOS, or Linux using standard library (zero external dependencies).
- **Run continuously**:
  ```bash
  python auto_sync.py
  ```
- **Sync to a personal backup branch**:
  ```bash
  python auto_sync.py -b backup/jagadesh
  ```
- **Run once and exit**:
  ```bash
  python auto_sync.py --once
  ```
- **Custom options**:
  ```bash
  python auto_sync.py --debounce 20 --remote origin --branch main -m "wip: progress"
  ```

---

## Synchronization Workflow

```
File modified / created / deleted
       │
       ▼
Debounce timer (default: 15 seconds to batch rapid saves)
       │
       ▼
Test .git/index.lock (waits if another git command is running)
       │
       ▼
Pre-commit Secret Scan (blocks .env, .pem, credentials)
       │
       ▼
git stash (temporarily safeguard local uncommitted edits)
       │
       ▼
git pull --rebase origin <branch> (pull upstream changes safely)
       │
       ▼
git stash pop (restore local edits on top of new upstream code)
       │
       ▼
git add -A (stage additions, modifications, deletions)
       │
       ▼
git commit -m "[skip ci] auto-sync: <timestamp> - <status summary>"
       │
       ▼
git push origin <branch> (push to GitHub)
```