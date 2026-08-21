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
    [switch]$TerminalBootstrap,
    [string]$Branch = $(if ($env:AGENT8088_BRANCH) { $env:AGENT8088_BRANCH } else { "main" }),
    [string]$Agent8088Home = $(if ($env:AGENT8088_HOME) { $env:AGENT8088_HOME } else { "$env:LOCALAPPDATA\agent8088" }),
    [string]$InstallDir = "",
    [string]$InstallerSourceUrl = ""
)

# Record an installer result without killing the user's current PowerShell.
# TerminalBootstrap is an internal child process whose parent waits on its OS
# exit code, so only that dedicated child may call exit; the documented
# `iex (irm ...)` path always returns to its caller.
function Set-InstallerExitStatus {
    param([Parameter(Mandatory = $true)][int]$ExitCode)
    $global:LASTEXITCODE = $ExitCode
    if ($TerminalBootstrap) { exit $ExitCode }
}

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
    # The documented iex invocation runs inside the user's existing shell.
    # `exit 1` here would terminate that shell (and VS Code would report only
    # "terminal process terminated"), hiding the useful explanation above.
    Set-InstallerExitStatus -ExitCode 1
    return
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
$RepoSlug = "palindrome-rl/AGENT8088"
$RepoUrl = "https://github.com/$RepoSlug.git"
$PythonVersion = "3.11"
$PythonFallbackVersions = @("3.12", "3.10")
$script:PythonExecutable = $null
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
# npm-installs one small package (dsh-sandbox-windows-acl + koffi), then runs one
# short no-op probe to verify the sandbox actually starts - not a large download,
# but npm registry round-trips and the probe both count as network/process calls
# nothing here should be allowed to hang forever.
$TSandboxSetup = 180 * $TimeoutScale

# One-line activity display for quiet installation stages. The external tools
# still own their real progress output where they have one (notably git clone);
# this is only for commands whose output is intentionally captured. ASCII
# frames survive Windows PowerShell 5.1's legacy code pages, unlike the Unicode
# spinners used by the interactive Agent8088 UI.
function Test-InteractiveProgress {
    if ($env:AGENT8088_NO_PROGRESS -eq "1" -or $env:CI) { return $false }
    if ($env:AGENT8088_FORCE_PROGRESS -eq "1") { return $true }
    try {
        return (-not [Console]::IsOutputRedirected -and $null -ne $Host.UI -and $null -ne $Host.UI.RawUI)
    } catch {
        return $false
    }
}

function Start-InstallerActivity {
    param([Parameter(Mandatory = $true)][string]$Message)
    $clock = New-Object System.Diagnostics.Stopwatch
    $clock.Start()
    $state = @{
        Enabled    = [bool](Test-InteractiveProgress)
        Message    = $Message
        Frame      = 0
        LastLength = 0
        Clock      = $clock
    }
    if ($state.Enabled) { Update-InstallerActivity -State $state }
    return $state
}

