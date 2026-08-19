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

# ----------------------------------------------------------------------------
# TLS -- must run before ANY https call, including the terminal gate's relaunch
# ----------------------------------------------------------------------------
# Windows PowerShell 5.1 on Windows 10 pre-1809 and Server 2016/2019 defaults
# ServicePointManager.SecurityProtocol to Ssl3, Tls -- TLS 1.0. Every host this
# installer touches (astral.sh, github.com, nodejs.org, raw.githubusercontent.com)
# has required TLS 1.2 since 2018, so on those systems the first https call dies
# with "Could not create SSL/TLS secure channel" -- a message that names TLS
# rather than the missing setting.
#
# Ordering is load-bearing: Start-InstallerInWindowsTerminal re-downloads this
# script with Invoke-RestMethod when $PSCommandPath is empty, which is the case on
# the documented `iex (irm ...)` path. That relaunch needs TLS 1.2 too.
#
# PowerShell 7 negotiates via the OS and ignores this property, so it is a no-op
# there. -bor rather than assignment: leaving the existing flags alone means a
# host that still requires TLS 1.0 (a corporate TLS-terminating proxy) keeps
# working.
try {
    $tlsWanted = [Net.SecurityProtocolType]::Tls12
    if ([enum]::GetNames([Net.SecurityProtocolType]) -contains 'Tls13') {
        $tlsWanted = $tlsWanted -bor [Net.SecurityProtocolType]::Tls13
    }
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor $tlsWanted
} catch {
    # A .NET too old to know Tls12, or a constrained host. A download that needs
    # it will fail with its own TLS message rather than silently here.
}

# ----------------------------------------------------------------------------
# PowerShell version floor -- also before the terminal gate
# ----------------------------------------------------------------------------
# 5.1 is the floor. Ensure-SupportedTerminal calls Get-AppxPackage and uses
# -notin, so on PS 3.0/4.0 it fails from inside the gate with a message about
# Appx rather than about the PowerShell version. Name it once, here.
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "[X] PowerShell $($PSVersionTable.PSVersion) is too old." -ForegroundColor Red
    Write-Host "    Windows PowerShell 5.1 or PowerShell 7+ is required."
    Write-Host "    5.1 ships with Windows 10 / Server 2016+; otherwise install"
    Write-Host "    PowerShell 7: https://aka.ms/powershell"
    exit 1
}

# ----------------------------------------------------------------------------
# Proxy
# ----------------------------------------------------------------------------
# WebClient and Invoke-WebRequest do NOT read HTTP_PROXY the way curl does; on
# Windows the proxy normally comes from WinHTTP/IE settings, which a machine
# configured only through environment variables does not have. Resolve one object
# here and hand it to Invoke-BoundedDownload.
$script:ResolvedProxy = $null
$proxyUrl = if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } elseif ($env:HTTP_PROXY) { $env:HTTP_PROXY } else { "" }
if ($proxyUrl) {
    try {
        $script:ResolvedProxy = New-Object System.Net.WebProxy($proxyUrl, $true)
        # An authenticating corporate proxy typically accepts the logged-in
        # identity, which avoids prompting for a password we must never handle.
        $script:ResolvedProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
        if ($env:NO_PROXY) { $script:ResolvedProxy.BypassList = $env:NO_PROXY -split ',' }
        [System.Net.WebRequest]::DefaultWebProxy = $script:ResolvedProxy
        # Masked: HTTPS_PROXY frequently carries user:password@host, and echoing it
        # would put a credential in the terminal scrollback and any CI log.
        Write-Host "-> Using proxy: $($proxyUrl -replace '://[^@/]+@', '://***@')" -ForegroundColor Cyan
    } catch {
        Write-Host "[!] Could not parse proxy '$($proxyUrl -replace '://[^@/]+@', '://***@')' - continuing without it" -ForegroundColor Yellow
    }
}

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
# Tool-level stall guards
# ----------------------------------------------------------------------------
# The wall-clock wrappers below are a backstop, not the first line of defence.
# Without these, a registry that accepts the connection and then goes quiet burns
# the ENTIRE wall-clock budget inside one dead request, so the wrapper's kill is
# the first thing that happens rather than a fast failure and a retry against a
# mirror that works. Only set when unset, so an operator on a genuinely slow link
# can raise them.
if (-not $env:UV_HTTP_TIMEOUT)     { $env:UV_HTTP_TIMEOUT = "60" }
if (-not $env:PIP_DEFAULT_TIMEOUT) { $env:PIP_DEFAULT_TIMEOUT = "60" }
if (-not $env:PIP_RETRIES)         { $env:PIP_RETRIES = "3" }

