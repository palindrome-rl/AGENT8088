# ============================================================================
# Agent8088 Installer - Windows (native PowerShell)
# ============================================================================
# Usage:
#   iex (irm https://<YOUR-URL>/install.ps1)
#
# Installs agent8088 as an isolated uv tool with a global `agent8088` command.
# Handles: uv bootstrap, Python provisioning, PortableGit install, repo clone,
# venv, editable install, PATH setup, config drop, and a setup wizard.
# ============================================================================

param(
    [switch]$SkipSetup,
    [string]$Branch = $(if ($env:AGENT8088_BRANCH) { $env:AGENT8088_BRANCH } else { "main" }),
    [string]$Agent8088Home = $(if ($env:AGENT8088_HOME) { $env:AGENT8088_HOME } else { "$env:LOCALAPPDATA\agent8088" }),
    [string]$InstallDir = ""
)

# Note: we use "Continue" (not "Stop") because native commands (uv, git, python)
# write progress/diagnostic text to stderr. With "Stop", PowerShell wraps every
# stderr line as a NativeCommandError and throws - making `uv venv`'s harmless
# "Using CPython..." banner fatal. We handle errors via explicit $LASTEXITCODE
# checks and Test-Path instead, matching the Hermes installer pattern.
$ErrorActionPreference = "Continue"

# Suppress Invoke-WebRequest's per-chunk progress bar. Windows PowerShell 5.1's
# progress UI repaints synchronously on every received byte, pegging CPU on a
# single core and throttling downloads by 10-100x.
$ProgressPreference = "SilentlyContinue"

# Force the console to UTF-8 so non-ASCII output from native commands (git box-
# drawing glyphs, etc.) renders correctly instead of as IBM437 mojibake.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {
    # Some constrained hosts disallow encoding mutation. Mojibake is cosmetic-only.
}

# ----------------------------------------------------------------------------
# 8.3 short-path normalization
# ----------------------------------------------------------------------------
# When the Windows user-profile folder contains a space (e.g. "First Last"),
# Windows generates an 8.3 short alias and may expose %TEMP%/%TMP% in that
# short form (e.g. C:\Users\FIRST~1.LAS\AppData\Local\Temp). PowerShell's
# FileSystem provider mishandles the "~1.ext" component when such a path is
# handed to a provider cmdlet (Tee-Object / Out-File), throwing "An object at
# the specified path does not exist." Expand %TEMP%/%TMP% to long form once.
function ConvertTo-LongPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }
    if ($Path -notmatch '~\d') { return $Path }
    try {
        $fso = New-Object -ComObject Scripting.FileSystemObject
        if ($fso.FolderExists($Path)) { return $fso.GetFolder($Path).Path }
        if ($fso.FileExists($Path))   { return $fso.GetFile($Path).Path }
    } catch { }
    return $Path
}

foreach ($tmpVar in @('TEMP', 'TMP')) {
    $current = [Environment]::GetEnvironmentVariable($tmpVar)
    if ($current) {
        $expanded = ConvertTo-LongPath $current
        if ($expanded -and $expanded -ne $current) {
            Set-Item -Path "Env:$tmpVar" -Value $expanded
        }
    }
}

# Guard against environment leakage when launched from another Python session.
$env:PYTHONPATH = $null
$env:PYTHONHOME = $null

# Prevent uv from discovering config files from the wrong user's home dir.
$env:UV_NO_CONFIG = "1"

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
if (-not $InstallDir) { $InstallDir = Join-Path $Agent8088Home "agent8088" }
$RepoUrl = "https://github.com/palindrome-rl/AGENT8088.git"
$PythonVersion = "3.11"
$PythonFallbackVersions = @("3.12", "3.10")
$NodeVersion = "22.11.0"
$FreshInstall = $false
$InitialSetupRan = $false
# Readiness flags set by the new stages so Verify-Install can report actual state.
$GatewayExtrasInstalled = $false
$SearchExtrasInstalled = $false
$ChromiumInstalled = $false
$NodeInstalled = $false
$WhatsAppBridgeReady = $false
$SandboxInstalled = $false
$EmbedModelReady = $false
$EmbedViaProvider = $false

# ----------------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------------
function Write-Banner {
    Write-Host ""
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "|             * Agent8088 Installer                        |" -ForegroundColor Magenta
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "|  A local AI agent by Palindrome Research Labs.          |" -ForegroundColor Magenta
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Info    { param([string]$Message) Write-Host "-> $Message" -ForegroundColor Cyan }
function Write-Success { param([string]$Message) Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Warn    { param([string]$Message) Write-Host "[!] $Message" -ForegroundColor Yellow }
function Write-Err     { param([string]$Message) Write-Host "[X] $Message" -ForegroundColor Red }

# ----------------------------------------------------------------------------
# Timeouts for the optional network stages
# ----------------------------------------------------------------------------
# Every optional stage already tolerates a *failure* (Write-Warn + return), but
# nothing protected them from a *hang*. A stalled `ollama pull`, an npm registry
# that accepts the connection and then goes quiet, or a wedged Ollama daemon left
# the installer waiting forever with no way out but Ctrl-C.
#
# Limits are deliberately moderate rather than maximally generous: every stage
# guarded here is optional and degrades to a "run this to fix it later" message,
# so the cost of cutting a slow-but-working download short is one rerun, while
# the cost of waiting too long is an installer that looks frozen. Roughly sized
# so a ~4 Mbps link finishes comfortably.
#
# Scale them all for a slow connection:
#   $env:AGENT8088_TIMEOUT_SCALE = 3; iex (irm <url>)
$TimeoutScale = 1
if ($env:AGENT8088_TIMEOUT_SCALE -match '^\d+$' -and [int]$env:AGENT8088_TIMEOUT_SCALE -ge 1) {
    $TimeoutScale = [int]$env:AGENT8088_TIMEOUT_SCALE
}

$TOllamaCheck = 15  * $TimeoutScale   # local socket call - instant unless the daemon is wedged
$TOllamaPull  = 600 * $TimeoutScale   # 274 MB embedding model
$TNpm         = 300 * $TimeoutScale   # 142 small packages, mostly round-trips
$TChromium    = 600 * $TimeoutScale   # ~150 MB browser download
$TDownload    = 180 * $TimeoutScale   # ~30 MB archives (Node, MinGit, repo ZIP)
$TPip         = 300 * $TimeoutScale   # tens of MB of wheels

# Run an external command under a wall-clock limit.
#
# PowerShell has no `timeout`, so this uses Start-Process -PassThru plus
# WaitForExit(ms). That also solves a second problem: -WorkingDirectory sets the
# child's directory without touching the caller's location, which is what the
# WhatsApp bridge stage needs (see Install-Node-Bridge).
#
# Output goes to temp files rather than the console to preserve the quiet install
# the previous `2>&1 | Out-Null` calls had. Returns a hashtable so callers can
# tell a hang from an ordinary non-zero exit:
#   @{ ExitCode = <int>; TimedOut = <bool>; Output = <string> }
# Output is only populated with -CaptureOutput, for callers that need to read what
# the command printed (e.g. `ollama list`).
#
# Built on System.Diagnostics.Process rather than Start-Process -PassThru: the
# latter does not reliably surface .ExitCode or honour WaitForExit(ms) on a
# redirected child, which made a "timeout" that never fired and an exit code that
# always read 0.
function Invoke-WithTimeout {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][int]$TimeoutSec,
        [string]$WorkingDirectory,
        [switch]$CaptureOutput
    )

    $result = @{ ExitCode = -1; TimedOut = $false; Output = "" }
    $proc = $null

    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName               = $FilePath
        $psi.UseShellExecute        = $false
        $psi.CreateNoWindow         = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }

        if ($Arguments.Count -gt 0) {
            if ($null -ne $psi.PSObject.Properties['ArgumentList']) {
                # PowerShell 7 / .NET Core: pass argv directly, no quoting needed.
                foreach ($a in $Arguments) { $psi.ArgumentList.Add($a) }
            } else {
                # Windows PowerShell 5.1 / .NET Framework has no ArgumentList, so
                # build the command line by hand. Quoting matters here: install
                # paths routinely contain spaces (C:\Users\First Last\...).
                $quoted = $Arguments | ForEach-Object {
                    if ($_ -match '[\s"]') {
                        '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
                    } else { $_ }
                }
                $psi.Arguments = ($quoted -join ' ')
            }
        }

        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        [void]$proc.Start()

        # Drain both pipes asynchronously. A child that fills its stdout buffer
        # while nobody reads it blocks forever, which would reintroduce the exact
        # hang this function exists to prevent (npm is chatty enough to hit it).
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()

        if ($proc.WaitForExit($TimeoutSec * 1000)) {
            # Second, argument-less wait: lets the async readers finish flushing
            # before the exit code is read (documented .NET requirement).
            try { $proc.WaitForExit() } catch { }
            $result.ExitCode = $proc.ExitCode
            if ($CaptureOutput) {
                try { $result.Output = $outTask.GetAwaiter().GetResult() } catch { }
            }
        } else {
            $result.TimedOut = $true
            # Kill($true) takes the whole process tree but is .NET Core only, so
            # 5.1 falls back to killing just the launched process. A stray child
            # (npm's node, say) is a better outcome than a frozen installer.
            try { $proc.Kill($true) } catch { try { $proc.Kill() } catch { } }
            try { [void]$proc.WaitForExit(5000) } catch { }
        }
        if ($null -eq $result.Output) { $result.Output = "" }
    } catch {
        $result.ExitCode = -1
        $result.Error = $_.Exception.Message
    } finally {
        if ($proc) { try { $proc.Dispose() } catch { } }
    }

    return $result
}