function Update-InstallerActivity {
    param([Parameter(Mandatory = $true)][hashtable]$State)
    if (-not $State.Enabled) { return }
    $frames = @('|', '/', '-', '\')
    $frame = $frames[$State.Frame % $frames.Count]
    $State.Frame++
    $elapsed = [int][Math]::Floor($State.Clock.Elapsed.TotalSeconds)
    $line = "[$frame] $($State.Message) ($($elapsed)s)"
    $padding = ""
    if ($State.LastLength -gt $line.Length) {
        $padding = " " * ($State.LastLength - $line.Length)
    }
    try {
        Write-Host ("`r" + $line + $padding) -NoNewline -ForegroundColor Cyan
        $State.LastLength = $line.Length
    } catch {
        # Losing the terminal must never turn a successful install into a failure.
        $State.Enabled = $false
    }
}

function Stop-InstallerActivity {
    param([hashtable]$State)
    if ($null -eq $State) { return }
    try { $State.Clock.Stop() } catch { }
    if (-not $State.Enabled) { return }
    try {
        $width = [Math]::Max([int]$State.LastLength, 1)
        Write-Host ("`r" + (" " * $width) + "`r") -NoNewline
    } catch { }
}

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
        [switch]$CaptureOutput,
        [string]$Activity,
        # How long to wait for an exited child's pipes to reach EOF. Not a limit on
        # the work - the child is already gone by then - only on the flush, so it
        # is deliberately short and deliberately not one of the stage budgets.
        [int]$DrainSec = 10
    )

    $result = @{ ExitCode = -1; TimedOut = $false; Output = "" }
    $proc = $null
    $activityState = $null

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

        if ($Activity) { $activityState = Start-InstallerActivity -Message $Activity }
        $waitClock = New-Object System.Diagnostics.Stopwatch
        $waitClock.Start()
        $exited = $proc.HasExited
        while (-not $exited -and $waitClock.Elapsed.TotalSeconds -lt $TimeoutSec) {
            $remainingMs = [int](($TimeoutSec - $waitClock.Elapsed.TotalSeconds) * 1000)
            $sliceMs = [Math]::Min(125, [Math]::Max(1, $remainingMs))
            $exited = $proc.WaitForExit($sliceMs)
            if ($activityState) { Update-InstallerActivity -State $activityState }
        }
        $waitClock.Stop()

        if ($exited) {
            # Second wait, bounded: lets the async readers finish flushing before
            # the exit code is read (documented .NET requirement). It has to be
            # bounded, because both this wait and the read below finish on pipe
            # EOF rather than on the child exiting - and the child's own children
            # inherit those pipe handles. One surviving grandchild (`ollama list`
            # leaving a server behind, npm leaving a node) holds the write end
            # open, EOF never comes, and an argument-less WaitForExit or a
            # .GetResult() on the read task waits for it forever. That put an
            # unbounded wait inside the one function whose job is to bound them,
            # and hung the installer with no output at all - the message for the
            # stage comes after this returns.
            try { [void]$proc.WaitForExit($DrainSec * 1000) } catch { }
            $result.ExitCode = $proc.ExitCode
            if ($CaptureOutput) {
                try {
                    if ($outTask.Wait($DrainSec * 1000)) { $result.Output = $outTask.Result }
                } catch { }
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
        if ($activityState) { Stop-InstallerActivity -State $activityState }
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
        [System.Net.IWebProxy]$Proxy,
        [string]$Activity
    )

    $result = @{ Success = $false; TimedOut = $false; Error = "" }
    $client = $null
    $activityState = $null
    try {
        $client = New-Object System.Net.WebClient
        # GitHub release assets 403 an absent User-Agent.
        $client.Headers.Add('User-Agent', 'agent8088-installer')
        if ($Proxy) { $client.Proxy = $Proxy }

        $task = $client.DownloadFileTaskAsync($Uri, $OutFile)
        if ($Activity) { $activityState = Start-InstallerActivity -Message $Activity }
        $waitClock = New-Object System.Diagnostics.Stopwatch
        $waitClock.Start()
        while (-not $task.IsCompleted -and $waitClock.Elapsed.TotalSeconds -lt $TimeoutSec) {
            Start-Sleep -Milliseconds 125
            if ($activityState) { Update-InstallerActivity -State $activityState }
        }
        $waitClock.Stop()
        if ($task.IsCompleted) {
            if ($task.IsFaulted) {
                $result.Error = $task.Exception.GetBaseException().Message
            } elseif ($task.IsCanceled) {
                $result.Error = "download was cancelled"
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
        if ($activityState) { Stop-InstallerActivity -State $activityState }
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

# PortableGit, the Node zip, and the repo ZIP fallback each hit exactly one
# URL with no retry. GitHub and nodejs.org both throw intermittent 503s that a
# second attempt clears on its own; a timeout already burned its whole budget,
# so backoff is a short fixed delay, not exponential.
function Invoke-BoundedDownloadWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [Parameter(Mandatory = $true)][int]$TimeoutSec,
        [System.Net.IWebProxy]$Proxy,
        [string]$Activity,
        [int]$MaxAttempts = 3,
        [int]$BackoffSec = 2
    )
    $result = $null
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $result = Invoke-BoundedDownload -Uri $Uri -OutFile $OutFile -TimeoutSec $TimeoutSec `
            -Proxy $Proxy -Activity $Activity
        if ($result.Success) { return $result }
        if ($attempt -lt $MaxAttempts) {
            $why = if ($result.TimedOut) { "timed out" } elseif ($result.Error) { $result.Error } else { "failed" }
            Write-Warn "Download attempt $attempt/$MaxAttempts failed ($why) - retrying..."
            Start-Sleep -Seconds $BackoffSec
        }
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

# A valid archive that extracts without error but is missing its expected
# executable is the single most common "worked on my other PC, not this one"
# report - almost always antivirus quarantine, but the raw error
# ("node.exe not found after extraction") never names AV as the cause.
function Write-AntivirusGuidance {
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][string]$ExpectedPath,
        [Parameter(Mandatory = $true)][string]$QuarantineHint
    )
    Write-Warn "$What was downloaded and extracted, but $ExpectedPath is missing."
    Write-Warn "This is typically antivirus quarantine, not a bad download."
    Write-Warn "Add an exclusion for '$QuarantineHint' and re-run, or temporarily disable real-time protection."
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
    # An active WT_SESSION is stronger evidence than AppX registration. Portable
    # Terminal builds and temporarily stale per-user package registrations do not
    # appear in Get-AppxPackage; trying to "upgrade" them can close the terminal
    # that is running this installer and leave the user with no continuation.
    if (-not $package) { return $true }
    return ($package -and ([version]$package.Version -ge $WindowsTerminalMinVersion))
}

function Get-WindowsTerminalExecutable {
    param($Package = (Get-WindowsTerminalPackage))

    $terminalCommand = Get-Command wt.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($terminalCommand -and $terminalCommand.Source) { return $terminalCommand.Source }
    if ($Package -and $Package.InstallLocation) {
        $candidate = Join-Path $Package.InstallLocation "WindowsTerminal.exe"
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

# WinGet-less path for LTSC/LTSB or any machine with App Installer removed.
# Resolves the current version via the /releases/latest redirect - a plain
# github.com page redirect, not the 60-req/hr api.github.com endpoint - so the
# asset filename (which embeds the version) never needs to be hardcoded.
function Install-WindowsTerminalFromGitHubRelease {
    Write-Info "WinGet unavailable - trying Windows Terminal's GitHub release directly..."
    $tmpFile = $null
    try {
        $req = [System.Net.WebRequest]::Create("https://github.com/microsoft/terminal/releases/latest")
        $req.AllowAutoRedirect = $false
        $req.Method = "HEAD"
        if ($script:ResolvedProxy) { $req.Proxy = $script:ResolvedProxy }
        $resp = $req.GetResponse()
        $location = $resp.Headers["Location"]
        $resp.Close()
        if ($location -notmatch '/releases/tag/(v[\d.]+)') {
            Write-Warn "Could not resolve the latest Windows Terminal version from GitHub."
            return $false
        }
        $tag = $Matches[1]
        $ver = $tag.TrimStart('v')
        $assetName = "Microsoft.WindowsTerminal_${ver}_8wekyb3d8bbwe.msixbundle"
        $downloadUrl = "https://github.com/microsoft/terminal/releases/download/$tag/$assetName"
        $tmpFile = "$env:TEMP\$assetName"
        $dl = Invoke-BoundedDownloadWithRetry -Uri $downloadUrl -OutFile $tmpFile `
            -TimeoutSec $TDownload -Proxy $script:ResolvedProxy `
            -Activity "Downloading Windows Terminal $ver"
        if (-not $dl.Success) {
            $why = if ($dl.TimedOut) { "timed out" } else { $dl.Error }
            Write-Warn "Downloading Windows Terminal $ver failed: $why"
            return $false
        }
        Add-AppxPackage -Path $tmpFile -ErrorAction Stop
        return $true
    } catch {
        # Most common cause: a missing dependency package (VCLibs/UI.Xaml)
        # that WinGet resolves automatically but sideloading does not.
        Write-Warn "Could not sideload Windows Terminal: $_"
        Write-Info "Manual install: https://github.com/microsoft/terminal/releases/latest"
        return $false
    } finally {
        if ($tmpFile) { Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue }
    }
}

function Install-WindowsTerminal {
    param($ExistingPackage)

    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $winget) {
        if (Install-WindowsTerminalFromGitHubRelease) {
            $package = Get-WindowsTerminalPackage
            if ($package -and ([version]$package.Version -ge $WindowsTerminalMinVersion)) {
                Write-Success "Windows Terminal $($package.Version) is ready"
                return $true
            }
        }
        Write-Err "Could not install Windows Terminal automatically (no WinGet, and the GitHub fallback did not succeed)."
        Write-Info "Install App Installer from Microsoft Store for the WinGet path, then re-run this installer."
        Write-Info "https://aka.ms/getwinget"
        Write-Info "Or install Windows Terminal manually: https://aka.ms/terminal"
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

    # WinGet returns UPDATE_NOT_APPLICABLE when its inventory sees an installed
    # current package even if Get-AppxPackage cannot see that user's registration.
    # If the application alias is usable, launch it and let WT_SESSION prove the
    # handoff rather than turning a successful/no-op install into a hard failure.
    $updateNotApplicable = -1978335189  # 0x8A15002B
    $terminalExe = Get-WindowsTerminalExecutable -Package $package
    if (-not $package -and $terminalExe -and $wingetExit -in @(0, $updateNotApplicable)) {
        Write-Success "Windows Terminal is available at $terminalExe"
        return $true
    }

    Write-Err "Windows Terminal installation did not reach version $WindowsTerminalMinVersion (WinGet exit $wingetExit)."
    if ($wingetExit -eq $updateNotApplicable) {
        Write-Info "WinGet found no applicable update, but Windows Terminal is not registered for this user."
    }
    return $false
}

function ConvertTo-PowerShellLiteral {
    param([AllowNull()][string]$Value)
    return "'" + ([string]$Value).Replace("'", "''") + "'"
}

function ConvertTo-EncodedPowerShellCommand {
    param([Parameter(Mandatory = $true)][string]$Command)
    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
}

function Get-InstallerInvocation {
    param(
        [switch]$ForTerminalBootstrap,
        [switch]$PreferLocalScript
    )

    $branchLiteral = ConvertTo-PowerShellLiteral $Branch
    $homeLiteral = ConvertTo-PowerShellLiteral $Agent8088Home
    $installLiteral = ConvertTo-PowerShellLiteral $InstallDir
    $skipSetupLiteral = if ($SkipSetup) { '$true' } else { '$false' }
    $arguments = "-Branch $branchLiteral -Agent8088Home $homeLiteral -InstallDir $installLiteral -SkipSetup`:$skipSetupLiteral"
    if ($ForTerminalBootstrap) { $arguments += " -TerminalBootstrap" }

    if ($PreferLocalScript -and -not $InstallerSourceUrl -and $PSCommandPath -and (Test-Path -LiteralPath $PSCommandPath)) {
        $scriptLiteral = ConvertTo-PowerShellLiteral $PSCommandPath
        return "& $scriptLiteral $arguments"
    }

    $sourceUrl = $InstallerSourceUrl
    if (-not $sourceUrl) {
        $escapedBranch = [Uri]::EscapeDataString($Branch).Replace('%2F', '/')
        $sourceUrl = "https://raw.githubusercontent.com/$RepoSlug/$escapedBranch/install.ps1"
    }
    $urlLiteral = ConvertTo-PowerShellLiteral $sourceUrl
    $tlsCommand = "try { [Net.ServicePointManager]::SecurityProtocol = " +
                  "[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}"
    return ("$tlsCommand`r`n`$source = Invoke-RestMethod -Uri $urlLiteral`r`n" +
            "& ([scriptblock]::Create(`$source)) $arguments -InstallerSourceUrl $urlLiteral")
}

function Start-InstallerInWindowsTerminal {
    $package = Get-WindowsTerminalPackage
    $terminalExe = Get-WindowsTerminalExecutable -Package $package
    if (-not $terminalExe) {
        Write-Err "Windows Terminal is installed, but its executable could not be found."
        Write-Info "Enable the 'wt.exe' App execution alias in Windows Settings, then re-run this installer."
        return $false
    }

    $installCommand = Get-InstallerInvocation -PreferLocalScript
    $encodedCommand = ConvertTo-EncodedPowerShellCommand -Command $installCommand
    $powerShellExe = Get-PowerShellHostExe
    $terminalArgs = @(
        "-w", "new", "`"$powerShellExe`"", "-NoExit", "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", $encodedCommand
    )
    try {
        Start-Process -FilePath $terminalExe -ArgumentList $terminalArgs | Out-Null
        Write-Success "Continuing installation in Windows Terminal..."
        return $true
    } catch {
        Write-Err "Could not launch Windows Terminal: $_"
        return $false
    }
}

function Start-TerminalUpgradeBootstrap {
    $powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $powerShellExe)) { $powerShellExe = Get-PowerShellHostExe }

    # Run the actual installer in a child PowerShell. Its `exit 1` must not close
    # this visible bootstrap window before the user can read the error.
    $childCommand = Get-InstallerInvocation -ForTerminalBootstrap -PreferLocalScript
    $childEncoded = ConvertTo-EncodedPowerShellCommand -Command $childCommand
    $powerShellLiteral = ConvertTo-PowerShellLiteral $powerShellExe
    $childEncodedLiteral = ConvertTo-PowerShellLiteral $childEncoded
    $bootstrapCommand = @"
 try { `$Host.UI.RawUI.WindowTitle = 'Agent8088 Terminal Setup' } catch {}
 Write-Host 'Agent8088 is installing or updating Windows Terminal.' -ForegroundColor Cyan
 Write-Host 'This window will remain open and report whether installation continues.'
 & $powerShellLiteral -NoProfile -ExecutionPolicy Bypass -EncodedCommand $childEncodedLiteral
 `$installerExit = `$LASTEXITCODE
 if (`$installerExit -eq 0) {
     Write-Host ''
     Write-Host '[OK] Windows Terminal is ready. Agent8088 installation is continuing in the new window.' -ForegroundColor Green
     Write-Host 'You may close this bootstrap window.'
 } else {
     Write-Host ''
     Write-Host "[X] Agent8088 installation could not continue (exit `$installerExit)." -ForegroundColor Red
     Write-Host 'Review the error above, then press Enter to close this window.'
     [void](Read-Host)
 }
"@
    $bootstrapEncoded = ConvertTo-EncodedPowerShellCommand -Command $bootstrapCommand
    $conhostExe = Join-Path $env:SystemRoot "System32\conhost.exe"
    try {
        if (Test-Path -LiteralPath $conhostExe) {
            $bootstrapArgs = @(
                "`"$powerShellExe`"", "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", $bootstrapEncoded
            )
            Start-Process -FilePath $conhostExe -ArgumentList $bootstrapArgs | Out-Null
        } else {
            Start-Process -FilePath $powerShellExe -ArgumentList @(
                "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", $bootstrapEncoded
            ) | Out-Null
        }
        Write-Success "Windows Terminal setup opened in a separate window. Follow its progress there."
        return $true
    } catch {
        Write-Err "Could not open the Windows Terminal setup window: $_"
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
        if (-not $TerminalBootstrap) {
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
            if (-not (Start-TerminalUpgradeBootstrap)) { return "failed" }
            return "relaunched"
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
# Pre-flight checks -- run once at the very start of Main, before any stage
# ----------------------------------------------------------------------------

# The full install (Python, playwright/chromium, node_modules, ollama model)
# needs roughly 3-4 GB. Left unchecked, a full disk fails partway through with
# a cryptic error from pip or npm that never names the actual cause.
function Test-DiskSpace {
    param([int]$MinimumGB = 4)
    try {
        $root = [System.IO.Path]::GetPathRoot($Agent8088Home)
        $driveLetter = $root.TrimEnd('\', '/').TrimEnd(':')
        if (-not $driveLetter) { return $true }
        $drive = Get-PSDrive -Name $driveLetter -ErrorAction Stop
        $freeGB = [math]::Round($drive.Free / 1GB, 1)
        if ($drive.Free -lt ($MinimumGB * 1GB)) {
            Write-Err "Only $freeGB GB free on $root - Agent8088 needs about $MinimumGB GB"
            Write-Err "(Python, Playwright/Chromium, node_modules, and the embedding model)."
            Write-Err "Free up space or install to a different drive with -Agent8088Home, then re-run."
            return $false
        }
        return $true
    } catch {
        # A drive type Get-PSDrive can't report on (rare). Don't block the
        # install over a check that itself failed to run.
        return $true
    }
}

# MAX_PATH is 260 unless this registry value is set (Windows 10 1607+, off by
# default). Checked once up front so Install-Node-Bridge can read
# $script:LongPathsEnabled instead of querying the registry itself.
function Test-LongPathsRegistryEnabled {
    try {
        return ((Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
            -Name 'LongPathsEnabled' -ErrorAction Stop).LongPathsEnabled -eq 1)
    } catch {
        return $false
    }
}

function Show-LongPathWarningIfNeeded {
    # No warning here: Install-Node-Bridge already falls back to a flattened
    # dependency tree when this is off, and only warns if that fallback is
    # itself at risk (path still too long) or actually fails.
    $script:LongPathsEnabled = Test-LongPathsRegistryEnabled
}

# Hosts this installer downloads from, probed once up front. A short TCP
# connect that fails names the actual unreachable host immediately, instead of
# letting the user wait out a multi-minute download timeout with no way to
# tell a dead network from a slow one.
$script:InstallerRequiredHosts = @(
    @{ Name = "astral.sh"; Required = $true },
    @{ Name = "github.com"; Required = $true },
    @{ Name = "nodejs.org"; Required = $false },
    @{ Name = "registry.npmjs.org"; Required = $false }
)

function Test-TcpConnectivity {
    param([string]$HostName, [int]$Port = 443, [int]$TimeoutMs = 5000)
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $connectTask = $client.ConnectAsync($HostName, $Port)
        if (-not $connectTask.Wait($TimeoutMs)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        if ($client) { try { $client.Dispose() } catch { } }
    }
}

function Test-HostConnectivity {
    # A configured proxy means WinHTTP/the proxy resolves routing; a raw TCP
    # connect straight to the target host bypasses that and can fail even
    # though a proxied download (Invoke-BoundedDownload honors -Proxy) would
    # succeed. Skip the direct probe there rather than report a false failure.
    if ($script:ResolvedProxy) { return }

    $unreachable = @($script:InstallerRequiredHosts | Where-Object { -not (Test-TcpConnectivity -HostName $_.Name) })
    if ($unreachable.Count -eq 0) { return }

    foreach ($h in $unreachable) {
        $kind = if ($h.Required) { "required" } else { "optional" }
        Write-Warn "Cannot reach $($h.Name):443 ($kind) - check your firewall, VPN, or proxy (set HTTPS_PROXY)."
    }
    if (@($unreachable | Where-Object { $_.Required }).Count -gt 0) {
        Write-Warn "The install will likely hang or fail at a download step until this is resolved."
    }
}

# Rough throughput probe against this repo's own install.ps1 (a host we
# already trust and are about to talk to for uv). Bounded to 8s so a probe
# that itself stalls doesn't add a real delay to the install it's trying to
# speed up.
function Test-SlowConnection {
    param([int]$ThresholdBytesPerSec = 125000)  # ~1 Mbps
    if ($script:ResolvedProxy) { return $false }  # throughput through a proxy isn't representative of the direct probe path
    $tmp = $null
    try {
        $tmp = [System.IO.Path]::GetTempFileName()
        $probeUrl = "https://raw.githubusercontent.com/$RepoSlug/$Branch/install.ps1"
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $dl = Invoke-BoundedDownload -Uri $probeUrl -OutFile $tmp -TimeoutSec 8 -Proxy $script:ResolvedProxy
        $sw.Stop()
        if (-not $dl.Success) { return $false }
        $size = (Get-Item -LiteralPath $tmp -ErrorAction SilentlyContinue).Length
        if (-not $size -or $sw.Elapsed.TotalSeconds -le 0) { return $false }
        return (($size / $sw.Elapsed.TotalSeconds) -lt $ThresholdBytesPerSec)
    } catch {
        return $false
    } finally {
        if ($tmp) { Remove-Item -Force $tmp -ErrorAction SilentlyContinue }
    }
}

# ----------------------------------------------------------------------------
# Resume/checkpoint markers -- let a rerun after a failure skip heavy optional
# stages (gateway/search extras, Chromium) that already completed instead of
# redownloading them, so recovery is a quick rerun rather than starting over.
# ----------------------------------------------------------------------------
function Get-StageMarkerPath {
    param([Parameter(Mandatory = $true)][string]$Stage)
    Join-Path $Agent8088Home ".install-stages\$Stage.done"
}

function Test-StageComplete {
    param([Parameter(Mandatory = $true)][string]$Stage)
    Test-Path -LiteralPath (Get-StageMarkerPath $Stage)
}

function Set-StageComplete {
    param([Parameter(Mandatory = $true)][string]$Stage)
    $marker = Get-StageMarkerPath $Stage
    try {
        New-Item -ItemType Directory -Path (Split-Path $marker -Parent) -Force -ErrorAction Stop | Out-Null
        Set-Content -LiteralPath $marker -Value (Get-Date).ToString("o") -ErrorAction Stop
    } catch {
        # Silently swallowing this used to mean a rerun would look like resume
        # was broken - redownloading everything with no clue why - instead of
        # telling the user their $Agent8088Home permissions are the cause.
        Write-Warn "Could not save resume marker for '$Stage' ($_) - a rerun will redo this stage."
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
    $activityState = $null
    if (Get-Command Start-InstallerActivity -ErrorAction SilentlyContinue) {
        $activityState = Start-InstallerActivity -Message "Waiting for previous Agent8088 cleanup"
    }
    try {
        while ((Test-Path -LiteralPath $pendingMarker) -and (Test-Path -LiteralPath $Agent8088Home) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 125
            if ($activityState) { Update-InstallerActivity -State $activityState }
        }
    } finally {
        if ($activityState) { Stop-InstallerActivity -State $activityState }
    }
    if (-not (Test-Path -LiteralPath $pendingMarker)) { return $true }
    if (-not (Test-Path -LiteralPath $Agent8088Home)) {
        Remove-Item -LiteralPath $pendingMarker -Force -ErrorAction SilentlyContinue
        return $true
    }

    # The cleanup helper clears this marker on its way out whether it succeeded
    # or not, so a marker still sitting here means that helper died without
    # finishing. Waiting longer cannot help it, and refusing to install left
    # people with no way forward at all, so the stale marker goes and the
    # install proceeds over whatever the helper could not delete.
    $cleanupLog = Get-Content -LiteralPath $pendingMarker -Raw -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pendingMarker -Force -ErrorAction SilentlyContinue
    Write-Warn "A previous Agent8088 uninstall did not finish; installing over what it left behind."
    if ($cleanupLog) { Write-Warn "Cleanup log: $($cleanupLog.Trim())" }
    return $true
}

# Fallback for when astral.sh is unreachable or the bootstrap times out. Uses
# uv's own GitHub release via the /releases/latest/download/ redirect, which
# resolves the current version without hardcoding it or hitting the
# rate-limited api.github.com endpoint.
function Install-UvFromGitHubRelease {
    Write-Info "astral.sh install failed or timed out - trying uv's GitHub release as a fallback..."
    $tmpFile = $null
    $tmpExtract = $null
    try {
        $arch = Get-WindowsArch
        $assetName = switch ($arch) {
            "arm64" { "uv-aarch64-pc-windows-msvc.zip" }
            "x86"   { "uv-i686-pc-windows-msvc.zip" }
            default { "uv-x86_64-pc-windows-msvc.zip" }
        }
        $downloadUrl = "https://github.com/astral-sh/uv/releases/latest/download/$assetName"
        $tmpFile = "$env:TEMP\$assetName"
        $dl = Invoke-BoundedDownloadWithRetry -Uri $downloadUrl -OutFile $tmpFile `
            -TimeoutSec $TDownload -Proxy $script:ResolvedProxy -Activity "Downloading uv"
        if (-not $dl.Success) {
            $why = if ($dl.TimedOut) { "timed out" } else { $dl.Error }
            Write-Warn "GitHub release fallback for uv also failed: $why"
            return $false
        }
        $binDir = Join-Path $Agent8088Home "bin"
        New-Item -ItemType Directory -Path $binDir -Force | Out-Null
        $tmpExtract = "$env:TEMP\uv-extract-$(Get-Random)"
        Expand-Archive -Path $tmpFile -DestinationPath $tmpExtract -Force
        # The zip's internal layout (flat root vs. a uv-<target>/ subfolder)
        # has varied across releases; search rather than assume one shape.
        $uvExeFound = Get-ChildItem -Path $tmpExtract -Recurse -Filter "uv.exe" | Select-Object -First 1
        if (-not $uvExeFound) {
            Write-Warn "GitHub release archive did not contain uv.exe"
            return $false
        }
        Copy-Item $uvExeFound.FullName (Join-Path $binDir "uv.exe") -Force
        $uvxFound = Get-ChildItem -Path $tmpExtract -Recurse -Filter "uvx.exe" | Select-Object -First 1
        if ($uvxFound) { Copy-Item $uvxFound.FullName (Join-Path $binDir "uvx.exe") -Force }
        return (Test-Path (Join-Path $binDir "uv.exe"))
    } catch {
        Write-Warn "GitHub release fallback for uv failed: $_"
        return $false
    } finally {
        if ($tmpFile) { Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue }
        if ($tmpExtract) { Remove-Item -Recurse -Force $tmpExtract -ErrorAction SilentlyContinue }
    }
}

function Install-Uv {
    $managedUv = Join-Path $Agent8088Home "bin\uv.exe"

    if (Test-Path $managedUv) {
        # A corrupted/partial binary (an interrupted prior install, or AV that
        # quarantined it after this Test-Path but before the launch) can
        # throw a real Win32 launch exception here, not just a bad exit code.
        # Uncaught, that propagates out of the whole script with a raw stack
        # trace instead of a clean message - so treat "won't run" the same
        # as "not installed" and fall through to (re)installing it.
        try {
            $version = & $managedUv --version 2>$null
        } catch {
            $version = $null
        }
        if ($version) {
            $script:UvCmd = $managedUv
            Write-Success "Managed uv found ($version)"
            return $true
        }
        Write-Warn "Existing uv at $managedUv did not run - reinstalling"
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
            -TimeoutSec $TUvBoot -Activity "Installing uv"
        $ErrorActionPreference = $prevEAP
        if ($bootResult.TimedOut) {
            Write-Warn "The uv installer timed out after $([int]($TUvBoot / 60))m"
        }

        if (Test-Path $managedUv) {
            $script:UvCmd = $managedUv
            $version = & $managedUv --version
            Write-Success "Managed uv installed ($version)"
            return $true
        }

        # astral.sh was unreachable, or the bootstrap failed/timed out - try
        # uv's GitHub release directly before giving up.
        if (Install-UvFromGitHubRelease) {
            $script:UvCmd = $managedUv
            $version = & $managedUv --version
            Write-Success "Managed uv installed via GitHub release fallback ($version)"
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
                $script:PythonExecutable = $pythonPath
                return $true
            }
        } catch { }
    }

    Write-Info "Python not found, installing via uv..."
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $pyResult = Invoke-WithTimeout -FilePath $script:UvCmd `
            -Arguments @("python", "install", $PythonVersion) -TimeoutSec $TVenv `
            -Activity "Installing Python $PythonVersion"
        if ($pyResult.TimedOut) {
            Write-Err "Downloading Python $PythonVersion timed out after $([int]($TVenv / 60))m"
            Write-Warn 'On a slow connection, rerun with: $env:AGENT8088_TIMEOUT_SCALE = 3'
        }
        $ErrorActionPreference = $prevEAP
        $pythonPath = & $script:UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {
            $ver = & $pythonPath --version 2>$null
            Write-Success "Python installed: $ver"
            $script:PythonExecutable = $pythonPath
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
                    $script:PythonExecutable = $pythonSource
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
        $dl = Invoke-BoundedDownloadWithRetry -Uri $downloadUrl -OutFile $tmpFile `
                -TimeoutSec $TDownload -Proxy $script:ResolvedProxy `
                -Activity "Downloading PortableGit"
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
                -Arguments @("-o$gitDir", "-y") -TimeoutSec $TExtract `
                -Activity "Extracting PortableGit"
            if ($extract.TimedOut) {
                throw "PortableGit extraction timed out after $([int]($TExtract / 60))m"
            }
            if ($extract.ExitCode -ne 0) {
                throw "PortableGit extraction failed (exit code $($extract.ExitCode))"
            }
        }
        Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue

        $gitExe = "$gitDir\cmd\git.exe"
        if (-not (Test-Path $gitExe)) {
            Write-AntivirusGuidance -What "PortableGit" -ExpectedPath $gitExe -QuarantineHint $gitDir
            throw "Git extraction did not produce git.exe at $gitExe"
        }

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
    $activityState = $null
    if (Get-Command Start-InstallerActivity -ErrorAction SilentlyContinue) {
        $activityState = Start-InstallerActivity -Message "Removing incomplete Agent8088 installation"
    }
    try {
        for ($attempt = 1; $attempt -le 10; $attempt++) {
            try {
                Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction Stop
                if (-not (Test-Path -LiteralPath $InstallDir)) { return $true }
            } catch {
                $lastError = $_
            }
            if ($attempt -lt 10) {
                Start-Sleep -Seconds 1
                if ($activityState) { Update-InstallerActivity -State $activityState }
            }
        }
    } finally {
        if ($activityState) { Stop-InstallerActivity -State $activityState }
    }

    Write-Err "Could not remove the incomplete installation at $InstallDir."
    # Get-Process only sees processes whose own executable lives under
    # $InstallDir - it won't catch a process elsewhere holding a handle open
    # via a DLL or working directory - but that covers the common case
    # (a running agent8088.exe) without needing a handle.exe dependency.
    $blockers = @()
    try {
        $blockers = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like "$InstallDir*" })
    } catch { }
    if ($blockers.Count -gt 0) {
        Write-Err "These process(es) have files open inside $InstallDir :"
        foreach ($p in $blockers) { Write-Err "  $($p.ProcessName) (PID $($p.Id)) - $($p.Path)" }
        Write-Err "Close them, then run the installer again."
    } else {
        Write-Err "Close every Agent8088 session, then run the installer again."
    }
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
                $zipUrl = "https://github.com/$RepoSlug/archive/refs/heads/$Branch.zip"
                $tmpZip = "$env:TEMP\agent8088-$Branch.zip"
                $dl = Invoke-BoundedDownloadWithRetry -Uri $zipUrl -OutFile $tmpZip `
                        -TimeoutSec $TDownload -Proxy $script:ResolvedProxy `
                        -Activity "Downloading Agent8088 repository archive"
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
    if (-not $script:PythonExecutable) {
        throw "Failed to install agent8088: Python was detected but its executable path was not recorded"
    }
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
            -Arguments @("venv", "--python", $script:PythonExecutable, "--allow-existing", $venvDir) `
            -TimeoutSec $TVenv -Activity "Creating Agent8088 virtual environment"
        if ($venvResult.TimedOut -or $venvResult.ExitCode -ne 0 -or -not (Test-Path $py)) {
            # A venv from a Python that has since gone, or a half-written one
            # from an interrupted run, cannot be reused. Rebuild it rather than
            # handing the user a decision they have no way to evaluate.
            if (Test-Path -LiteralPath $venvDir) {
                Write-Warn "Existing virtualenv is not usable - rebuilding it"
            } else {
                Write-Warn "Initial virtualenv creation failed - retrying with a clean environment"
            }
            $venvResult = Invoke-WithTimeout -FilePath $script:UvCmd `
                -Arguments @("venv", "--python", $script:PythonExecutable, "--clear", $venvDir) `
                -TimeoutSec $TVenv -Activity "Rebuilding Agent8088 virtual environment"
            if ($venvResult.TimedOut) {
                throw "venv creation timed out after $([int]($TVenv / 60))m (uv may be downloading a Python build)"
            }
            if ($venvResult.ExitCode -ne 0 -or -not (Test-Path $py)) {
                Write-Err "Run this to see the underlying error:"
                Write-Err "  $script:UvCmd venv --python `"$script:PythonExecutable`" --clear `"$venvDir`""
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
            -TimeoutSec $TCoreInstall -Activity "Installing Agent8088 core dependencies"
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
        Register-SkippedStage -Label "Gateway/search extras + Chromium" `
            -Reason "venv python not found" `
            -Fix "re-run the installer so Install-Deps can rebuild the venv"
        return
    }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Each sub-step below is marked complete on success and skipped on a
        # later rerun. Without this, a rerun triggered by e.g. a failed
        # WhatsApp bridge step redid every pip install and the ~280 MB
        # Chromium download from scratch, turning any single failure into a
        # full redo instead of a 30-second resume.
        if (Test-StageComplete "gateway-extras") {
            $script:GatewayExtrasInstalled = $true
            Write-Success "Gateway adapters already installed (skipping)"
        } else {
            Write-Info "Installing gateway adapter dependencies (Slack, Discord, WhatsApp, Telegram)..."
            $gwResult = Invoke-WithTimeout -FilePath $script:UvCmd `
                -Arguments @("pip", "install", "--python", $py, "-e", "$InstallDir[gateway]") `
                -TimeoutSec $TPip -Activity "Installing gateway adapter dependencies"
            if ($gwResult.ExitCode -eq 0) {
                $script:GatewayExtrasInstalled = $true
                Set-StageComplete "gateway-extras"
                Write-Success "Gateway adapters installed"
            } else {
                Write-StageWarning -Result $gwResult -TimeoutSec $TPip `
                    -What "Gateway adapter extras" `
                    -Consequence "Slack/Discord/Telegram adapters unavailable" `
                    -Fix "$script:UvCmd pip install --python `"$py`" -e `"$InstallDir[gateway]`""
            }
        }

        # Keyless web search backend ([search] extra - see pyproject.toml).
        if (Test-StageComplete "search-extras") {
            $script:SearchExtrasInstalled = $true
            Write-Success "Keyless web search backend already installed (skipping)"
        } else {
            Write-Info "Installing keyless web search backend (ddgs)..."
            $searchResult = Invoke-WithTimeout -FilePath $script:UvCmd `
                -Arguments @("pip", "install", "--python", $py, "-e", "$InstallDir[search]") `
                -TimeoutSec $TPip -Activity "Installing keyless web search backend"
            if ($searchResult.ExitCode -eq 0) {
                $script:SearchExtrasInstalled = $true
                Set-StageComplete "search-extras"
                Write-Success "Keyless web search backend installed"
            } else {
                Write-StageWarning -Result $searchResult -TimeoutSec $TPip `
                    -What "Keyless web search backend (ddgs)" `
                    -Consequence "configure SearXNG or an API-key backend for web_search" `
                    -Fix "$script:UvCmd pip install --python `"$py`" -e `"$InstallDir[search]`""
            }
        }

        # Playwright is an optional [browser] extra, so install the package
        # before asking it to fetch the Chromium binary.
        if (Test-StageComplete "chromium") {
            $script:ChromiumInstalled = $true
            Write-Success "Chromium already installed (skipping)"
        } else {
            Write-Info "Installing Playwright (optional, for browse_page)..."
            $pwResult = Invoke-WithTimeout -FilePath $script:UvCmd `
                -Arguments @("pip", "install", "--python", $py, "-e", "$InstallDir[browser]") `
                -TimeoutSec $TPip -Activity "Installing Playwright"
            if ($pwResult.ExitCode -eq 0) {
                Write-Info "Installing Playwright Chromium browser (~280 MB)..."
                $chromiumResult = Invoke-WithTimeout -FilePath $py `
                    -Arguments @("-m", "playwright", "install", "chromium") `
                    -TimeoutSec $TChromium -Activity "Installing Playwright Chromium"
                if ($chromiumResult.ExitCode -eq 0) {
                    $script:ChromiumInstalled = $true
                    Set-StageComplete "chromium"
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
            # A corrupted/partial binary can throw a real launch exception,
            # not just print nothing - catch it the same way as the exit
            # code check just below, so it falls through to reinstalling
            # instead of taking down the whole script.
            try {
                $ver = & $managedNode --version 2>$null
            } catch {
                $ver = $null
            }
            if ($ver) {
                $nodeExe = $managedNode
                $npmExe = Join-Path $Agent8088Home "node\npm.cmd"
                Write-Success "Managed Node found ($ver)"
                # The fresh-install branch below adds $nodeDir to $env:Path so this
                # session's own child processes can find node - a resumed install
                # skipping straight to this branch needs the same thing, or the
                # later native-sandbox-setup step (which shells out to a fresh
                # agent8088.exe process) can't find node even though it's right here.
                $managedNodeDir = Split-Path $managedNode -Parent
                if (($env:Path -split ";") -notcontains $managedNodeDir) {
                    $env:Path = "$managedNodeDir;$env:Path"
                }
                $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
                $userPathItems = if ($userPath) { $userPath -split ";" } else { @() }
                if ($userPathItems -notcontains $managedNodeDir) {
                    $userPathItems += $managedNodeDir
                    [Environment]::SetEnvironmentVariable("Path", ($userPathItems -join ";"), "User")
                }
            } else {
                Write-Warn "Existing Node at $managedNode did not run - reinstalling"
            }
        }
    }

    if (-not $nodeExe) {
        $arch = Get-WindowsArch
        if ($arch -eq "x86") {
            # Node.js has published no 32-bit Windows builds since Node 10
            # (EOL 2021); mapping x86 to the x64 zip here used to silently
            # download a binary that can't run on the machine at all.
            Write-Warn "32-bit Windows detected - Node.js no longer publishes 32-bit Windows builds."
            Write-Info "The WhatsApp bridge is unavailable on 32-bit Windows."
            Register-SkippedStage -Label "WhatsApp bridge (Node.js)" `
                -Reason "32-bit Windows has no compatible Node.js build"
            return
        }
        Write-Info "Installing portable Node $NodeVersion into $Agent8088Home\node ..."
        $nodeArch = if ($arch -eq "arm64") { "arm64" } else { "x64" }
        $assetName = "node-v$NodeVersion-win-$nodeArch.zip"
        $downloadUrl = "https://nodejs.org/dist/v$NodeVersion/$assetName"
        $tmpFile = "$env:TEMP\$assetName"
        $nodeDir = "$Agent8088Home\node"

        try {
            $dl = Invoke-BoundedDownloadWithRetry -Uri $downloadUrl -OutFile $tmpFile `
                    -TimeoutSec $TDownload -Proxy $script:ResolvedProxy `
                    -Activity "Downloading portable Node.js"
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
            if (-not (Test-Path $nodeExe)) {
                Write-AntivirusGuidance -What "Node.js" -ExpectedPath $nodeExe -QuarantineHint $nodeDir
                throw "node.exe not found after extraction at $nodeExe"
            }

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
            Register-SkippedStage -Label "WhatsApp bridge (Node.js)" `
                -Reason "portable Node install failed: $_" `
                -Fix "install Node 20.11+ manually from https://nodejs.org/, then rerun"
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
    # Read once, up front in Show-LongPathWarningIfNeeded (Main), rather than
    # re-querying the registry here.
    $longPathsEnabled = if ($null -ne $script:LongPathsEnabled) { $script:LongPathsEnabled } else { Test-LongPathsRegistryEnabled }

    $npmExtraArgs = @()
    if (-not $longPathsEnabled) {
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
            -TimeoutSec $TNpm -Activity "Installing WhatsApp bridge dependencies"
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
        -Arguments @("pull", $EmbedModel) -TimeoutSec $TOllamaPull `
        -Activity "Pulling embedding model $EmbedModel"
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
# Stage 5d: Native sandbox runtime
# ----------------------------------------------------------------------------
# `agent8088 --sandbox-setup` now runs unattended during install.
#
# It used to be skipped deliberately: the runtime provisioned a restricted
# `srt-sandbox` account and spawned sandboxed children through
# CreateProcessWithLogonW, which on at least one machine was refused with
# ERROR_ACCESS_DENIED (no Security audit event, ruled out antivirus, arch
# mismatch, Node version, and a clean reprovision) - an upstream
# sandbox-runtime issue, not ours to fix, and not safe to run unattended
# because a failure there ended an otherwise successful install looking
# broken.
#
# `install_native_sandbox()` (engine.py) now provisions the DSH Windows ACL
# backend instead on win32: a restricted-token + NTFS ACL write-confinement
# scheme that duplicates the CURRENT user's own token rather than switching to
# a second account, so it never calls CreateProcessWithLogonW and needs no
# UAC prompt. Safe to run every install, same as the embedding-model pull
# above - bounded by Invoke-WithTimeout so a wedged npm registry or a stuck
# sandbox probe degrades to a skipped-stage warning instead of a hung
# installer.
#
# Docker remains the fallback whenever native isn't available or fails to
# verify - sandbox_status() picks it automatically, nothing here needs to
# arrange that.
function Install-Native-Sandbox {
    $agentExe = Join-Path $InstallDir "venv\Scripts\agent8088.exe"
    if (-not (Test-Path -LiteralPath $agentExe)) {
        Write-Warn "Agent8088 executable not found - skipping native sandbox setup"
        Register-SkippedStage -Label "Native sandbox" `
            -Reason "agent8088 executable not found" `
            -Fix "agent8088 --sandbox-setup"
        return
    }
    Write-Info "Setting up native sandbox..."
    $result = Invoke-WithTimeout -FilePath $agentExe `
        -Arguments @("--sandbox-setup") -TimeoutSec $TSandboxSetup -CaptureOutput `
        -Activity "Setting up native sandbox"
    if ($result.ExitCode -eq 0) {
        $script:SandboxInstalled = $true
        Write-Success "Native sandbox installed and verified"
    } else {
        $detail = if ($result.Output) { [string]$result.Output } elseif ($result.Error) { [string]$result.Error } else { "" }
        $detail = $detail.Trim()
        if ($detail.Length -gt 1000) { $detail = $detail.Substring(0, 1000) + "..." }
        if ($detail) { Write-Warn "Native sandbox details: $detail" }
        Write-StageWarning -Result $result -TimeoutSec $TSandboxSetup `
            -What "Native sandbox setup" `
            -Consequence "Docker will be used for sandboxing if available" `
            -Fix "agent8088 --sandbox-setup"
    }
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
    $homeLiteral = "'" + ([string]$Agent8088Home).Replace("'", "''") + "'"
    $markerLiteral = "'" + ([string]"${Agent8088Home}.uninstall-pending").Replace("'", "''") + "'"
    # The running executable has to exit before its final files can be removed,
    # so the launcher owns the last progress line.  Encode the waiter instead of
    # writing a temporary .ps1: machine execution policy cannot block
    # -EncodedCommand, and there is no script file that can disappear mid-run.
    $waitScript = @'
$ErrorActionPreference = 'SilentlyContinue'
$HomePath = __AGENT8088_HOME__
$MarkerPath = __AGENT8088_MARKER__
$LogPath = ''
if (Test-Path -LiteralPath $MarkerPath) {
  $LogPath = [string](Get-Content -LiteralPath $MarkerPath -ErrorAction SilentlyContinue | Select-Object -First 1)
  }
$Frames = @('|', '/', '-', '\')
$Interactive = $false
if ($env:AGENT8088_NO_PROGRESS -ne '1' -and -not $env:CI) {
  if ($env:AGENT8088_FORCE_PROGRESS -eq '1') {
    $Interactive = $true
  } else {
    try { $Interactive = -not [Console]::IsOutputRedirected } catch { }
  }
  }
$Clock = [Diagnostics.Stopwatch]::StartNew()
$Frame = 0
$LastLength = 0
$Announced = $false
while ((Test-Path -LiteralPath $HomePath) -and
       (Test-Path -LiteralPath $MarkerPath) -and
       $Clock.Elapsed.TotalSeconds -lt 60) {
  if ($Interactive) {
    $Glyph = $Frames[$Frame % $Frames.Count]
    $Line = "[$Glyph] Finishing Agent8088 cleanup... ($([int]$Clock.Elapsed.TotalSeconds)s)"
    $Padding = ' ' * [Math]::Max(0, $LastLength - $Line.Length)
    Write-Host ("`r" + $Line + $Padding) -NoNewline -ForegroundColor Cyan
    $LastLength = $Line.Length
    $Frame++
  } elseif (-not $Announced) {
    Write-Output 'Finishing Agent8088 cleanup...'
    $Announced = $true
  }
  Start-Sleep -Milliseconds 125
  }
$Clock.Stop()
if ($Interactive -and $LastLength -gt 0) {
  Write-Host ("`r" + (' ' * $LastLength) + "`r") -NoNewline
  }
if (-not (Test-Path -LiteralPath $HomePath)) {
  if ($Interactive) {
    Write-Host '[OK] Agent8088 was completely uninstalled.' -ForegroundColor Green
  } else {
    Write-Output '[OK] Agent8088 was completely uninstalled.'
  }
  exit 0
  }
if ($Interactive) {
  Write-Host '[X] Agent8088 uninstall did not remove every file.' -ForegroundColor Red
  } else {
    Write-Output '[X] Agent8088 uninstall did not remove every file.'
  }
if ($LogPath -and (Test-Path -LiteralPath $LogPath)) {
  Get-Content -LiteralPath $LogPath -ErrorAction SilentlyContinue
  }
Write-Output "Delete this folder by hand to finish: $HomePath"
exit 1
'@
    $waitScript = $waitScript.Replace('__AGENT8088_HOME__', $homeLiteral).Replace(
        '__AGENT8088_MARKER__', $markerLiteral
    )
    $waitEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($waitScript))
    $systemPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $systemPowerShell)) { $systemPowerShell = "powershell.exe" }
    $content = @"
@echo off
setlocal
set "AGENT8088_HOME=$Agent8088Home"
set "AGENT8088_LINK_DIR=$LauncherDir"
"$AgentExe" %*
set "_agent8088_exit=%ERRORLEVEL%"
rem A non-zero exit means the uninstaller cancelled, or stopped before it
rem scheduled any cleanup, so there is nothing here to wait for. Without this,
rem a marker left behind by an earlier run turns a cancelled uninstall into a
rem full wait and then reports a failure that never happened.
if /I "%~1"=="--uninstall" goto agent8088_wait_for_uninstall
if /I "%~1"=="-uninstall" goto agent8088_wait_for_uninstall
exit /b %_agent8088_exit%

:agent8088_wait_for_uninstall
if not "%_agent8088_exit%"=="0" exit /b %_agent8088_exit%
"$systemPowerShell" -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $waitEncoded
set "_agent8088_wait_exit=%ERRORLEVEL%"
if not "%_agent8088_wait_exit%"=="0" exit /b %_agent8088_wait_exit%
set "_agent8088_self=%~f0"
for %%I in ("%~dp0.") do set "_agent8088_launcher=%%~fI"
(goto) 2>nul & del /f /q "%_agent8088_self%" >nul 2>&1 & rd "%_agent8088_launcher%" >nul 2>&1
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
            Register-SkippedStage -Label "Default config" `
                -Reason "no config.txt template found in the repository checkout" `
                -Fix "create $configPath by hand, or re-run the installer over a clean checkout"
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
    Write-Host "  Update: `$env:AGENT8088_BRANCH = '$Branch'; iex (irm https://raw.githubusercontent.com/$RepoSlug/$Branch/install.ps1)"
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
Set-InstallerExitStatus -ExitCode 0
if (-not (Test-DiskSpace)) {
    Write-Info "Installation stopped. Free the required disk space, then run the installer again."
    Set-InstallerExitStatus -ExitCode 1
    return
}
Show-LongPathWarningIfNeeded
Test-HostConnectivity
if (-not $env:AGENT8088_TIMEOUT_SCALE -and (Test-SlowConnection)) {
    Write-Warn "Slow connection detected - doubling all download timeouts."
    $TOllamaPull *= 2; $TNpm *= 2; $TChromium *= 2; $TDownload *= 2; $TPip *= 2
    $TCoreInstall *= 2; $TVenv *= 2; $TUvBoot *= 2; $TExtract *= 2; $TSandboxSetup *= 2
}
# Wrapped so an unexpected exception (e.g. Install-Deps's throw, or any
# native launch failure nothing downstream anticipated) prints one clean
# message and still runs Write-SkippedSummary, instead of propagating out as
# a raw stack trace that skips the summary entirely. Fatal checks return from
# this remotely evaluated script after setting LASTEXITCODE; they must never
# terminate the PowerShell/VS Code terminal that invoked `iex (irm ...)`.
try {
    if (-not (Wait-ForPendingUninstall)) {
        Set-InstallerExitStatus -ExitCode 1
        return
    }
    $terminalAction = Ensure-SupportedTerminal
    if ($terminalAction -eq "relaunched") {
        Set-InstallerExitStatus -ExitCode 0
        return
    }
    if ($terminalAction -ne "continue") {
        Set-InstallerExitStatus -ExitCode 1
        return
    }
    if (-not (Install-Uv)) {
        Set-InstallerExitStatus -ExitCode 1
        return
    }
    if (-not (Test-Python)) {
        Set-InstallerExitStatus -ExitCode 1
        return
    }
    if (-not (Install-Git)) {
        Set-InstallerExitStatus -ExitCode 1
        return
    }
    if (-not (Clone-Repo)) {
        Set-InstallerExitStatus -ExitCode 1
        return
    }
    Install-Deps
    Install-Gateway-Extras
    Install-Node-Bridge
    Install-Embedding-Model
    Install-Native-Sandbox
    if (-not (Setup-Path)) {
        Set-InstallerExitStatus -ExitCode 1
        return
    }
    Drop-Config
    Run-InitialSetup
    Verify-Install
    Start-InitialAgent
} catch {
    Write-Host ""
    Write-Err "Installation failed: $_"
    Write-Info "Re-run the installer to retry - completed stages are skipped or resumed automatically."
    Write-SkippedSummary
    Set-InstallerExitStatus -ExitCode 1
    return
}
Set-InstallerExitStatus -ExitCode 0