# git aborts a transfer that stays under 1 KB/s for 60s. This is what bounds
# Clone-Repo's `git clone` / `git fetch`, which have no wrapper of their own --
# wrapping them would swallow the progress output people rely on to see life.
if (-not $env:GIT_HTTP_LOW_SPEED_LIMIT) { $env:GIT_HTTP_LOW_SPEED_LIMIT = "1000" }
if (-not $env:GIT_HTTP_LOW_SPEED_TIME)  { $env:GIT_HTTP_LOW_SPEED_TIME  = "60" }

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
if (-not $InstallDir) { $InstallDir = Join-Path $Agent8088Home "agent8088" }
$LauncherDir = "${Agent8088Home}-launcher"
$RepoUrl = "https://github.com/tayyabimam1/AGENT8088.git"
$PythonVersion = "3.11"
$PythonFallbackVersions = @("3.12", "3.10")
$NodeVersion = "22.11.0"
$WindowsTerminalMinVersion = [version]"1.19.0.0"
$PendingUninstallWaitSeconds = 65
$FreshInstall = $false
$ConfigCreated = $false
$InitialSetupRan = $false
# Readiness flags set by the new stages so Verify-Install can report actual state.
$GatewayExtrasInstalled = $false
$SearchExtrasInstalled = $false
$ChromiumInstalled = $false
$NodeInstalled = $false
$WhatsAppBridgeReady = $false
$SandboxInstalled = $false

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
# Timeouts for the network stages
# ----------------------------------------------------------------------------
# Nothing in this installer had a time limit, so any of its network calls could
# hang forever: a stalled `ollama pull`, an npm registry that accepts the
# connection and then goes quiet, a wedged Ollama daemon, or a package download
# that dribbles bytes left the installer waiting with no way out but Ctrl-C.
#
# Limits are deliberately moderate rather than maximally generous: an optional
# stage degrades to a "run this to fix it later" message, so the cost of cutting
# a slow-but-working download short is one rerun, while the cost of waiting too
# long is an installer that looks frozen. Sized so a ~4 Mbps link finishes
# comfortably.
#
# Scale them all for a slow connection:
#   $env:AGENT8088_TIMEOUT_SCALE = 3; iex (irm <url>)
$TimeoutScale = 1
if ($env:AGENT8088_TIMEOUT_SCALE -match '^\d+$' -and [int]$env:AGENT8088_TIMEOUT_SCALE -ge 1) {
    $TimeoutScale = [int]$env:AGENT8088_TIMEOUT_SCALE
}

$TOllamaCheck = 15  * $TimeoutScale   # nothing, local - instant unless the daemon is wedged
$TOllamaPull  = 600 * $TimeoutScale   # 274 MB embedding model
$TNpm         = 300 * $TimeoutScale   # 142 small packages, mostly round-trips
$TChromium    = 600 * $TimeoutScale   # ~150 MB browser download
$TDownload    = 180 * $TimeoutScale   # ~30 MB archives (Node, MinGit, repo ZIP)
$TPip         = 300 * $TimeoutScale   # gateway extras: tens of MB of wheels
# The core editable install is the stage that actually hangs: it pulls
# playwright's and ddgs's native wheels plus mcp and Pillow. Not optional, so a
# premature cut fails the install outright -- but it is still the largest
# download set here, so it gets the same 10m ceiling as Chromium.
$TCoreInstall = 600 * $TimeoutScale
$TVenv        = 300 * $TimeoutScale   # uv may download a CPython build
$TUvBoot      = 300 * $TimeoutScale   # uv self-installer
$TExtract     = 300 * $TimeoutScale   # PortableGit self-extractor

# Run an external command under a wall-clock limit.
#
# PowerShell has no `timeout`, so this uses System.Diagnostics.Process plus
# WaitForExit(ms). That also solves a second problem: -WorkingDirectory sets the
# child's directory without touching the caller's location.
#
# Output goes to pipes rather than the console to preserve the quiet install the
# previous `2>&1 | Out-Null` calls had. Returns a hashtable so callers can tell a
# hang from an ordinary non-zero exit:
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