# Skipped-stage ledger, printed as one block at the end of the run.
#
# Warnings are emitted as each stage runs, which on a multi-minute install means
# they have scrolled well out of view by the time it finishes - the WhatsApp
# bridge failing was reported and still went unnoticed. Recording them lets the
# final summary state plainly what did not install and how to fix each one.
$SkippedStages = New-Object System.Collections.ArrayList

function Register-SkippedStage {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Reason,
        [string]$Fix = ""
    )
    # Create the list on first use rather than relying on the top-level
    # declaration alone. This installer is normally run as `iex (irm ...)`, where
    # $script: does not always resolve to the scope holding that declaration -
    # and unlike the boolean readiness flags, where `$script:X = $true` creates
    # the variable on assignment, calling .Add() on an unresolved name throws.
    if ($null -eq $script:SkippedStages) {
        $script:SkippedStages = New-Object System.Collections.ArrayList
    }
    [void]$script:SkippedStages.Add([pscustomobject]@{
        Label  = $Label
        Reason = $Reason
        Fix    = $Fix
    })
}

# Warn about an optional stage that did not complete, naming a hang as a hang.
# "timed out after 10m" and "failed" point at different fixes. -Fix is the command
# that repairs it, surfaced again in the final summary.
function Write-StageWarning {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Result,
        [Parameter(Mandatory = $true)][int]$TimeoutSec,
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][string]$Consequence,
        [string]$Fix = ""
    )
    if ($Result.TimedOut) {
        $reason = "timed out after $([int]($TimeoutSec / 60))m"
        Write-Warn "$What timed out after $([int]($TimeoutSec / 60))m - $Consequence"
        Write-Warn 'On a slow connection, rerun with: $env:AGENT8088_TIMEOUT_SCALE = 3'
    } else {
        $reason = "failed (exit $($Result.ExitCode))"
        Write-Warn "$What failed (exit $($Result.ExitCode)) - $Consequence"
    }
    Register-SkippedStage -Label $What -Reason $reason -Fix $Fix
}

# Final block: what did not install, why, and the command that fixes it. Silent
# when everything succeeded.
function Write-SkippedSummary {
    if ($null -eq $script:SkippedStages -or $script:SkippedStages.Count -eq 0) { return }
    Write-Host ""
    Write-Host "$($script:SkippedStages.Count) optional component(s) did not install:" -ForegroundColor Yellow
    foreach ($s in $script:SkippedStages) {
        Write-Host "  * " -ForegroundColor Yellow -NoNewline
        Write-Host "$($s.Label) - $($s.Reason)"
        if ($s.Fix) { Write-Host "      fix: $($s.Fix)" }
    }
    Write-Host ""
    Write-Host "  The core agent is installed and works without these."
}

