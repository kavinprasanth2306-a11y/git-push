<#
.SYNOPSIS
    Auto Git Sync: Watches repository files for changes, pulls remote updates first, commits and pushes changes.
.DESCRIPTION
    Protected auto-sync script with guardrails:
      - Checks and respects .git/index.lock from concurrent Git processes
      - Inspects uncommitted files for accidental secrets (.env, keys)
      - Appends [skip ci] to prevent exhausting GitHub Actions quotas
      - Increased default debounce to 15s to prevent commit flood
      - Supports targeting personal backup branches
#>

[CmdletBinding()]
param(
    [int]$DebounceSeconds = 15,
    [string]$Remote = "origin",
    [string]$Branch = "",
    [string]$CommitMessage = "",
    [switch]$SkipCI = $true,
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

function Test-GitLock {
    $lockPath = Join-Path $repoRoot ".git\index.lock"
    $waited = 0
    while ((Test-Path $lockPath) -and ($waited -lt 5)) {
        Write-Host "-> .git/index.lock is held by another process. Waiting..." -ForegroundColor Yellow
        Start-Sleep -Seconds 1
        $waited++
    }
    return (-not (Test-Path $lockPath))
}

function Test-SensitiveFiles {
    param([string[]]$StatusLines)
    $sensitivePatterns = @(
        "^\.env(\..+)?$",
        "(id_rsa|id_ed25519)",
        "\.(pem|key|cert|pfx)$",
        "(secret|credential|password).*\.(json|yaml|yml|txt|conf)$"
    )

    $blocked = @()
    foreach ($line in $StatusLines) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        $parts = $trimmed -split '\s+', 2
        if ($parts.Length -eq 2) {
            $fileName = [System.IO.Path]::GetFileName($parts[1].Trim('"'))
            foreach ($pat in $sensitivePatterns) {
                if ($fileName -match $pat) {
                    $blocked += $parts[1]
                    break
                }
            }
        }
    }
    return $blocked
}

function Sync-GitChanges {
    if (-not (Test-GitLock)) {
        Write-Warning "Git index is currently locked. Skipping this sync cycle."
        return
    }

    $activeBranch = Get-GitBranch
    if (-not $activeBranch) {
        Write-Warning "Cannot determine git branch. Skipping sync."
        return
    }

    # Check uncommitted status
    $statusLines = @(git status --porcelain 2>$null)
    if (-not $statusLines -or $statusLines.Length -eq 0 -or ($statusLines.Length -eq 1 -and [string]::IsNullOrWhiteSpace($statusLines[0]))) {
        return
    }

    # Safeguard: prevent accidental secret leaks
    $secretsFound = Test-SensitiveFiles -StatusLines $statusLines
    if ($secretsFound.Count -gt 0) {
        Write-Host "`n[Security Alert] Potentially sensitive files detected:" -ForegroundColor Red
        foreach ($sec in $secretsFound) {
            Write-Host "  ! $sec" -ForegroundColor Red
        }
        Write-Warning "Auto-sync aborted to avoid secret leakage. Please add these files to .gitignore."
        return
    }

    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $epoch = [DateTimeOffset]::Now.ToUnixTimeSeconds()
    $stashTag = "auto-sync-$epoch"

    Write-Host "`n[$timestamp] Sync triggered! Checking remote updates..." -ForegroundColor Cyan

    # Step 1: Stash & Pull Rebase
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
    }

    if ($needsPop) {
        $popOutput = git stash pop 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Stash pop encountered conflict: $popOutput"
            return
        }
    }

    # Re-check status after pull/pop
    $statusAfter = @(git status --porcelain 2>$null)
    if (-not $statusAfter -or ($statusAfter.Length -eq 1 -and [string]::IsNullOrWhiteSpace($statusAfter[0]))) {
        Write-Host "-> Working tree clean after pull. Nothing new to commit." -ForegroundColor Green
        return
    }

    # Step 2: Stage all changes
    Write-Host "-> Staging changes (git add -A)..." -ForegroundColor Yellow
    git add -A

    # Step 3: Commit
    $changedSummary = (git diff --cached --name-status) -join ", "
    if ($changedSummary.Length -gt 100) {
        $changedSummary = $changedSummary.Substring(0, 97) + "..."
    }

    $ciPrefix = if ($SkipCI) { "[skip ci] " } else { "" }
    if ($CommitMessage) {
        $fullCommitMsg = "$ciPrefix$CommitMessage ($timestamp)"
    } else {
        $fullCommitMsg = "$ciPrefix" + "auto-sync: $timestamp - $changedSummary"
    }

    Write-Host "-> Committing: '$fullCommitMsg'..." -ForegroundColor Yellow
    git commit -m $fullCommitMsg

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
Write-Host " Auto Git Sync Watcher Active (Protected Mode)" -ForegroundColor Cyan
Write-Host " Repository : $repoRoot" -ForegroundColor White
Write-Host " Branch     : $currentBranchName" -ForegroundColor White
Write-Host " Remote     : $Remote" -ForegroundColor White
Write-Host " Debounce   : $DebounceSeconds second(s) (prevents commit spam)" -ForegroundColor White
Write-Host " Skip CI    : $(if ($SkipCI) { 'Enabled [skip ci]' } else { 'Disabled' })" -ForegroundColor White
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

# Filter out directories
$ignoredDirs = @(".git", ".dart_tool", "build", ".idea", ".vscode", "node_modules", "dist", "out")

$global:hasChanges = $false
$global:lastChangeTime = [DateTime]::MinValue

$changeAction = {
    param($sender, $eventArgs)
    $path = $eventArgs.FullPath
    
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
