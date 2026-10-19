<#
.SYNOPSIS
    Auto Git Sync: Watches repository files for changes, pulls remote updates first, commits and pushes changes.
.DESCRIPTION
    Monitors the repository directory (excluding .git, build, .dart_tool).
    When file changes are detected:
      1. Waits for a debounce window (default 3 seconds) to batch multiple quick edits.
      2. Runs 'git pull --rebase' to fetch and integrate any remote changes safely without messy merge commits.
      3. Adds all modified and untracked files ('git add -A').
      4. Creates a commit with timestamp and changed file summaries.
      5. Pushes the commit to the active tracking branch on origin.
.EXAMPLE
    .\auto_sync.ps1
    .\auto_sync.ps1 -DebounceSeconds 5
#>

[CmdletBinding()]
param(
    [int]$DebounceSeconds = 3,
    [string]$Remote = "origin",
    [string]$Branch = "",
    [switch]$Once
)

$ErrorActionPreference = "Continue"

$repoRoot = (git rev-parse --show-toplevel 2>$null)
if (-not $repoRoot) {
    Write-Error "Not inside a git repository."
    exit 1
}
$repoRoot = $repoRoot.Trim().Replace('/', '\')
Set-Location $repoRoot

function Get-GitBranch {
    if ($Branch) { return $Branch }
    $currentBranch = (git branch --show-current 2>$null)
    if (-not $currentBranch) {
        $currentBranch = (git rev-parse --abbrev-ref HEAD 2>$null)
    }
    if ($currentBranch) {
        return $currentBranch.Trim()
    }
    return "main"
}

function Sync-GitChanges {
    $activeBranch = Get-GitBranch
    if (-not $activeBranch) {
        Write-Warning "Cannot determine git branch. Skipping sync."
        return
    }

    # Check if there are uncommitted changes or untracked files
    $status = git status --porcelain
    if (-not $status) {
        return
    }

    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $epoch = [DateTimeOffset]::Now.ToUnixTimeSeconds()
    $stashTag = "auto-sync-$epoch"

    Write-Host "`n[$timestamp] Changes detected! Starting sync..." -ForegroundColor Cyan

    # Step 1: Check remote and pull any incoming changes safely
    Write-Host "-> Checking remote for incoming changes (git pull --rebase $Remote $activeBranch)..." -ForegroundColor Yellow
    
    $needsPop = $false
    git stash push -u -m "$stashTag" 2>&1 | Out-Null
    $stashList = git stash list
    if ($stashList -match $stashTag) {
        $needsPop = $true
    }

    $pullOutput = git pull --rebase $Remote $activeBranch 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Pull failed or encountered conflicts: $pullOutput"
        if ($needsPop) {
            Write-Host "-> Restoring local stashed changes..." -ForegroundColor Yellow
            git stash pop 2>&1 | Out-Null
        }
        Write-Warning "Please resolve conflicts manually before sync can resume."
        return
    } else {
        Write-Host "-> Remote changes synchronized successfully." -ForegroundColor Green
    }

    if ($needsPop) {
        $popOutput = git stash pop 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Stash pop encountered conflict. Please review your files."
            return
        }
    }

    # Re-check status after pull/pop
    $statusAfter = git status --porcelain
    if (-not $statusAfter) {
        Write-Host "-> Working tree clean after pull. Nothing new to commit." -ForegroundColor Green
        return
    }

    # Step 2: Stage all changes
    Write-Host "-> Staging changes (git add -A)..." -ForegroundColor Yellow
    git add -A

    # Step 3: Commit
    $changedSummary = (git diff --cached --name-status) -join ", "
    if ($changedSummary.Length -gt 120) {
        $changedSummary = $changedSummary.Substring(0, 117) + "..."
    }
    $commitMsg = "auto-sync: $timestamp - $changedSummary"
    Write-Host "-> Committing: '$commitMsg'..." -ForegroundColor Yellow
    git commit -m $commitMsg

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Commit failed. Aborting push."
        return
    }

    # Step 4: Push to origin
    Write-Host "-> Pushing to $Remote $activeBranch..." -ForegroundColor Yellow
    $pushOutput = git push $Remote $activeBranch 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] Changes successfully pushed to $Remote/$activeBranch!`n" -ForegroundColor Green
    } else {
        Write-Warning "Push failed: $pushOutput"
    }
}

$currentBranchName = Get-GitBranch

Write-Host "==========================================================" -ForegroundColor Green
Write-Host " Auto Git Sync Watcher Active" -ForegroundColor Cyan
Write-Host " Repository : $repoRoot" -ForegroundColor White
Write-Host " Branch     : $currentBranchName" -ForegroundColor White
Write-Host " Remote     : $Remote" -ForegroundColor White
Write-Host " Debounce   : $DebounceSeconds second(s)" -ForegroundColor White
if ($Once) {
    Write-Host " Mode       : Single Run (-Once)" -ForegroundColor White
} else {
    Write-Host " Mode       : Continuous Watcher ([Ctrl+C] to stop)" -ForegroundColor White
}
Write-Host "==========================================================`n" -ForegroundColor Green

# Initial check
Sync-GitChanges

if ($Once) {
    exit 0
}

# Set up FileSystemWatcher
$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $repoRoot
$watcher.IncludeSubdirectories = $true
$watcher.EnableRaisingEvents = $false

# Filter out directories we don't want to trigger sync on
$ignoredDirs = @(".git", ".dart_tool", "build", ".idea", ".vscode", "node_modules")

$global:hasChanges = $false
$global:lastChangeTime = [DateTime]::MinValue

$changeAction = {
    param($sender, $eventArgs)
    $path = $eventArgs.FullPath
    
    # Check if path is in ignored directory
    foreach ($ignored in $ignoredDirs) {
        if ($path.Contains("\$ignored\") -or $path.EndsWith("\$ignored")) {
            return
        }
    }

    $global:hasChanges = $true
    $global:lastChangeTime = [DateTime]::Now
}

$createdEvent = Register-ObjectEvent $watcher "Created" -Action $changeAction
$changedEvent = Register-ObjectEvent $watcher "Changed" -Action $changeAction
$deletedEvent = Register-ObjectEvent $watcher "Deleted" -Action $changeAction
$renamedEvent = Register-ObjectEvent $watcher "Renamed" -Action $changeAction

$watcher.EnableRaisingEvents = $true

try {
    while ($true) {
        Start-Sleep -Milliseconds 500

        if ($global:hasChanges) {
            $elapsed = ([DateTime]::Now - $global:lastChangeTime).TotalSeconds
            if ($elapsed -ge $DebounceSeconds) {
                $global:hasChanges = $false
                Sync-GitChanges
            }
        }
    }
}
finally {
    $watcher.EnableRaisingEvents = $false
    Unregister-Event -SourceIdentifier $createdEvent.Name -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier $changedEvent.Name -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier $deletedEvent.Name -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier $renamedEvent.Name -ErrorAction SilentlyContinue
    $watcher.Dispose()
    Write-Host "`nAuto Git Sync Watcher stopped." -ForegroundColor Yellow
}