function Protect-ConfigFile {
    param([string]$Path)
    $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    icacls $Path /grant:r "*$sid`:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $owner = try { (Get-Acl -LiteralPath $Path).Owner } catch { "unknown" }
        throw ("Could not secure config.txt for the current user. " +
               "Current owner: $owner. Open PowerShell as Administrator and run: " +
               "takeown.exe /F `"$Path`"; " +
               "icacls.exe `"$Path`" /grant:r `"*$sid`:(F)`"")
    }
    icacls $Path /inheritance:r | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not remove inherited config permissions: $Path" }
}

# Detect non-interactive mode (iex (irm ...))
$NonInteractive = -not [Environment]::UserInteractive

# ----------------------------------------------------------------------------
# Resolve the PowerShell host executable used to spawn child PowerShell
# processes. Must NOT hardcode `powershell` - it isn't on PATH under pwsh 7+.
# ----------------------------------------------------------------------------
function Get-PowerShellHostExe {
    try {
        $hostExe = (Get-Process -Id $PID).Path
        if ($hostExe -and (Test-Path $hostExe)) {
            $leaf = Split-Path $hostExe -Leaf
            if ($leaf -match '^(?i:powershell|pwsh)\.exe$') { return $hostExe }
        }
    } catch { }
    foreach ($candidate in @("powershell", "pwsh")) {
        $cmd = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($cmd -and $cmd.Source) { return $cmd.Source }
    }
    return "powershell"
}

# ----------------------------------------------------------------------------
# Return the real OS architecture as a lowercase string for download URLs.
# On Windows on ARM under x64 emulation, [Environment]::OSArchitecture
# reports the emulated view. Win32_Processor.Architecture is invariant.
# ----------------------------------------------------------------------------
function Get-WindowsArch {
    try {
        $proc = Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop |
            Select-Object -First 1
        switch ([int]$proc.Architecture) {
            12 { return "arm64" }
            9  { return "x64" }
            0  { return "x86" }
            5  { return "arm" }
        }
    } catch { }
    $envArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($envArch) {
        "ARM64" { return "arm64" }
        "AMD64" { return "x64" }
        "x86"   { return "x86" }
        default { if ([Environment]::Is64BitOperatingSystem) { return "x64" } else { return "x86" } }
    }
}

# ----------------------------------------------------------------------------
# Stage 1: Install uv (managed, into $Agent8088Home\bin)
# ----------------------------------------------------------------------------
function Install-Uv {
    $managedUv = Join-Path $Agent8088Home "bin\uv.exe"

    if (Test-Path $managedUv) {
        $script:UvCmd = $managedUv
        $version = & $managedUv --version
        Write-Success "Managed uv found ($version)"
        return $true
    }

    Write-Info "Installing managed uv into $Agent8088Home\bin ..."
    New-Item -ItemType Directory -Path (Join-Path $Agent8088Home "bin") -Force | Out-Null

    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $env:UV_INSTALL_DIR = Join-Path $Agent8088Home "bin"
        $psHostExe = Get-PowerShellHostExe
        & $psHostExe -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP

        if (Test-Path $managedUv) {
            $script:UvCmd = $managedUv
            $version = & $managedUv --version
            Write-Success "Managed uv installed ($version)"
            return $true
        }
        Write-Err "uv installed but not found at $managedUv"
        Write-Info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        return $false
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        Write-Err "Failed to install uv: $_"
        Write-Info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        return $false
    }
}

# ----------------------------------------------------------------------------
# Stage 2: Find or install Python
# ----------------------------------------------------------------------------
function Resolve-AvailablePythonVersion {
    $candidates = @($PythonVersion) + $PythonFallbackVersions
    $seen = @{}
    foreach ($ver in $candidates) {
        if (-not $ver -or $seen.ContainsKey($ver)) { continue }
        $seen[$ver] = $true
        try {
            $found = & $script:UvCmd python find $ver 2>$null
            if ($found) { return $ver }
        } catch { }
    }
    return $null
}

function Test-Python {
    $resolvedVer = Resolve-AvailablePythonVersion
    if ($resolvedVer) {
        try {
            $pythonPath = & $script:UvCmd python find $resolvedVer 2>$null
            if ($pythonPath) {
                $ver = & $pythonPath --version 2>$null
                Write-Success "Python found: $ver"
                $script:PythonVersion = $resolvedVer
                return $true
            }
        } catch { }
    }

    Write-Info "Python not found, installing via uv..."
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $script:UvCmd python install $PythonVersion 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        $pythonPath = & $script:UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {
            $ver = & $pythonPath --version 2>$null
            Write-Success "Python installed: $ver"
            return $true
        }
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
    }

    # Fallback: try system python - but skip the Microsoft Store stub.
    # On Windows, %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe is a 0-byte
    # reparse-point that prints "Python was not found..." and exits non-zero.
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $isStoreStub = $false
        try {
            $pythonSource = $pythonCmd.Source
            if ($pythonSource -and $pythonSource -like "*\WindowsApps\*") {
                $isStoreStub = $true
            } else {
                $item = Get-Item $pythonSource -ErrorAction SilentlyContinue
                if ($item -and $item.Length -eq 0) { $isStoreStub = $true }
            }
        } catch { }
        if (-not $isStoreStub) {
            try {
                $prevEAP2 = $ErrorActionPreference
                $ErrorActionPreference = "Continue"
                $sysVer = & python --version 2>&1
                $ErrorActionPreference = $prevEAP2
                if ($sysVer -match "Python 3\.(1[0-9]|[1-9][0-9])") {
                    Write-Success "Using system Python: $sysVer"
                    return $true
                }
            } catch {
                if ($prevEAP2) { $ErrorActionPreference = $prevEAP2 }
            }
        }
    }

    Write-Err "Failed to install Python $PythonVersion"
    Write-Info "Install Python 3.11 manually: https://www.python.org/downloads/"
    Write-Info "Or: winget install Python.Python.3.11"
    return $false
}

# ----------------------------------------------------------------------------
# Stage 3: Install Git (PortableGit - no admin needed)
# ----------------------------------------------------------------------------
function Install-Git {
    Write-Info "Checking Git..."

    if (Get-Command git -ErrorAction SilentlyContinue) {
        $version = git --version
        Write-Success "Git found ($version)"
        return $true
    }

    Write-Info "Git not found - downloading PortableGit to $Agent8088Home\git\ ..."
    Write-Info "(no admin rights required; isolated from any system Git install)"

    try {
        $arch = Get-WindowsArch
        $assetTag = if ($arch -eq "arm64") { "arm64" } elseif ($arch -eq "x64") { "64-bit" } else { "32-bit-mingit" }
        $downloadIsZip = $assetTag -eq "32-bit-mingit"

        # Pinned git-for-windows release. We deliberately do NOT hit the API
        # /releases/latest endpoint (60 req/hr/IP rate limit for unauth users).
        $gitTag    = "v2.54.0.windows.1"
        $gitVer    = "2.54.0"
        $gitVerTag = "$gitVer.windows.1"

        if ($assetTag -eq "32-bit-mingit") {
            Write-Warn "32-bit Windows detected - installing MinGit 32-bit (bash-based features limited)."
            $assetName = "MinGit-$gitVer-32-bit.zip"
            $downloadIsZip = $true
        } elseif ($arch -eq "arm64") {
            $assetName = "PortableGit-$gitVer-arm64.7z.exe"
        } else {
            $assetName = "PortableGit-$gitVer-64-bit.7z.exe"
        }

        $downloadUrl = "https://github.com/git-for-windows/git/releases/download/$gitTag/$assetName"
        $tmpFile = "$env:TEMP\$assetName"
        $gitDir = "$Agent8088Home\git"

        Write-Info "Downloading $assetName (Git for Windows $gitVerTag)..."
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -UseBasicParsing -TimeoutSec $TDownload

        if (Test-Path $gitDir) { Remove-Item -Recurse -Force $gitDir }
        New-Item -ItemType Directory -Path $gitDir -Force | Out-Null

        if ($downloadIsZip) {
            Expand-Archive -Path $tmpFile -DestinationPath $gitDir -Force
        } else {
            # PortableGit is a self-extracting 7z archive.
            Write-Info "Extracting PortableGit to $gitDir ..."
            $extractProc = Start-Process -FilePath $tmpFile `
                -ArgumentList "-o`"$gitDir`"", "-y" `
                -NoNewWindow -Wait -PassThru
            if ($extractProc.ExitCode -ne 0) {
                throw "PortableGit extraction failed (exit code $($extractProc.ExitCode))"
            }
        }
        Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue

        $gitExe = "$gitDir\cmd\git.exe"
        if (-not (Test-Path $gitExe)) { throw "Git extraction did not produce git.exe at $gitExe" }

        # Add to session PATH
        $env:Path = "$gitDir\cmd;$env:Path"
        # Persist to User PATH
        $newPathEntries = @("$gitDir\cmd", "$gitDir\bin", "$gitDir\usr\bin")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $userPathItems = if ($userPath) { $userPath -split ";" } else { @() }
        $changed = $false
        foreach ($entry in $newPathEntries) {
            if ($userPathItems -notcontains $entry) { $userPathItems += $entry; $changed = $true }
        }
        if ($changed) { [Environment]::SetEnvironmentVariable("Path", ($userPathItems -join ";"), "User") }

        $version = & $gitExe --version
        Write-Success "Git $version installed to $gitDir (portable, user-scoped)"
        return $true
    } catch {
        Write-Err "Could not install portable Git: $_"
        Write-Info "Fallback: install Git manually from https://git-scm.com/download/win"
        return $false
    }
}