# Download one file under a hard wall-clock limit covering the BODY transfer.
#
# Invoke-WebRequest cannot do this on either edition, with or without -TimeoutSec.
# On Windows PowerShell 5.1 that parameter maps to HttpWebRequest.Timeout, which
# covers only up to the response headers -- the body is governed by a separate
# per-read ReadWriteTimeout. On PowerShell 7 it maps to HttpClient.Timeout, which
# stops applying once headers arrive and the stream copy to -OutFile begins.
# Either way a server that accepts, sends headers, then dribbles bytes forever
# hangs the installer with no way out but Ctrl-C.
#
# WebClient.DownloadFileTaskAsync + Task.Wait(ms) bounds the whole operation and
# is the only API with one code path on 5.1 and 7.x. Obsolete in .NET Core, not
# removed. Returns a hashtable shaped like Invoke-WithTimeout's:
#   @{ Success = <bool>; TimedOut = <bool>; Error = <string> }
#
# -Proxy is a parameter rather than a read of script scope so the function stays
# runnable in isolation by tests/test_installer_timeouts.py.
function Invoke-BoundedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [Parameter(Mandatory = $true)][int]$TimeoutSec,
        [System.Net.IWebProxy]$Proxy
    )

    $result = @{ Success = $false; TimedOut = $false; Error = "" }
    $client = $null
    try {
        $client = New-Object System.Net.WebClient
        # GitHub release assets 403 an absent User-Agent.
        $client.Headers.Add('User-Agent', 'agent8088-installer')
        if ($Proxy) { $client.Proxy = $Proxy }

        $task = $client.DownloadFileTaskAsync($Uri, $OutFile)
        if ($task.Wait($TimeoutSec * 1000)) {
            if ($task.IsFaulted) {
                $result.Error = $task.Exception.GetBaseException().Message
            } else {
                $result.Success = $true
            }
        } else {
            $result.TimedOut = $true
            try { $client.CancelAsync() } catch { }
        }
    } catch {
        $result.Error = $_.Exception.Message
    } finally {
        if ($client) { try { $client.Dispose() } catch { } }
    }

    # A cancelled or faulted transfer leaves a truncated file. Removing it matters:
    # every caller's next step is Expand-Archive or a self-extractor, and a partial
    # archive fails there with a corruption error that names the wrong cause.
    if (-not $result.Success) {
        Remove-Item -Force -ErrorAction SilentlyContinue $OutFile
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

# Warn about a stage that did not complete, naming a hang as a hang. "timed out
# after 10m" and "failed" point at different fixes. -Fix is the command that
# repairs it, surfaced again in the final summary.
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
# Require a modern Windows terminal host before installation. Windows
# PowerShell 5.1 itself remains supported when hosted by Windows Terminal or
# VS Code; the unsupported component is the legacy Windows Console Host.
# ----------------------------------------------------------------------------
function Get-WindowsTerminalPackage {
    try {
        return Get-AppxPackage -Name Microsoft.WindowsTerminal -ErrorAction Stop |
            Sort-Object { [version]$_.Version } -Descending |
            Select-Object -First 1
    } catch {
        return $null
    }
}

function Test-SupportedTerminalHost {
    if ($env:TERM_PROGRAM -eq "vscode") { return $true }
    if (-not $env:WT_SESSION) { return $false }

    $package = Get-WindowsTerminalPackage
    return ($package -and ([version]$package.Version -ge $WindowsTerminalMinVersion))
}

function Install-WindowsTerminal {
    param($ExistingPackage)

    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $winget) {
        Write-Err "WinGet is required to install Windows Terminal automatically."
        Write-Info "Install App Installer from Microsoft Store, then re-run this installer."
        Write-Info "https://aka.ms/getwinget"
        return $false
    }

    $operation = if ($ExistingPackage) { "upgrade" } else { "install" }
    Write-Info "$($operation.Substring(0, 1).ToUpper())$($operation.Substring(1))ing Windows Terminal..."
    & $winget.Source $operation --id Microsoft.WindowsTerminal --exact --source winget `
        --accept-source-agreements --accept-package-agreements --disable-interactivity | Out-Host
    $wingetExit = $LASTEXITCODE

    $package = Get-WindowsTerminalPackage
    if ($package -and ([version]$package.Version -ge $WindowsTerminalMinVersion)) {
        Write-Success "Windows Terminal $($package.Version) is ready"
        return $true
    }

    Write-Err "Windows Terminal installation did not reach version $WindowsTerminalMinVersion (WinGet exit $wingetExit)."
    return $false
}

function ConvertTo-PowerShellLiteral {
    param([AllowNull()][string]$Value)
    return "'" + ([string]$Value).Replace("'", "''") + "'"
}

function Start-InstallerInWindowsTerminal {
    $package = Get-WindowsTerminalPackage
    $terminalCommand = Get-Command wt.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $terminalExe = if ($terminalCommand) { $terminalCommand.Source } else { $null }
    if (-not $terminalExe -and $package.InstallLocation) {
        $candidate = Join-Path $package.InstallLocation "WindowsTerminal.exe"
        if (Test-Path $candidate) { $terminalExe = $candidate }
    }
    if (-not $terminalExe) {
        Write-Err "Windows Terminal is installed, but its executable could not be found."
        Write-Info "Enable the 'wt.exe' App execution alias in Windows Settings, then re-run this installer."
        return $false
    }

    $launcherPath = Join-Path ([IO.Path]::GetTempPath()) ("agent8088-install-{0}.ps1" -f [guid]::NewGuid())
    $branchLiteral = ConvertTo-PowerShellLiteral $Branch
    $homeLiteral = ConvertTo-PowerShellLiteral $Agent8088Home
    $installLiteral = ConvertTo-PowerShellLiteral $InstallDir
    $skipSetupLiteral = if ($SkipSetup) { '$true' } else { '$false' }

    if ($PSCommandPath -and (Test-Path $PSCommandPath)) {
        $scriptLiteral = ConvertTo-PowerShellLiteral $PSCommandPath
        $installCommand = "& $scriptLiteral -Branch $branchLiteral -Agent8088Home $homeLiteral -InstallDir $installLiteral -SkipSetup`:$skipSetupLiteral"
    } else {
        $escapedBranch = [Uri]::EscapeDataString($Branch).Replace('%2F', '/')
        $installerUrl = "https://raw.githubusercontent.com/tayyabimam1/AGENT8088/$escapedBranch/install.ps1"
        $urlLiteral = ConvertTo-PowerShellLiteral $installerUrl
        $installCommand = "`$source = Invoke-RestMethod -Uri $urlLiteral`r`n& ([scriptblock]::Create(`$source)) -Branch $branchLiteral -Agent8088Home $homeLiteral -InstallDir $installLiteral -SkipSetup`:$skipSetupLiteral"
    }

    @"
