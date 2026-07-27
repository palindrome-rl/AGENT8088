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
    [string]$Branch = "main",
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
$RepoUrl = "https://github.com/tayyabimam1/AGENT8088.git"
$PythonVersion = "3.11"
$PythonFallbackVersions = @("3.12", "3.10")

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
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -UseBasicParsing

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
            & git -c windows.appendAtomically=false remote set-branches origin $Branch 2>$null
            & git -c windows.appendAtomically=false fetch origin $Branch 2>$null
            & git -c windows.appendAtomically=false checkout $Branch 2>$null
            if (-not (& git -c windows.appendAtomically=false pull --ff-only origin $Branch 2>$null)) {
                Write-Warn "Fast-forward not possible; resetting to origin/$Branch..."
                & git -c windows.appendAtomically=false reset --hard "origin/$Branch" 2>$null
            }
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
            $zipUrl = "https://github.com/tayyabimam1/AGENT8088/archive/refs/heads/$Branch.zip"
            $tmpZip = "$env:TEMP\agent8088-$Branch.zip"
            Invoke-WebRequest -Uri $zipUrl -OutFile $tmpZip -UseBasicParsing
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
    }
    Write-Success "Repository ready at $InstallDir"
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
        & $script:UvCmd venv $venvDir 2>&1 | Out-Null
        if (-not (Test-Path $py)) { throw "venv creation failed: $py not found" }
        & $script:UvCmd pip install --python $py -e $InstallDir 2>&1 | Out-Null
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
        # The installed package ships a default config.txt next to engine.py.
        $venvConfig = Join-Path $InstallDir "venv\Lib\site-packages\agent8088\config.txt"
        $repoConfig = Join-Path $InstallDir "config.txt"
        if (Test-Path $venvConfig) {
            Copy-Item $venvConfig $configPath
        } elseif (Test-Path $repoConfig) {
            Copy-Item $repoConfig $configPath
        } else {
            Write-Warn "No default config.txt found; you'll need to create one"
            return
        }
        Write-Success "Default config.txt copied"
    } else {
        Write-Info "config.txt already exists at $configPath - preserving"
    }

    # Set AGENT8088_CONFIG env var
    [Environment]::SetEnvironmentVariable("AGENT8088_CONFIG", $configPath, "User")
    $env:AGENT8088_CONFIG = $configPath
}

# ----------------------------------------------------------------------------
# Stage 8: Setup wizard
# ----------------------------------------------------------------------------
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
    Write-Info "Setup wizard - configure your model endpoint"
    Write-Info "  (Press Enter to keep the default shown in brackets)"

    # Read current values
    $currentUrl = (Select-String -Path $config -Pattern '^model_base_url=' | ForEach-Object { $_.Line -replace 'model_base_url=', '' })
    if (-not $currentUrl) { $currentUrl = "http://localhost:11434/v1" }
    $newUrl = Read-Host "Model base URL [$currentUrl]"
    if (-not $newUrl) { $newUrl = $currentUrl }

    $currentName = (Select-String -Path $config -Pattern '^model_name=' | ForEach-Object { $_.Line -replace 'model_name=', '' })
    if (-not $currentName) { $currentName = "qwen14b-tooluse-v3" }
    $newName = Read-Host "Model name [$currentName]"
    if (-not $newName) { $newName = $currentName }

    $currentKey = (Select-String -Path $config -Pattern '^api_key=' | ForEach-Object { $_.Line -replace 'api_key=', '' })
    if (-not $currentKey) { $currentKey = "ollama" }
    $newKey = Read-Host "API key [$currentKey]"
    if (-not $newKey) { $newKey = $currentKey }

    # Write back
    $content = Get-Content $config -Raw
    $content = $content -replace '(?m)^model_base_url=.*', "model_base_url=$newUrl"
    $content = $content -replace '(?m)^model_name=.*', "model_name=$newName"
    $content = $content -replace '(?m)^api_key=.*', "api_key=$newKey"
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
    Write-Host "  Update: cd $InstallDir; git pull; uv pip install --python venv\Scripts\python.exe -e ."
    Write-Host ""
    Write-Host "If 'agent8088' is not recognized, open a NEW terminal (PATH was updated)."
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
Setup-Path
Drop-Config
Run-SetupWizard
Verify-Install