# ----------------------------------------------------------------------------
# Stage 4: Clone repo (with ZIP fallback)
# ----------------------------------------------------------------------------
function Clone-Repo {
    Write-Info "Installing to $InstallDir..."

    # Suppress git credential prompts - the repo is public, anonymous clone
    # works. Without these, Git Credential Manager on Windows pops a login
    # dialog even for public repos. If the repo were private, the clone would
    # fail cleanly instead of hanging on a prompt.
    $env:GIT_TERMINAL_PROMPT = "0"
    $env:GCM_INTERACTIVE = "never"

    # An interrupted previous clone leaves .git with no initial commit.
    if ((Test-Path (Join-Path $InstallDir ".git")) -and -not (& git -C $InstallDir rev-parse --verify HEAD 2>$null)) {
        $backupDir = "${InstallDir}.broken-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Write-Warn "Existing checkout at $InstallDir has no commits (interrupted clone)."
        Write-Warn "Moving it aside to $backupDir before re-cloning."
        Move-Item $InstallDir $backupDir
    }

    if (Test-Path (Join-Path $InstallDir ".git")) {
        Write-Info "Existing installation found, updating..."
        Push-Location $InstallDir
        try {
            & git -c windows.appendAtomically=false config core.autocrlf false
            $diff = & git -c windows.appendAtomically=false diff --name-only 2>$null
            if ($diff) {
                # Clear unmerged index entries
                $unmerged = & git -c windows.appendAtomically=false ls-files --unmerged 2>$null
                if ($unmerged) {
                    Write-Info "Clearing unmerged index entries..."
                    & git -c windows.appendAtomically=false reset -q
                }
                Write-Info "Local changes detected, stashing before update..."
                & git -c windows.appendAtomically=false stash push --include-untracked -m "agent8088-install-autostash" 2>$null | Out-Null
            }
            & git -c windows.appendAtomically=false remote set-url origin $RepoUrl 2>$null
            & git -c windows.appendAtomically=false fetch --depth 1 origin $Branch 2>$null
            & git -c windows.appendAtomically=false checkout -B $Branch FETCH_HEAD 2>$null
            & git -c windows.appendAtomically=false reset --hard FETCH_HEAD 2>$null
        } finally {
            Pop-Location
        }
    } else {
        Write-Info "Cloning Agent8088 repository..."
        if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
        New-Item -ItemType Directory -Path (Split-Path $InstallDir -Parent) -Force | Out-Null

        try {
            & git -c windows.appendAtomically=false clone --depth 1 --branch $Branch $RepoUrl $InstallDir
            & git -C $InstallDir -c windows.appendAtomically=false config core.autocrlf false
        } catch {
            # ZIP fallback: GitHub archive. Then git init so future updates work.
            Write-Warn "git clone failed; falling back to ZIP archive..."
            $zipUrl = "https://github.com/palindrome-rl/AGENT8088/archive/refs/heads/$Branch.zip"
            $tmpZip = "$env:TEMP\agent8088-$Branch.zip"
            Invoke-WebRequest -Uri $zipUrl -OutFile $tmpZip -UseBasicParsing -TimeoutSec $TDownload
            $tmpExtract = "$env:TEMP\agent8088-extract"
            if (Test-Path $tmpExtract) { Remove-Item -Recurse -Force $tmpExtract }
            Expand-Archive -Path $tmpZip -DestinationPath $tmpExtract -Force
            $extractedDir = Get-ChildItem $tmpExtract -Directory | Select-Object -First 1
            Move-Item $extractedDir.FullName $InstallDir
            Remove-Item -Force $tmpZip; Remove-Item -Recurse -Force $tmpExtract

            # Re-init so future `agent8088 update` works
            & git -C $InstallDir init 2>$null
            & git -C $InstallDir -c windows.appendAtomically=false config core.autocrlf false
            & git -C $InstallDir remote add origin $RepoUrl 2>$null
            & git -C $InstallDir fetch --depth 1 origin $Branch 2>$null
            & git -C $InstallDir checkout -t origin/$Branch 2>$null
        }
        $script:FreshInstall = $true
    }
    $installedCommit = (& git -C $InstallDir rev-parse --short HEAD 2>$null)
    if (-not $installedCommit) { $installedCommit = "unknown" }
    Write-Success "Repository ready at $InstallDir ($Branch@$installedCommit)"
}

# ----------------------------------------------------------------------------
# Stage 5: Create venv + install the package
# ----------------------------------------------------------------------------
function Install-Deps {
    Write-Info "Creating venv and installing via uv..."
    $venvDir = Join-Path $InstallDir "venv"
    $py = Join-Path $venvDir "Scripts\python.exe"
    # Relax EAP: uv writes progress ("Using CPython...") to stderr, which
    # $ErrorActionPreference="Stop" treats as a fatal NativeCommandError.
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        # --allow-existing: re-running the installer over an existing install is
        # a supported path, but plain `uv venv` exits 2 on one ("A virtual
        # environment already exists"). Here that was masked rather than fatal:
        # the Test-Path below finds python.exe from the PREVIOUS install and
        # carries on, so a failed venv step reported success and the update
        # silently did not happen. install.sh hit the same call and died.
        & $script:UvCmd venv --python $script:PythonVersion --allow-existing $venvDir 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $py)) {
            # A venv from a Python that has since gone, or a half-written one
            # from an interrupted run, cannot be reused. Rebuild it rather than
            # handing the user a decision they have no way to evaluate.
            Write-Warn "Existing virtualenv is not usable - rebuilding it"
            & $script:UvCmd venv --python $script:PythonVersion --clear $venvDir 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path $py)) {
                Write-Err "Run this to see the underlying error:"
                Write-Err "  $script:UvCmd venv --python $script:PythonVersion --clear $venvDir"
                Write-Err "If it keeps failing, remove the install and start clean: agent8088 --uninstall"
                throw "venv creation failed (uv exit $LASTEXITCODE)"
            }
        }
        & $script:UvCmd pip install --python $py --reinstall-package agent8088 -e $InstallDir 2>&1 | Out-Null
        $exit = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($exit -ne 0) {
            Write-Err "uv pip install failed (exit $exit)"
            throw "Failed to install agent8088"
        }
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        throw "Failed to install agent8088: $_"
    }
    Write-Success "agent8088 installed (editable)"
}