Remove-Item -LiteralPath `$PSCommandPath -Force -ErrorAction SilentlyContinue
$installCommand
"@ | Set-Content -LiteralPath $launcherPath -Encoding UTF8

    $powerShellExe = Get-PowerShellHostExe
    $terminalArgs = @(
        "-w", "new", "`"$powerShellExe`"", "-NoExit", "-ExecutionPolicy", "Bypass",
        "-File", "`"$launcherPath`""
    )
    try {
        Start-Process -FilePath $terminalExe -ArgumentList $terminalArgs | Out-Null
        Write-Success "Continuing installation in Windows Terminal..."
        return $true
    } catch {
        Remove-Item -LiteralPath $launcherPath -Force -ErrorAction SilentlyContinue
        Write-Err "Could not launch Windows Terminal: $_"
        return $false
    }
}

function Ensure-SupportedTerminal {
    if (Test-SupportedTerminalHost) { return "continue" }

    $package = Get-WindowsTerminalPackage
    $versionLabel = if ($package) { $package.Version } else { "not installed" }
    Write-Warn "This terminal host is not supported by Agent8088."
    Write-Host "  Windows Terminal required: $WindowsTerminalMinVersion or newer"
    Write-Host "  Windows Terminal detected: $versionLabel"

    if (-not $package -or ([version]$package.Version -lt $WindowsTerminalMinVersion)) {
        if ($NonInteractive) {
            Write-Err "Interactive confirmation is required to install or update Windows Terminal."
            return "failed"
        }
        do {
            $answer = (Read-Host "Install/update Windows Terminal and continue? [y/n]").Trim().ToLowerInvariant()
        } while ($answer -notin @("y", "n"))
        if ($answer -eq "n") {
            Write-Info "Installation cancelled. Agent8088 was not installed."
            return "failed"
        }
        if (-not (Install-WindowsTerminal $package)) { return "failed" }
    }

    if (-not (Start-InstallerInWindowsTerminal)) { return "failed" }
    # The replacement installer owns the remaining work; stop this legacy-host run.
    return "relaunched"
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
function Wait-ForPendingUninstall {
    $pendingMarker = "${Agent8088Home}.uninstall-pending"
    if (-not (Test-Path -LiteralPath $pendingMarker)) { return $true }

    # Once the live directory has been moved aside, a new install cannot race
    # the helper because cleanup is restricted to its unique quarantine path.
    if (-not (Test-Path -LiteralPath $Agent8088Home)) {
        Remove-Item -LiteralPath $pendingMarker -Force -ErrorAction SilentlyContinue
        return $true
    }

    Write-Info "Waiting for a previous Agent8088 uninstall to finish..."
    $deadline = (Get-Date).AddSeconds($PendingUninstallWaitSeconds)
    while ((Test-Path -LiteralPath $pendingMarker) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path -LiteralPath $pendingMarker)) { return $true }

    $cleanupLog = Get-Content -LiteralPath $pendingMarker -Raw -ErrorAction SilentlyContinue
    Write-Err "A previous Agent8088 uninstall has not completed."
    Write-Err "Close every Agent8088 session, then run the installer again."
    if ($cleanupLog) { Write-Err "Cleanup log: $($cleanupLog.Trim())" }
    return $false
}

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
        $bootResult = Invoke-WithTimeout -FilePath $psHostExe `
            -Arguments @("-ExecutionPolicy", "ByPass", "-c",
                         "irm https://astral.sh/uv/install.ps1 | iex") `
            -TimeoutSec $TUvBoot
        if ($bootResult.TimedOut) {
            Write-Err "The uv installer timed out after $([int]($TUvBoot / 60))m"
            Write-Info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
            return $false
        }
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
        $pyResult = Invoke-WithTimeout -FilePath $script:UvCmd `
            -Arguments @("python", "install", $PythonVersion) -TimeoutSec $TVenv
        if ($pyResult.TimedOut) {
            Write-Err "Downloading Python $PythonVersion timed out after $([int]($TVenv / 60))m"
            Write-Warn 'On a slow connection, rerun with: $env:AGENT8088_TIMEOUT_SCALE = 3'
        }
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
        $dl = Invoke-BoundedDownload -Uri $downloadUrl -OutFile $tmpFile `
                -TimeoutSec $TDownload -Proxy $script:ResolvedProxy
        if (-not $dl.Success) {
            $why = if ($dl.TimedOut) { "timed out after $([int]($TDownload / 60))m" } else { $dl.Error }
            throw "Downloading $assetName failed: $why"
        }

        if (Test-Path $gitDir) { Remove-Item -Recurse -Force $gitDir }
        New-Item -ItemType Directory -Path $gitDir -Force | Out-Null

        if ($downloadIsZip) {
            Expand-Archive -Path $tmpFile -DestinationPath $gitDir -Force
        } else {
            # PortableGit is a self-extracting 7z archive.
            Write-Info "Extracting PortableGit to $gitDir ..."
            # Start-Process -Wait has no time limit, so a self-extractor stuck on
            # an AV-locked file hung the install here. Invoke-WithTimeout quotes
            # each argument itself, so "-o$gitDir" must NOT be pre-quoted -- doing
            # so double-quotes it on Windows PowerShell 5.1.
            $extract = Invoke-WithTimeout -FilePath $tmpFile `
                -Arguments @("-o$gitDir", "-y") -TimeoutSec $TExtract
            if ($extract.TimedOut) {
                throw "PortableGit extraction timed out after $([int]($TExtract / 60))m"
            }
            if ($extract.ExitCode -ne 0) {
                throw "PortableGit extraction failed (exit code $($extract.ExitCode))"
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
function Remove-IncompleteInstallDirectory {
    if (-not (Test-Path -LiteralPath $InstallDir)) { return $true }

    $lastError = $null
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction Stop
            if (-not (Test-Path -LiteralPath $InstallDir)) { return $true }
        } catch {
            $lastError = $_
        }
        if ($attempt -lt 10) { Start-Sleep -Seconds 1 }
    }

    Write-Err "Could not remove the incomplete installation at $InstallDir."
    Write-Err "Close every Agent8088 session, then run the installer again."
    if ($lastError) { Write-Err "Locked-file error: $lastError" }
    return $false
}

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
        try {
            Move-Item -LiteralPath $InstallDir -Destination $backupDir -ErrorAction Stop
        } catch {
            Write-Err "Could not move the interrupted checkout: $_"
            return $false
        }
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
                if ($LASTEXITCODE -ne 0) {
                    Write-Err "Could not stash local installation changes."
                    return $false
                }
            }
            & git -c windows.appendAtomically=false remote set-url origin $RepoUrl 2>$null
            if ($LASTEXITCODE -ne 0) { Write-Err "Could not configure the Agent8088 remote."; return $false }
            & git -c windows.appendAtomically=false fetch --depth 1 origin $Branch 2>$null
            if ($LASTEXITCODE -ne 0) { Write-Err "Could not fetch '$Branch' from the Agent8088 remote."; return $false }
            & git -c windows.appendAtomically=false checkout -B $Branch FETCH_HEAD 2>$null | Out-Host
            if ($LASTEXITCODE -ne 0) { Write-Err "Could not check out '$Branch'."; return $false }
            & git -c windows.appendAtomically=false reset --hard FETCH_HEAD 2>$null | Out-Host
            if ($LASTEXITCODE -ne 0) { Write-Err "Could not reset the installation to '$Branch'."; return $false }
        } finally {
            Pop-Location
        }
    } else {
        Write-Info "Cloning Agent8088 repository..."
        if (-not (Remove-IncompleteInstallDirectory)) { return $false }
        New-Item -ItemType Directory -Path (Split-Path $InstallDir -Parent) -Force | Out-Null

        try {
            & git -c windows.appendAtomically=false clone --depth 1 --branch $Branch $RepoUrl $InstallDir | Out-Host
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $InstallDir ".git"))) {
                throw "git clone failed (exit $LASTEXITCODE)"
            }
            & git -C $InstallDir -c windows.appendAtomically=false config core.autocrlf false
            if ($LASTEXITCODE -ne 0) { throw "git config failed (exit $LASTEXITCODE)" }
        } catch {
            # ZIP fallback: GitHub archive. Then git init so future updates work.
            Write-Warn "git clone failed; falling back to ZIP archive: $_"
            if (-not (Remove-IncompleteInstallDirectory)) { return $false }
            try {
                $zipUrl = "https://github.com/tayyabimam1/AGENT8088/archive/refs/heads/$Branch.zip"
                $tmpZip = "$env:TEMP\agent8088-$Branch.zip"
                $dl = Invoke-BoundedDownload -Uri $zipUrl -OutFile $tmpZip `
                        -TimeoutSec $TDownload -Proxy $script:ResolvedProxy
                if (-not $dl.Success) {
                    $why = if ($dl.TimedOut) { "timed out after $([int]($TDownload / 60))m" } else { $dl.Error }
                    throw "ZIP fallback download failed: $why"
                }
                $tmpExtract = "$env:TEMP\agent8088-extract"
                if (Test-Path $tmpExtract) { Remove-Item -Recurse -Force $tmpExtract -ErrorAction Stop }
                Expand-Archive -Path $tmpZip -DestinationPath $tmpExtract -Force -ErrorAction Stop
                $extractedDir = Get-ChildItem $tmpExtract -Directory | Select-Object -First 1
                if (-not $extractedDir) { throw "Downloaded archive did not contain a repository directory" }
                Move-Item $extractedDir.FullName $InstallDir -ErrorAction Stop
                Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $tmpExtract -ErrorAction SilentlyContinue

                # Re-init so future `agent8088 update` works
                & git -C $InstallDir init 2>$null | Out-Host
                if ($LASTEXITCODE -ne 0) { throw "git init failed" }
                & git -C $InstallDir -c windows.appendAtomically=false config core.autocrlf false
                & git -C $InstallDir remote add origin $RepoUrl 2>$null
                & git -C $InstallDir fetch --depth 1 origin $Branch 2>$null
                if ($LASTEXITCODE -ne 0) { throw "git fetch after ZIP fallback failed" }
                & git -C $InstallDir checkout -t origin/$Branch 2>$null | Out-Host
                if ($LASTEXITCODE -ne 0) { throw "git checkout after ZIP fallback failed" }
            } catch {
                Write-Err "Could not prepare the Agent8088 repository: $_"
                return $false
            }
        }
        $script:FreshInstall = $true
    }
    $installedCommit = (& git -C $InstallDir rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $installedCommit) {
        Write-Err "Repository verification failed at $InstallDir."
        return $false
    }
    Write-Success "Repository ready at $InstallDir ($Branch@$installedCommit)"
    return $true
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
        $venvResult = Invoke-WithTimeout -FilePath $script:UvCmd `
            -Arguments @("venv", "--python", $script:PythonVersion, "--allow-existing", $venvDir) `
            -TimeoutSec $TVenv
        if ($venvResult.TimedOut -or $venvResult.ExitCode -ne 0 -or -not (Test-Path $py)) {
            # A venv from a Python that has since gone, or a half-written one
            # from an interrupted run, cannot be reused. Rebuild it rather than
            # handing the user a decision they have no way to evaluate.
            Write-Warn "Existing virtualenv is not usable - rebuilding it"
            $venvResult = Invoke-WithTimeout -FilePath $script:UvCmd `
                -Arguments @("venv", "--python", $script:PythonVersion, "--clear", $venvDir) `
                -TimeoutSec $TVenv
            if ($venvResult.TimedOut) {
                throw "venv creation timed out after $([int]($TVenv / 60))m (uv may be downloading a Python build)"
            }
            if ($venvResult.ExitCode -ne 0 -or -not (Test-Path $py)) {
                Write-Err "Run this to see the underlying error:"
                Write-Err "  $script:UvCmd venv --python $script:PythonVersion --clear $venvDir"
                Write-Err "If it keeps failing, remove the install and start clean: agent8088 --uninstall"
                throw "venv creation failed (uv exit $($venvResult.ExitCode))"
            }
        }
        # This is the stage that actually hangs: playwright's and ddgs's native
        # wheels plus mcp and Pillow. Mandatory, so a timeout is a hard failure
        # with a specific message rather than a skip.
        $coreResult = Invoke-WithTimeout -FilePath $script:UvCmd `
            -Arguments @("pip", "install", "--python", $py,
                         "--reinstall-package", "agent8088", "-e", $InstallDir) `
            -TimeoutSec $TCoreInstall
        $ErrorActionPreference = $prevEAP
        if ($coreResult.TimedOut) {
            Write-Err "uv pip install timed out after $([int]($TCoreInstall / 60))m - a package download stalled."
            Write-Err 'Retry on a slower link with: $env:AGENT8088_TIMEOUT_SCALE = 3'
            Write-Err "Or see the underlying error with:"
            Write-Err "  $script:UvCmd pip install --python `"$py`" -e `"$InstallDir`""
            throw "uv pip install timed out"
        }
        if ($coreResult.ExitCode -ne 0) {
            Write-Err "uv pip install failed (exit $($coreResult.ExitCode))"
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
                -What "Gateway adapter extras" `
                -Consequence "Slack/Discord/Telegram adapters unavailable" `
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
                -What "Keyless web search backend (ddgs)" `
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
                -What "Playwright" `
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
            $dl = Invoke-BoundedDownload -Uri $downloadUrl -OutFile $tmpFile `
                    -TimeoutSec $TDownload -Proxy $script:ResolvedProxy
            if (-not $dl.Success) {
                # Node is optional (WhatsApp bridge only), so warn rather than throw.
                Write-StageWarning -Result @{ ExitCode = -1; TimedOut = $dl.TimedOut } `
                    -TimeoutSec $TDownload -What "Node.js download" `
                    -Consequence "WhatsApp bridge unavailable" `
                    -Fix "rerun the installer"
                return
            }
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

    # MAX_PATH is 260 unless LongPathsEnabled is on (Windows 10 1607+, off by
    # default). The bridge sits about 120 characters deep before node_modules
    # starts nesting, and npm trees routinely add 150+, so without this the
    # install fails partway with ENAMETOOLONG or EPERM and the error names a
    # package rather than the cause. A flat tree is cheaper and far likelier to
    # succeed than asking for a registry change that needs admin and a reboot.
    $longPathsEnabled = 0
    try {
        $longPathsEnabled = (Get-ItemProperty `
            -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
            -Name 'LongPathsEnabled' -ErrorAction Stop).LongPathsEnabled
    } catch { $longPathsEnabled = 0 }

    $npmExtraArgs = @()
    if ($longPathsEnabled -ne 1) {
        Write-Info "Long paths are disabled; installing the bridge with a flat node_modules tree."
        $npmExtraArgs += "--install-strategy=hoisted"
        if ($bridgeDir.Length -gt 150) {
            Write-Warn "Bridge path is $($bridgeDir.Length) chars - npm may still hit the 260-char limit."
            Write-Warn "Enable long paths (admin, one time), then rerun the installer:"
            Write-Warn "  Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' LongPathsEnabled 1"
        }
    }

    Write-Info "Installing WhatsApp bridge npm dependencies..."
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $npmResult = Invoke-WithTimeout -FilePath $npmExe `
            -Arguments (@("install", "--prefix", $bridgeDir, "--no-audit", "--no-fund") + $npmExtraArgs) `
            -TimeoutSec $TNpm
        if ($npmResult.ExitCode -eq 0 -and (Test-Path $nodeModules)) {
            $script:WhatsAppBridgeReady = $true
            Write-Success "WhatsApp bridge npm dependencies installed"
        } elseif ($npmResult.ExitCode -eq 0) {
            Write-Warn "WhatsApp bridge npm install reported success but node_modules missing"
            Register-SkippedStage -Label "WhatsApp bridge" `
                -Reason "npm reported success but node_modules missing" `
                -Fix "cd `"$bridgeDir`"; npm install"
        } else {
            Write-StageWarning -Result $npmResult -TimeoutSec $TNpm `
                -What "WhatsApp bridge npm dependencies" `
                -Consequence "WhatsApp adapter unavailable" `
                -Fix "cd `"$bridgeDir`"; npm install"
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
        return
    }
    # `ollama list` talks to the daemon on :11434. It answers instantly when that
    # daemon is healthy and never when it is wedged, which is why a local call is
    # guarded at all.
    $listResult = Invoke-WithTimeout -FilePath $ollama.Source `
        -Arguments @("list") -TimeoutSec $TOllamaCheck -CaptureOutput
    $installed = $false
    if ($listResult.ExitCode -eq 0 -and $listResult.Output -match "(?m)^$([regex]::Escape($EmbedModel))") {
        $installed = $true
    }
    if ($installed) {
        Write-Success "Embedding model $EmbedModel already present"
        return
    }
    Write-Info "Pulling embedding model $EmbedModel (274 MB, for memory recall)..."
    $pullResult = Invoke-WithTimeout -FilePath $ollama.Source `
        -Arguments @("pull", $EmbedModel) -TimeoutSec $TOllamaPull
    if ($pullResult.ExitCode -eq 0) {
        Write-Success "Embedding model $EmbedModel installed"
    } else {
        Write-StageWarning -Result $pullResult -TimeoutSec $TOllamaPull `
            -What "Embedding model $EmbedModel" `
            -Consequence "memory recall will use keyword search only" `
            -Fix "ollama pull $EmbedModel"
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
# Stage 6: Install the Windows command launcher
# ----------------------------------------------------------------------------
function Write-Agent8088Launcher {
    param([string]$AgentExe = (Join-Path $InstallDir "venv\Scripts\agent8088.exe"))

    if (-not (Test-Path -LiteralPath $AgentExe)) {
        Write-Err "Cannot create the Agent8088 launcher; executable not found: $AgentExe"
        return $false
    }

    New-Item -ItemType Directory -Path $LauncherDir -Force | Out-Null
    $launcher = Join-Path $LauncherDir "agent8088.cmd"
    $content = @"
@echo off
setlocal
set "AGENT8088_HOME=$Agent8088Home"
set "AGENT8088_LINK_DIR=$LauncherDir"
"$AgentExe" %*
set "_agent8088_exit=%ERRORLEVEL%"
if /I "%~1"=="--uninstall" goto agent8088_wait_for_uninstall
if /I "%~1"=="-uninstall" goto agent8088_wait_for_uninstall
exit /b %_agent8088_exit%

:agent8088_wait_for_uninstall
set "_agent8088_home=$Agent8088Home"
set "_agent8088_marker=${Agent8088Home}.uninstall-pending"
set "_agent8088_log="
set /a _agent8088_attempts=0
if exist "%_agent8088_marker%" set /p "_agent8088_log="<"%_agent8088_marker%"

:agent8088_wait_loop
if not exist "%_agent8088_marker%" goto agent8088_uninstall_finished
if defined _agent8088_log if exist "%_agent8088_log%" findstr /b /c:"FAILED:" "%_agent8088_log%" >nul && goto agent8088_uninstall_failed
set /a _agent8088_attempts+=1
if %_agent8088_attempts% GEQ 150 goto agent8088_uninstall_failed
>nul 2>&1 ping -n 2 127.0.0.1
goto agent8088_wait_loop

:agent8088_uninstall_finished
if exist "%_agent8088_home%" goto agent8088_uninstall_failed
if not "%_agent8088_exit%"=="0" goto agent8088_uninstall_failed
echo.
echo [OK] Agent8088 was completely uninstalled.
set "_agent8088_self=%~f0"
for %%I in ("%~dp0.") do set "_agent8088_launcher=%%~fI"
(goto) 2>nul & del /f /q "%_agent8088_self%" >nul 2>&1 & rd "%_agent8088_launcher%" >nul 2>&1

:agent8088_uninstall_failed
echo.
echo [X] Agent8088 uninstall did not complete.
if not "%_agent8088_exit%"=="0" echo The Agent8088 uninstaller exited with code %_agent8088_exit%.
if defined _agent8088_log if exist "%_agent8088_log%" type "%_agent8088_log%"
exit /b 1
"@
    [System.IO.File]::WriteAllText($launcher, $content, [System.Text.UTF8Encoding]::new($false))
    return $true
}

function Setup-Path {
    $venvScripts = Join-Path $InstallDir "venv\Scripts"
    $managedBin = Join-Path $Agent8088Home "bin"
    Write-Info "Installing Agent8088 command launcher at $LauncherDir..."
    if (-not (Write-Agent8088Launcher)) { return $false }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $userPathItems = if ($userPath) { $userPath -split ";" } else { @() }
    $userPathItems = @($userPathItems | Where-Object {
        $_ -and $_ -ne $venvScripts -and $_ -ne $managedBin -and $_ -ne $LauncherDir
    })
    $userPathItems += $LauncherDir
    [Environment]::SetEnvironmentVariable("Path", ($userPathItems -join ";"), "User")
    Write-Success "Agent8088 command launcher installed"
    # Session PATH so the rest of this run can find agent8088
    $env:Path = "$LauncherDir;$env:Path"
    return $true
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
        $script:ConfigCreated = $true
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
    Write-Host "  Update: `$env:AGENT8088_BRANCH = '$Branch'; iex (irm https://raw.githubusercontent.com/tayyabimam1/AGENT8088/$Branch/install.ps1)"
    Write-Host ""
    Write-Host "If 'agent8088' is not recognized, open a NEW terminal (PATH was updated)."
    # Last, so it is the final thing on screen: per-stage warnings scrolled out of
    # view minutes ago on a multi-minute install, which is how a failed WhatsApp
    # bridge got reported and still went unnoticed.
    Write-SkippedSummary
}

# Runs on EVERY invocation, not only on a fresh install.
#
# The removed gate was `-not $script:FreshInstall -and -not $script:ConfigCreated`,
# which skipped setup on any re-run over an existing install that already had a
# config. That is exactly the run where setup matters most: when an optional stage
# fails the core agent still installs, so the user re-runs the installer -- and got
# no prompt for working directory, model or web search, and no hint that
# `agent8088 --setup` is the thing to run.
#
# Two gates remain, and both are there because the prompt is physically impossible,
# not because it is unwanted: an explicit -SkipSetup, and a non-interactive host.
#
# Deliberately NOT wrapped in Invoke-WithTimeout: this is interactive and reads the
# console, so a wall clock here would kill the user mid-answer.
function Run-InitialSetup {
    if ($SkipSetup) {
        Write-Info "Skipping setup (-SkipSetup)"
        Write-Info "Configure later with: agent8088 --setup"
        return
    }
    if ($NonInteractive) {
        Write-Info "Non-interactive mode - skipping setup"
        Write-Info "Run agent8088 --setup later to configure your model."
        return
    }

    # Prefer the console script, fall back to the venv interpreter: a missing .exe
    # shim (a partial install, an AV quarantine) is not a reason to skip setup when
    # the module itself is right there and importable.
    $agentExe = Join-Path $InstallDir "venv\Scripts\agent8088.exe"
    $venvPy   = Join-Path $InstallDir "venv\Scripts\python.exe"
    if (Test-Path $agentExe) {
        Write-Info "Starting setup..."
        & $agentExe --setup
    } elseif (Test-Path $venvPy) {
        Write-Warn "agent8088.exe not found; running setup via the venv interpreter"
        & $venvPy -m agent8088.cli --setup
    } else {
        Write-Warn "agent8088 is not runnable yet; run agent8088 --setup later."
        Register-SkippedStage -Label "First-run setup" `
            -Reason "agent8088 not runnable" -Fix "agent8088 --setup"
        return
    }

    if ($LASTEXITCODE -eq 0) {
        $script:InitialSetupRan = $true
    } else {
        # Recorded, not just warned: on a multi-minute install this line scrolls out
        # of view, which is how a skipped setup went unnoticed.
        Write-Warn "Setup did not complete; run agent8088 --setup later."
        Register-SkippedStage -Label "First-run setup" `
            -Reason "did not complete (exit $LASTEXITCODE)" -Fix "agent8088 --setup"
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
$terminalAction = Ensure-SupportedTerminal
if ($terminalAction -eq "relaunched") { exit 0 }
if ($terminalAction -ne "continue") { exit 1 }
if (-not (Wait-ForPendingUninstall)) { exit 1 }
if (-not (Install-Uv)) { exit 1 }
if (-not (Test-Python)) { exit 1 }
if (-not (Install-Git)) { exit 1 }
if (-not (Clone-Repo)) { exit 1 }
Install-Deps
Install-Gateway-Extras
Install-Node-Bridge
Install-Embedding-Model
Install-Native-Sandbox
if (-not (Setup-Path)) { exit 1 }
Drop-Config
Run-InitialSetup
Verify-Install
Start-InitialAgent