# ----------------------------------------------------------------------------
# Stage 5b: Gateway adapter Python extras + Playwright Chromium binary
# ----------------------------------------------------------------------------
# Installs the [gateway] optional extra (slack-bolt, slack-sdk, httpx,
# discord.py, python-telegram-bot) into the existing venv so the messaging
# adapters in runner.py are importable. Also downloads the Playwright
# Chromium browser binary so browse_page works out of the box.
# Both steps warn-on-fail and never abort: the core agent (chat, MCP, search,
# file tools) does not depend on either.
function Install-Gateway-Extras {
    $py = Join-Path $InstallDir "venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Warn "venv python not found at $py - skipping gateway extras"
        return
    }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        Write-Info "Installing gateway adapter dependencies (Slack, Discord, WhatsApp, Telegram)..."
        $gwResult = Invoke-WithTimeout -FilePath $script:UvCmd `
            -Arguments @("pip", "install", "--python", $py, "-e", "$InstallDir[gateway]") `
            -TimeoutSec $TPip
        if ($gwResult.ExitCode -eq 0) {
            $script:GatewayExtrasInstalled = $true
            Write-Success "Gateway adapters installed"
        } else {
            Write-StageWarning -Result $gwResult -TimeoutSec $TPip `
                -What "Gateway adapters (Slack/Discord/Telegram)" `
                -Consequence "core agent still works" `
                -Fix "$script:UvCmd pip install --python `"$py`" -e `"$InstallDir[gateway]`""
        }

        # Keyless web search backend ([search] extra - see pyproject.toml).
        Write-Info "Installing keyless web search backend (ddgs)..."
        $searchResult = Invoke-WithTimeout -FilePath $script:UvCmd `
            -Arguments @("pip", "install", "--python", $py, "-e", "$InstallDir[search]") `
            -TimeoutSec $TPip
        if ($searchResult.ExitCode -eq 0) {
            $script:SearchExtrasInstalled = $true
            Write-Success "Keyless web search backend installed"
        } else {
            Write-StageWarning -Result $searchResult -TimeoutSec $TPip `
                -What "Keyless web search (ddgs)" `
                -Consequence "configure SearXNG or an API-key backend for web_search" `
                -Fix "$script:UvCmd pip install --python `"$py`" -e `"$InstallDir[search]`""
        }

        # Playwright is an optional [browser] extra, so install the package
        # before asking it to fetch the Chromium binary.
        Write-Info "Installing Playwright (optional, for browse_page)..."
        $pwResult = Invoke-WithTimeout -FilePath $script:UvCmd `
            -Arguments @("pip", "install", "--python", $py, "-e", "$InstallDir[browser]") `
            -TimeoutSec $TPip
        if ($pwResult.ExitCode -eq 0) {
            Write-Info "Installing Playwright Chromium browser (~280 MB)..."
            $chromiumResult = Invoke-WithTimeout -FilePath $py `
                -Arguments @("-m", "playwright", "install", "chromium") `
                -TimeoutSec $TChromium
            if ($chromiumResult.ExitCode -eq 0) {
                $script:ChromiumInstalled = $true
                Write-Success "Chromium installed for browse_page"
            } else {
                Write-StageWarning -Result $chromiumResult -TimeoutSec $TChromium `
                    -What "Chromium browser" `
                    -Consequence "browse_page will show install instructions" `
                    -Fix "`"$py`" -m playwright install chromium"
            }
        } else {
            Write-StageWarning -Result $pwResult -TimeoutSec $TPip `
                -What "Playwright (browse_page)" `
                -Consequence "browse_page will show install instructions" `
                -Fix "$script:UvCmd pip install --python `"$py`" -e `"$InstallDir[browser]`""
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

# ----------------------------------------------------------------------------
# Stage 5c: Node.js (portable, for WhatsApp bridge) + npm install
# ----------------------------------------------------------------------------
# WhatsApp's bridge is a Node.js process (Baileys). Without Node on PATH the
# adapter errors at connect() time. We install a portable, user-scoped Node
# (no admin needed) mirroring the PortableGit pattern. Then npm install in the
# bridge dir so node_modules is materialized for the bridge to require().
function Install-Node-Bridge {
    # --- 1. Ensure Node >= 20.11 is available ------------------------------
    $nodeExe = $null
    $existingNode = Get-Command node -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($existingNode) {
        try {
            $ver = (& $existingNode.Source --version 2>$null) -replace '^v', ''
            $parts = $ver.Split('.')
            $major = [int]$parts[0]
            $minor = if ($parts.Count -ge 2) { [int]$parts[1] } else { 0 }
            if ($major -gt 20 -or ($major -eq 20 -and $minor -ge 11)) {
                $candidateNpm = Join-Path (Split-Path $existingNode.Source -Parent) "npm.cmd"
                if (Test-Path $candidateNpm) {
                    $nodeExe = $existingNode.Source
                    $npmExe = $candidateNpm
                    Write-Success "Node $ver found on PATH"
                } else {
                    Write-Warn "Node $ver has no matching npm.cmd at $candidateNpm - will install portable Node"
                }
            } else {
                Write-Warn "Node $ver found but < 20.11 - sandbox-runtime needs 20.11+; will install portable Node"
            }
        } catch {
            Write-Warn "Could not determine Node version - will install portable Node"
        }
    }

    if (-not $nodeExe) {
        $managedNode = Join-Path $Agent8088Home "node\node.exe"
        if (Test-Path $managedNode) {
            $ver = & $managedNode --version 2>$null
            if ($ver) {
                $nodeExe = $managedNode
                $npmExe = Join-Path $Agent8088Home "node\npm.cmd"
                Write-Success "Managed Node found ($ver)"
            }
        }
    }

    if (-not $nodeExe) {
        Write-Info "Installing portable Node $NodeVersion into $Agent8088Home\node ..."
        $arch = Get-WindowsArch
        $nodeArch = if ($arch -eq "arm64") { "arm64" } else { "x64" }
        $assetName = "node-v$NodeVersion-win-$nodeArch.zip"
        $downloadUrl = "https://nodejs.org/dist/v$NodeVersion/$assetName"
        $tmpFile = "$env:TEMP\$assetName"
        $nodeDir = "$Agent8088Home\node"

        try {
            Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -UseBasicParsing -TimeoutSec $TDownload
            if (Test-Path $nodeDir) { Remove-Item -Recurse -Force $nodeDir }
            New-Item -ItemType Directory -Path $nodeDir -Force | Out-Null
            Expand-Archive -Path $tmpFile -DestinationPath $nodeDir -Force
            Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue

            # Node ZIP extracts to a subfolder like node-v22.11.0-win-x64\node.exe
            $extractedExe = Get-ChildItem -Path $nodeDir -Recurse -Filter "node.exe" | Select-Object -First 1
            if (-not $extractedExe) { throw "Node extraction did not produce node.exe" }

            # Move contents up one level so $nodeDir\node.exe exists
            $extractedDir = Split-Path $extractedExe.FullName -Parent
            if ($extractedDir -ne $nodeDir) {
                Get-ChildItem -Path $extractedDir | Move-Item -Destination $nodeDir -Force
                Remove-Item -Recurse -Force $extractedDir
            }

            $nodeExe = Join-Path $nodeDir "node.exe"
            $npmExe = Join-Path $nodeDir "npm.cmd"
            if (-not (Test-Path $nodeExe)) { throw "node.exe not found after extraction at $nodeExe" }

            $env:Path = "$nodeDir;$env:Path"
            $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
            $userPathItems = if ($userPath) { $userPath -split ";" } else { @() }
            if ($userPathItems -notcontains $nodeDir) {
                $userPathItems += $nodeDir
                [Environment]::SetEnvironmentVariable("Path", ($userPathItems -join ";"), "User")
            }
            $ver = & $nodeExe --version
            Write-Success "Node $ver installed to $nodeDir (portable, user-scoped)"
        } catch {
            Write-Warn "Could not install portable Node: $_"
            Write-Info "WhatsApp bridge needs Node 20.11+ - install manually from https://nodejs.org/"
            Register-SkippedStage -Label "WhatsApp bridge (Node runtime)" `
                -Reason "portable Node install failed" `
                -Fix "install Node 20.11+ from https://nodejs.org/ then rerun this installer"
            return
        }
    }

    $script:NodeInstalled = $true

    # --- 2. npm install in the WhatsApp bridge dir ------------------------
    $bridgeDir = Join-Path $InstallDir "src\agent8088\gateway\platforms\whatsapp_bridge"
    if (-not (Test-Path (Join-Path $bridgeDir "package.json"))) {
        Write-Warn "WhatsApp bridge package.json not found at $bridgeDir - skipping npm install"
        return
    }
    $nodeModules = Join-Path $bridgeDir "node_modules"
    if (Test-Path $nodeModules) {
        Write-Success "WhatsApp bridge node_modules already present"
        $script:WhatsAppBridgeReady = $true
        return
    }

    Write-Info "Installing WhatsApp bridge npm dependencies..."
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Run npm *from inside* the bridge directory instead of pointing --prefix
        # at it. npm 10 (bundled with the portable Node 22.11.0 this installer
        # fetches) reads its config from --prefix but still resolves package.json
        # by walking up from the current directory. With the installer's own
        # location as that directory, npm found no package.json and aborted with
        # ENOENT / errno -4058, "Could not read package.json" - so the bridge was
        # left without node_modules on every affected machine.
        #
        # -WorkingDirectory sets it for the child process only; the caller's
        # location is untouched, so nothing leaks into later stages.
        $npmResult = Invoke-WithTimeout -FilePath $npmExe `
            -Arguments @("install", "--no-audit", "--no-fund") `
            -TimeoutSec $TNpm -WorkingDirectory $bridgeDir

        if ($npmResult.ExitCode -eq 0 -and (Test-Path $nodeModules)) {
            $script:WhatsAppBridgeReady = $true
            Write-Success "WhatsApp bridge npm dependencies installed"
        } elseif ($npmResult.ExitCode -eq 0) {
            Write-Warn "WhatsApp bridge npm install reported success but node_modules missing"
            Register-SkippedStage -Label "WhatsApp bridge npm deps" `
                -Reason "npm exited 0 but node_modules is missing" `
                -Fix "cd `"$bridgeDir`"; npm install"
        } else {
            Write-StageWarning -Result $npmResult -TimeoutSec $TNpm `
                -What "WhatsApp bridge npm deps" `
                -Consequence "the WhatsApp gateway will be unavailable until you rerun it" `
                -Fix "cd `"$bridgeDir`"; npm install"
            Write-Warn "Fix it later with:  cd `"$bridgeDir`"; npm install"
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

# ----------------------------------------------------------------------------
# Stage 5c2: Embedding model for persistent memory
# ----------------------------------------------------------------------------
# Memory is on by default, and its semantic recall needs an embedding model. This
# pulls it here rather than leaving it to first use, because the failure mode
# otherwise is silent: recall quietly degrades to keyword-only and the user has no
# reason to suspect the store is working at half strength.
#
# nomic-embed-text: 274 MB, 768 dimensions. Chosen over the top-of-leaderboard
# qwen3-embedding:0.6b (~1.2 GB) because memories are one-line facts and short
# queries, and BM25 carries half the ranking through RRF. See
# docs/wiki/16-memory.md.
#
# Not fatal if it cannot be pulled: an install that dies because a 274 MB model
# download failed is worse than one that says memory will use keyword search until
# the model is there. The message names the exact command to fix it.
$EmbedModel = "nomic-embed-text"

function Install-Embedding-Model {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) {
        # A cloud provider serves /embeddings itself, so there is nothing to pull.
        Write-Info "Ollama not found - memory will embed through your configured provider"
        $script:EmbedViaProvider = $true
        return
    }
    # `ollama list` talks to the daemon on :11434. It answers instantly when that
    # daemon is healthy and never when it is wedged, so it needs a bound too -
    # otherwise the installer hangs here, before the download it was guarding.
    $listResult = Invoke-WithTimeout -FilePath $ollama.Source `
        -Arguments @("list") -TimeoutSec $TOllamaCheck -CaptureOutput
    if ($listResult.Output -match "(?m)^$([regex]::Escape($EmbedModel))") {
        Write-Success "Embedding model $EmbedModel already present"
        $script:EmbedModelReady = $true
        return
    }

    Write-Info "Pulling embedding model $EmbedModel (274 MB, for memory recall)..."
    $pullResult = Invoke-WithTimeout -FilePath $ollama.Source `
        -Arguments @("pull", $EmbedModel) -TimeoutSec $TOllamaPull
    if ($pullResult.ExitCode -eq 0) {
        Write-Success "Embedding model $EmbedModel installed"
        $script:EmbedModelReady = $true
    } else {
        Write-StageWarning -Result $pullResult -TimeoutSec $TOllamaPull `
            -What "Embedding model ($EmbedModel)" `
            -Consequence "memory recall will use keyword search only" `
            -Fix "ollama pull $EmbedModel"
        Write-Warn "Fix it later with:  ollama pull $EmbedModel"
    }
}

# ----------------------------------------------------------------------------
# Stage 5d: Native sandbox runtime (Windows - deliberately not run)
# ----------------------------------------------------------------------------
# `agent8088 --sandbox-setup` is intentionally NOT invoked here.
#
# On Windows the runtime provisions a restricted `srt-sandbox` account and spawns
# sandboxed children as it through CreateProcessWithLogonW. On at least one
# machine that spawn is refused with ERROR_ACCESS_DENIED and Windows writes no
# Security audit event at all, and it stayed refused after ruling out antivirus,
# an architecture mismatch, the Node version, per-user credential scoping
# (runtime 0.0.73 moved install state machine-wide) and a clean reprovision. It
# reproduces with agent8088 out of the picture, driving the runtime CLI directly,
# so it is not ours to fix from here. Running setup during install only ends an
# otherwise successful install with an alarming failure the reader cannot act on.
#
# Docker carries Windows sandboxing meanwhile, and it is a real sandbox: no
# network, capped memory and CPU, every capability dropped. Little is lost by not
# provisioning native, because `sandbox_allowed_domains` ships empty and native's
# egress allowlist is the one thing Docker cannot express.
#
# To restore: `agent8088 --sandbox-setup` from an elevated terminal still works
# and is unchanged. Git history holds the self-elevating version of this stage for
# when the upstream spawn failure is understood.
function Install-Native-Sandbox {
    Write-Info "Native sandbox not set up - Docker will be used for sandboxing if available."
}

# ----------------------------------------------------------------------------
# Stage 6: Link the command (add venv\Scripts to User PATH)
# ----------------------------------------------------------------------------
function Setup-Path {
    $venvScripts = Join-Path $InstallDir "venv\Scripts"
    Write-Info "Adding $venvScripts to User PATH..."

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $userPathItems = if ($userPath) { $userPath -split ";" } else { @() }
    if ($userPathItems -notcontains $venvScripts) {
        $userPathItems += $venvScripts
        [Environment]::SetEnvironmentVariable("Path", ($userPathItems -join ";"), "User")
        Write-Success "Added $venvScripts to User PATH"
    } else {
        Write-Success "$venvScripts already on PATH"
    }
    # Session PATH so the rest of this run can find agent8088
    $env:Path = "$venvScripts;$env:Path"
}

# ----------------------------------------------------------------------------
# Stage 7: Drop default config
# ----------------------------------------------------------------------------
function Drop-Config {
    $configPath = Join-Path $Agent8088Home "config.txt"
    if (-not (Test-Path $configPath)) {
        Write-Info "Dropping default config.txt to $configPath"
        # The default config.txt ships at src/agent8088/config.txt in the repo.
        # For an editable install (-e), site-packages only has a .pth pointer,
        # so the venv path misses; the repo source path is the reliable one.
        $venvConfig = Join-Path $InstallDir "venv\Lib\site-packages\agent8088\config.txt"
        $repoConfig = Join-Path $InstallDir "config.txt"
        $srcConfig = Join-Path $InstallDir "src\agent8088\config.txt"
        if (Test-Path $venvConfig) {
            Copy-Item $venvConfig $configPath
        } elseif (Test-Path $repoConfig) {
            Copy-Item $repoConfig $configPath
        } elseif (Test-Path $srcConfig) {
            Copy-Item $srcConfig $configPath
        } else {
            Write-Warn "No default config.txt found; you'll need to create one"
            return
        }
        Protect-ConfigFile $configPath
        Write-Success "Default config.txt copied"
    } else {
        Write-Info "config.txt already exists at $configPath - preserving"
        Protect-ConfigFile $configPath
    }

    # Set AGENT8088_CONFIG env var
    [Environment]::SetEnvironmentVariable("AGENT8088_CONFIG", $configPath, "User")
    $env:AGENT8088_CONFIG = $configPath
}

# ----------------------------------------------------------------------------
# Stage 8: Setup wizard
# ----------------------------------------------------------------------------
$BuiltinModelProviders = @(
    "ollama", "openrouter", "openai", "gemini", "cerebras", "deepseek",
    "groq", "mistral", "moonshot", "qwen", "ollama-cloud", "copilot"
)
$BuiltinProviderLabels = @{
    "ollama" = "Ollama (local)"
    "openrouter" = "OpenRouter"
    "openai" = "OpenAI"
    "gemini" = "Google Gemini"
    "cerebras" = "Cerebras"
    "deepseek" = "DeepSeek"
    "groq" = "Groq"
    "mistral" = "Mistral"
    "moonshot" = "Moonshot (Kimi)"
    "qwen" = "Qwen (DashScope)"
    "ollama-cloud" = "Ollama Cloud"
    "copilot" = "GitHub Copilot"
}
$BuiltinProviderUrls = @{
    "ollama" = "http://localhost:11434/v1"
    "openrouter" = "https://openrouter.ai/api/v1"
    "openai" = "https://api.openai.com/v1"
    "gemini" = "https://generativelanguage.googleapis.com/v1beta/openai/"
    "cerebras" = "https://api.cerebras.ai/v1"
    "deepseek" = "https://api.deepseek.com/v1"
    "groq" = "https://api.groq.com/openai/v1"
    "mistral" = "https://api.mistral.ai/v1"
    "moonshot" = "https://api.moonshot.ai/v1"
    "qwen" = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    "ollama-cloud" = "https://ollama.com/v1"
    "copilot" = "https://api.githubcopilot.com"
}
$BuiltinProviderModels = @{
    "ollama" = "qwen14b-tooluse-v3"
    "openrouter" = "anthropic/claude-sonnet-4"
    "openai" = "gpt-4o"
    "gemini" = "gemini-2.0-flash"
    "cerebras" = "gpt-oss-120b"
    "deepseek" = "deepseek-chat"
    "groq" = "llama-3.3-70b-versatile"
    "mistral" = "mistral-small-latest"
    "moonshot" = "kimi-k2.6"
    "qwen" = "qwen-plus"
    "ollama-cloud" = "gpt-oss:120b"
    "copilot" = "gpt-4o-mini"
}

function Select-ModelProvider {
    param([string]$CurrentProvider)
    Write-Host "Select model provider:"
    for ($i = 0; $i -lt $BuiltinModelProviders.Count; $i++) {
        $provider = $BuiltinModelProviders[$i]
        Write-Host ("  {0,2}) {1} ({2}) - default: {3}" -f ($i + 1), $BuiltinProviderLabels[$provider], $provider, $BuiltinProviderModels[$provider])
    }
    $customIndex = $BuiltinModelProviders.Count + 1
    Write-Host ("  {0,2}) Custom OpenAI-compatible" -f $customIndex)
    $answer = Read-Host "Choice [$CurrentProvider]"
    if (-not $answer) { $answer = $CurrentProvider }
    $number = 0
    if ([int]::TryParse($answer, [ref]$number)) {
        if ($number -ge 1 -and $number -le $BuiltinModelProviders.Count) {
            return $BuiltinModelProviders[$number - 1]
        }
        if ($number -eq $customIndex) { return "__custom__" }
    }
    $answer = $answer.ToLowerInvariant()
    if ($BuiltinModelProviders -contains $answer) { return $answer }
    if ($answer -eq $CurrentProvider.ToLowerInvariant()) { return $CurrentProvider }
    if ($answer -in @("custom", "custom openai-compatible", "openai-compatible")) { return "__custom__" }
    Write-Warn "Unknown provider '$answer'; keeping $CurrentProvider"
    return $CurrentProvider
}

function Read-SecretValue {
    param([string]$Prompt)
    $secure = Read-Host $Prompt -AsSecureString
    if (-not $secure -or $secure.Length -eq 0) { return "" }
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Run-SetupWizard {
    if ($SkipSetup) {
        Write-Info "Skipping setup wizard (--SkipSetup)"
        return
    }
    if ($NonInteractive) {
            Write-Info "Non-interactive mode - skipping setup wizard"
        Write-Info "Edit $Agent8088Home\config.txt manually to configure your model."
        return
    }

    $config = Join-Path $Agent8088Home "config.txt"
    Write-Info "Setup wizard"
    Write-Info "  (Press Enter to keep the default shown in brackets)"

    # Working directory
    $currentPaths = (Select-String -Path $config -Pattern '^allowed_paths=' | ForEach-Object { $_.Line -replace 'allowed_paths=', '' })
    if (-not $currentPaths) { $currentPaths = "~" }
    $newPaths = Read-Host "Working directory [$currentPaths]"
    if (-not $newPaths) { $newPaths = $currentPaths }

    # Provider picker
    $currentProvider = (Select-String -Path $config -Pattern '^default_provider=' | ForEach-Object { $_.Line -replace 'default_provider=', '' })
    if (-not $currentProvider) { $currentProvider = "ollama" }
    $selectedProvider = Select-ModelProvider $currentProvider
    $newProvider = $selectedProvider
    $baseUrl = ""
    if ($selectedProvider -eq "__custom__") {
        $defaultCustom = if ($BuiltinModelProviders -contains $currentProvider) { "custom" } else { $currentProvider }
        $newProvider = Read-Host "Custom provider name [$defaultCustom]"
        if (-not $newProvider) { $newProvider = $defaultCustom }
        if ($newProvider -notmatch '^[A-Za-z0-9_-]+$') {
            Write-Err "Custom provider names use letters, numbers, _ or -"
            exit 1
        }
        $currentUrl = (Select-String -Path $config -Pattern "^provider\.$newProvider\.base_url=" | ForEach-Object { $_.Line -replace "provider\.$newProvider\.base_url=", '' })
        $urlLabel = if ($currentUrl) { "Enter keeps current" } else { "required" }
        $baseUrl = Read-Host "OpenAI-compatible URL [$urlLabel]"
        if (-not $baseUrl) { $baseUrl = $currentUrl }
        if (-not $baseUrl) {
            Write-Err "OpenAI-compatible URL is required for custom providers"
            exit 1
        }
    } elseif ($BuiltinModelProviders -notcontains $newProvider) {
        $baseUrl = (Select-String -Path $config -Pattern "^provider\.$newProvider\.base_url=" | ForEach-Object { $_.Line -replace "provider\.$newProvider\.base_url=", '' })
        if (-not $baseUrl) {
            Write-Err "OpenAI-compatible URL is required for custom providers"
            exit 1
        }
    }

    # Model name
    $currentModel = (Select-String -Path $config -Pattern "^provider\.$newProvider\.model=" | ForEach-Object { $_.Line -replace "provider\.$newProvider\.model=", '' })
    if (-not $currentModel) { $currentModel = if ($BuiltinProviderModels[$newProvider]) { $BuiltinProviderModels[$newProvider] } else { "model-name" } }
    $newModel = Read-Host "Model name [$currentModel]"
    if (-not $newModel) { $newModel = $currentModel }

    # API key
    $currentKey = (Select-String -Path $config -Pattern "^provider\.$newProvider\.api_key=" | ForEach-Object { $_.Line -replace "provider\.$newProvider\.api_key=", '' })
    $newKey = Read-SecretValue "API key for $newProvider [hidden; Enter keeps existing/skips]"
    if (-not $newKey) { $newKey = $currentKey }

    # Web search URL (optional)
    $currentSearch = (Select-String -Path $config -Pattern '^search_base_url=' | ForEach-Object { $_.Line -replace 'search_base_url=', '' })
    $newSearch = Read-Host "Web search URL (SearXNG) [Enter keeps current; type none to disable]"

    if (-not $baseUrl) { $baseUrl = $BuiltinProviderUrls[$newProvider] }

    # Write back
    $content = Get-Content $config -Raw
    $content = $content -replace '(?m)^allowed_paths=.*', "allowed_paths=$newPaths"
    $projectRoot = ($newPaths -split ',', 2)[0].Trim()
    $content = $content -replace '(?m)^#?\s*project_root=.*', "project_root=$projectRoot"
    if (-not ($content -match '(?m)^project_root=')) { $content += "`nproject_root=$projectRoot`n" }
    $content = $content -replace '(?m)^default_provider=.*', "default_provider=$newProvider"
    if (-not ($content -match '(?m)^default_provider=')) { $content += "`ndefault_provider=$newProvider`n" }
    $content = $content -replace "(?m)^provider\.$newProvider\.base_url=.*", "provider.$newProvider.base_url=$baseUrl"
    if (-not ($content -match "(?m)^provider\.$newProvider\.base_url=")) { $content += "`nprovider.$newProvider.base_url=$baseUrl`n" }
    if ($BuiltinModelProviders -notcontains $newProvider) {
        $content = $content -replace "(?m)^provider\.$newProvider\.api_mode=.*", "provider.$newProvider.api_mode=openai"
        if (-not ($content -match "(?m)^provider\.$newProvider\.api_mode=")) { $content += "`nprovider.$newProvider.api_mode=openai`n" }
    }
    $content = $content -replace "(?m)^provider\.$newProvider\.model=.*", "provider.$newProvider.model=$newModel"
    if (-not ($content -match "(?m)^provider\.$newProvider\.model=")) { $content += "`nprovider.$newProvider.model=$newModel`n" }
    if ($newKey) {
        $content = $content -replace "(?m)^provider\.$newProvider\.api_key=.*", "provider.$newProvider.api_key=$newKey"
        if (-not ($content -match "(?m)^provider\.$newProvider\.api_key=")) { $content += "`nprovider.$newProvider.api_key=$newKey`n" }
    }
    if ($newSearch -and $newSearch.Trim().ToLowerInvariant() -eq "none") {
        $content = $content -replace '(?m)^search_base_url=.*\r?\n?', ''
    } elseif ($newSearch) {
        # Anchored at column 0: config.txt documents commented example endpoints,
        # and a '^#?\s*' pattern rewrote every one of them into a duplicate key.
        $content = $content -replace '(?m)^search_base_url=.*', "search_base_url=$newSearch"
        if (-not ($content -match '(?m)^search_base_url=')) { $content += "`nsearch_base_url=$newSearch`n" }
    }
    Set-Content -Path $config -Value $content -NoNewline:$false
    Write-Success "Config written to $config"
}

# ----------------------------------------------------------------------------
# Stage 9: Verify + finish
# ----------------------------------------------------------------------------
function Verify-Install {
    Write-Info "Verifying install..."
    $agentExe = Join-Path $InstallDir "venv\Scripts\agent8088.exe"
    if (Test-Path $agentExe) {
        try { & $agentExe --version 2>$null | Out-Host } catch { }
    }
    Write-Host ""
    Write-Success "Done. Run 'agent8088' to start."
    Write-Host "  Config: $Agent8088Home\config.txt"
    # Readiness summary - reflects what actually installed, not static text.
    if ($script:GatewayExtrasInstalled) {
        Write-Host "  Adapters: Slack/Discord/Telegram/WhatsApp (Python deps installed)"
    } else {
        Write-Host "  Adapters: gateway extras not installed (run: uv pip install -e `".[gateway]`")"
    }
    if ($script:SearchExtrasInstalled) {
        Write-Host "  Search:   keyless ddgs backend installed"
    } else {
        Write-Host "  Search:   ddgs unavailable - configure SearXNG or an API-key backend"
    }
    if ($script:ChromiumInstalled) {
        Write-Host "  Browser:  Chromium installed (browse_page ready)"
    } else {
        Write-Host "  Browser:  Chromium missing (browse_page will show install instructions)"
    }
    if ($script:WhatsAppBridgeReady) {
        Write-Host "  WhatsApp: Node bridge ready (run 'node bridge.js --pair' to pair)"
    } elseif ($script:NodeInstalled) {
        Write-Host "  WhatsApp: Node installed but bridge npm deps missing"
    } else {
        Write-Host "  WhatsApp: needs Node 20.11+ (install from https://nodejs.org/)"
    }
    if ($script:SandboxInstalled) {
        Write-Host "  Sandbox:  native runtime installed"
    } else {
        Write-Host "  Sandbox:  Docker is used when available"
        Write-Host "            Native is not set up on Windows yet (optional, may fail):"
        Write-Host "            elevated agent8088 --sandbox-setup"
    }
    # Memory: semantic recall needs an embedder; keyword search works without one.
    # No local Ollama is not a downgrade - the configured provider serves
    # /embeddings itself - so that case must not read as a failure.
    if ($script:EmbedModelReady) {
        Write-Host "  Memory:   $EmbedModel ready (keyword + semantic recall)"
    } elseif ($script:EmbedViaProvider) {
        Write-Host "  Memory:   embeddings served by your configured provider"
    } else {
        Write-Host "  Memory:   keyword recall only ($EmbedModel not installed)"
    }
    Write-Host "  Update: `$env:AGENT8088_BRANCH = '$Branch'; iex (irm https://raw.githubusercontent.com/palindrome-rl/AGENT8088/$Branch/install.ps1)"
    Write-Host ""
    Write-Host "If 'agent8088' is not recognized, open a NEW terminal (PATH was updated)."
    Write-SkippedSummary
}

function Run-InitialSetup {
    if (-not $script:FreshInstall) {
        Write-Info "Existing installation updated - skipping first-run setup."
        return
    }
    if ($SkipSetup) {
        Write-Info "Skipping first-run setup (--SkipSetup)"
        return
    }
    if ($NonInteractive) {
        Write-Info "Non-interactive mode - skipping first-run setup"
        Write-Info "Run agent8088 --setup later to configure your model."
        return
    }

    $agentExe = Join-Path $InstallDir "venv\Scripts\agent8088.exe"
    if (-not (Test-Path $agentExe)) {
        Write-Warn "agent8088 command is not ready yet; run agent8088 --setup later."
        return
    }
    Write-Info "Starting first-run setup..."
    & $agentExe --setup
    if ($LASTEXITCODE -eq 0) {
        $script:InitialSetupRan = $true
    } else {
        Write-Warn "First-run setup did not complete; run agent8088 --setup later."
    }
}

function Start-InitialAgent {
    if (-not $script:FreshInstall -or -not $script:InitialSetupRan) { return }

    $agentExe = Join-Path $InstallDir "venv\Scripts\agent8088.exe"
    Write-Host ""
    Write-Info "Starting Agent8088..."
    & $agentExe
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
Write-Banner
if (-not (Install-Uv)) { exit 1 }
if (-not (Test-Python)) { exit 1 }
if (-not (Install-Git)) { exit 1 }
Clone-Repo
Install-Deps
Install-Gateway-Extras
Install-Node-Bridge
Install-Embedding-Model
Install-Native-Sandbox
Setup-Path
Drop-Config
Run-InitialSetup
Verify-Install
Start-InitialAgent
