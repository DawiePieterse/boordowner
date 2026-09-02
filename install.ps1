# Boord Owner - Windows server installer
#
# Sets up the Owner service to run beside Boord on the same PC: installs
# Python if needed, creates the virtual environment, installs dependencies,
# asks where Boord's database is, writes the launcher, opens the firewall
# port, and registers the service to auto-start at boot as SYSTEM. Safe to
# re-run - each step checks what is already done.
#
# Run via install.bat, which handles the administrator-elevation prompt.

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$VenvDir = Join-Path $BackendDir ".venv"
$DataDir = Join-Path $RepoRoot "data"
$Port = 8010
$TaskName = "Boord Owner Server"
$FirewallRuleName = "Boord Owner Server"
$PythonVersion = "3.11.9"
$PythonInstallerUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$LauncherPath = Join-Path $RepoRoot "start_owner_server.bat"
$InitialPasswordFile = Join-Path $DataDir "initial_owner_password.txt"
$ReleaseKeyPath = Join-Path $RepoRoot "release-key.asc"
$FprFile = Join-Path $DataDir "release_key.fpr"
# The default guess for Boord's database, if Boord is checked out beside this repo.
$DefaultBoordDb = Join-Path (Split-Path $RepoRoot -Parent) "Boord\data\boord.db"

function Write-Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "    $m" -ForegroundColor Red }

function Test-PythonOk($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    try {
        $out = & $exe -c "import sys; print(sys.version_info[0]); print(sys.version_info[1]); print('64BIT' if sys.maxsize > 2**32 else '32BIT')" 2>$null
        if (-not $out -or $out.Count -lt 3) { return $false }
        return ([int]$out[0] -eq 3 -and [int]$out[1] -ge 9 -and $out[2].Trim() -eq "64BIT")
    } catch { return $false }
}

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Err "This script needs to run as Administrator. Run install.bat instead."
        exit 1
    }

    Write-Host ""
    Write-Host "Boord Owner - Server Installer" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan

    # --- Step 1: Find or install Python ---
    Write-Step "Checking for Python 3.9+ (64-bit)..."
    $pythonExe = $null
    $existing = Get-Command python -ErrorAction SilentlyContinue
    if ($existing -and (Test-PythonOk $existing.Source)) {
        $pythonExe = $existing.Source
        Write-Ok "Found a compatible Python at $pythonExe"
    } else {
        $wellKnown = Join-Path $env:ProgramFiles "Python311\python.exe"
        if (Test-PythonOk $wellKnown) {
            $pythonExe = $wellKnown
            Write-Ok "Found a compatible Python at $pythonExe"
        } else {
            Write-Warn "No compatible 64-bit Python 3.9+ found - downloading Python $PythonVersion..."
            $installer = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
            Invoke-WebRequest -Uri $PythonInstallerUrl -OutFile $installer -UseBasicParsing
            Start-Process -FilePath $installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
            Remove-Item $installer -ErrorAction SilentlyContinue
            $pythonExe = $wellKnown
            if (-not (Test-PythonOk $pythonExe)) {
                Write-Err "Python installation could not be confirmed. Install Python 3.9+ (64-bit) from python.org and re-run."
                exit 1
            }
            Write-Ok "Installed Python at $pythonExe"
        }
    }

    # --- Step 2: Git and GnuPG (both only needed by update_owner_server.bat) ---
    #
    # Neither is required to RUN the server, so nothing here is fatal - a farm
    # that cannot update is still a farm that works. They are checked at
    # install time anyway because the alternative is finding out months later,
    # in the middle of wanting an update.
    Write-Step "Checking for Git..."
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Ok "Found Git"
    } else {
        Write-Warn "Git is not installed. The server will still run, but update_owner_server.bat"
        Write-Warn "cannot fetch updates without it. Get it from https://git-scm.com/download/win"
    }

    # Updates are signed-tag only (see update_owner_server.bat), so the update
    # path needs a working gpg. Git for Windows bundles one, but it keeps keys
    # in a keyboxd daemon the Git distribution does not ship, so it fails
    # exactly like a bad signature does - which sends people looking for an
    # attacker instead of an installer. Reject it by path, same as Boord does.
    Write-Step "Checking for GnuPG (used to verify signed releases)..."
    $gpgExe = $null
    $gpgCmd = Get-Command gpg -ErrorAction SilentlyContinue
    if ($gpgCmd -and $gpgCmd.Source -notlike "*\Git\usr\bin\*") {
        $gpgExe = $gpgCmd.Source
        Write-Ok "Found GnuPG at $gpgExe"
    } else {
        foreach ($candidate in @((Join-Path $env:ProgramFiles "GnuPG\bin\gpg.exe"),
                                 (Join-Path ${env:ProgramFiles(x86)} "GnuPG\bin\gpg.exe"))) {
            if ((Test-Path $candidate) -and -not $gpgExe) { $gpgExe = $candidate }
        }
        if ($gpgExe) {
            Write-Ok "Found GnuPG at $gpgExe"
        } else {
            Write-Warn "GnuPG not found. The server runs fine without it, but"
            Write-Warn "update_owner_server.bat cannot verify a release and will refuse to"
            Write-Warn "install one. Boord's own install.bat installs Gpg4win and points git"
            Write-Warn "at it - if this PC runs Boord, run that once and this is handled."
            Write-Warn "Otherwise: https://gpg4win.org"
        }
    }

    # Importing the public key from the repo is safe: what actually decides
    # which releases are trusted is the fingerprint in data\release_key.fpr,
    # which lives outside the repo. A swapped key would not match it and
    # update_owner_server.bat would refuse the release.
    if ($gpgExe -and (Test-Path $ReleaseKeyPath)) {
        try {
            # Not -Wait, and time-limited. GnuPG starts keyboxd and gpg-agent on
            # its first run, and on a machine whose keyring has just been created
            # that start-up can hang indefinitely with its output redirected into
            # a non-interactive process. Importing the key is a convenience; it
            # must never be able to block the install.
            $gpgLog = Join-Path $env:TEMP "boord-owner-gpg-import.log"
            $proc = Start-Process -FilePath $gpgExe `
                -ArgumentList @("--batch", "--yes", "--import", $ReleaseKeyPath) `
                -NoNewWindow -PassThru `
                -RedirectStandardError $gpgLog -RedirectStandardOutput "$gpgLog.out"
            try { $null = $proc.Handle } catch { }
            if ($proc.WaitForExit(60000)) {
                $exit = $null
                try { $exit = $proc.ExitCode } catch { }
                if ($exit -eq 0) { Write-Ok "Imported the release key" }
                else { Write-Warn "Importing release-key.asc did not report success - see $gpgLog" }
            } else {
                try { $proc.Kill() } catch { }
                Write-Warn "gpg did not finish within 60 seconds - skipped the key import."
                Write-Warn "Run this once in a Command Prompt, which lets it finish:"
                Write-Warn "    ""$gpgExe"" --import release-key.asc"
            }
            Remove-Item "$gpgLog.out" -ErrorAction SilentlyContinue
        } catch {
            Write-Warn "Could not import release-key.asc: $($_.Exception.Message)"
        }
    }

    # --- Step 3: Locate Boord's database ---
    Write-Step "Locating Boord's database (read-only access)..."
    $boordDb = $null
    if (Test-Path $DefaultBoordDb) {
        $boordDb = (Resolve-Path $DefaultBoordDb).Path
        Write-Ok "Found it at $boordDb"
    }
    while (-not $boordDb -or -not (Test-Path $boordDb)) {
        Write-Warn "Could not find boord.db automatically."
        $entered = Read-Host "Full path to Boord's data\boord.db (e.g. C:\Boord\data\boord.db)"
        if ($entered -and (Test-Path $entered)) {
            $boordDb = (Resolve-Path $entered).Path
        } else {
            Write-Err "No file at that path. Install Boord first, then re-run this."
        }
    }

    # --- Step 4: Virtual environment ---
    Write-Step "Setting up the app's virtual environment..."
    if (-not (Test-Path $VenvDir)) { & $pythonExe -m venv $VenvDir; Write-Ok "Created virtual environment" }
    else { Write-Ok "Virtual environment already exists" }
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    # Deliberately NOT $VenvDir\Scripts\pip.exe. That is a launcher stub
    # generated when the venv is created - a brand-new unsigned executable,
    # which Windows Application Control (Smart App Control / WDAC / AppLocker)
    # blocks outright on a machine that enforces one: "Program 'pip.exe'
    # failed to run: An Application Control policy has blocked this file".
    # The venv's python.exe is a COPY of the Python Software Foundation
    # binary and keeps its Authenticode signature, so `python -m pip` runs
    # where `pip.exe` cannot. Same pip, same result, one fewer unsigned
    # binary - so this is the right call even where nothing is enforcing.

    # --- Step 5: Dependencies ---
    Write-Step "Installing app dependencies (first run can take a few minutes)..."
    & $venvPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $BackendDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Dependency install failed."
        Write-Err "Usually this is no internet, or pip being blocked by a proxy."
        Write-Err "If the error above mentions an Application Control policy, this PC"
        Write-Err "enforces one and has blocked a binary inside .venv. Nothing here"
        Write-Err "needs the policy relaxed - tell whoever manages it which file was"
        Write-Err "blocked. Re-running this installer is safe; it reuses the venv."
        exit 1
    }
    Write-Ok "Dependencies installed"

    # --- Step 6: Launcher ---
    Write-Step "Creating the server launcher..."
    $launcher = @"
@echo off
cd /d "$BackendDir"
set "BOORD_DB_PATH=$boordDb"
"$venvPython" -m uvicorn main:app --host 0.0.0.0 --port $Port
"@
    Set-Content -Path $LauncherPath -Value $launcher -Encoding ASCII
    Write-Ok "Created $LauncherPath"

    # --- Step 7: Firewall ---
    Write-Step "Allowing the app through Windows Firewall..."
    netsh advfirewall firewall delete rule name="$FirewallRuleName" | Out-Null
    netsh advfirewall firewall add rule name="$FirewallRuleName" dir=in action=allow protocol=TCP localport=$Port | Out-Null
    Write-Ok "Firewall rule set for port $Port"

    # --- Step 8: Scheduled task (auto-start at boot, runs as SYSTEM so it can
    #     read boord.db and its -wal/-shm sidecars, same account as Boord's task) ---
    Write-Step "Registering the server to start automatically with Windows..."
    cmd /c "schtasks /query /tn ""$TaskName"" >nul 2>&1"
    if ($LASTEXITCODE -eq 0) {
        cmd /c "schtasks /end /tn ""$TaskName"" >nul 2>&1"
        Start-Sleep -Seconds 1
        schtasks /delete /tn "$TaskName" /f | Out-Null
    }
    schtasks /create /tn "$TaskName" /tr "`"$LauncherPath`"" /sc onstart /ru SYSTEM /rl highest /f | Out-Null
    Write-Ok "Scheduled task '$TaskName' registered (runs at every startup, no one needs to log in)"

    # --- Step 9: Start it now ---
    Write-Step "Starting the server now..."
    schtasks /run /tn "$TaskName" | Out-Null

    # --- Step 10: Confirm it answers ---
    $serverUp = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest -Uri "http://localhost:$Port/" -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { $serverUp = $true; break }
        } catch { }
    }
    if ($serverUp) { Write-Ok "Server is up and answering on port $Port" }
    else {
        Write-Warn "The server did not answer on port $Port within 20 seconds."
        Write-Warn "Run start_owner_server.bat directly in a window - errors print there"
        Write-Warn "rather than being swallowed by the Scheduled Task."
    }

    # --- Step 11: Report the address and the first password ---
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown" } |
        Select-Object -First 1 -ExpandProperty IPAddress

    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " Setup complete!" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host " On this PC:                        http://localhost:$Port/"
    if ($ip) { Write-Host " From other devices on the network: http://$ip`:$Port/" }
    Write-Host ""
    if (Test-Path $InitialPasswordFile) {
        $pw = (Get-Content $InitialPasswordFile -TotalCount 1).Trim()
        Write-Host " Sign in as:         admin"
        Write-Host " With this password: $pw" -ForegroundColor Yellow
        Write-Host ""
        Write-Warn "Write it down now. It was generated for this server alone, and you"
        Write-Warn "will be asked to replace it at first sign-in. Once you have, the copy"
        Write-Warn "in data\initial_owner_password.txt is deleted automatically."
        Write-Warn "Add more people from the Users tab once you are in."
    } else {
        Write-Host " Sign in with the manager password already set on this server."
    }
    Write-Host ""
    Write-Host " The server will now start automatically every time this PC turns on."
    Write-Host " update_owner_server.bat installs the newest SIGNED release and restarts it."

    # --- Step 12: The trust root for updates ---
    #
    # Deliberately NOT written automatically. What this fingerprint decides is
    # which code this machine will accept in future, and this installer came
    # out of the very repository those updates come from - so a fingerprint it
    # wrote for you would be the repo vouching for itself. Typing it is the one
    # step that has to be a person's decision.
    #
    # Boord's own release_key.fpr is a different matter: it is outside both
    # repos and a human already put it there, for the same publisher and the
    # same key. Offering it saves the "where do I get the fingerprint" problem
    # without making the choice for anybody.
    if (-not (Test-Path $FprFile)) {
        $boordFpr = $null
        try {
            $boordDataDir = Split-Path (Split-Path $boordDb -Parent) -Parent
            $candidate = Join-Path $boordDataDir "data\release_key.fpr"
            if (Test-Path $candidate) { $boordFpr = (Get-Content $candidate -TotalCount 1).Trim() }
        } catch { }

        Write-Host ""
        Write-Host "================================================" -ForegroundColor Yellow
        Write-Host " One step left: trust the release key" -ForegroundColor Yellow
        Write-Host "================================================" -ForegroundColor Yellow
        Write-Host " update_owner_server.bat will refuse to install anything until this"
        Write-Host " server knows which signing key to trust. The server you just"
        Write-Host " installed runs fine without this - only updates need it."
        Write-Host ""
        if ($boordFpr) {
            Write-Host " Boord on this PC already trusts this key. Same publisher, same"
            Write-Host " key - so in this folder, run:"
            Write-Host ""
            Write-Host "     echo $boordFpr> data\release_key.fpr" -ForegroundColor Cyan
        } else {
            Write-Host " In this folder, run:"
            Write-Host ""
            Write-Host "     echo <FINGERPRINT>> data\release_key.fpr" -ForegroundColor Cyan
            Write-Host ""
            Write-Host " ...with the 40-character fingerprint from whoever maintains this"
            Write-Host " install."
        }
        Write-Host ""
        Write-Warn "Note there is NO space before the > - echo would write one into the"
        Write-Warn "file, and the fingerprint would then never match."
    } else {
        $fpr = (Get-Content $FprFile -TotalCount 1).Trim()
        Write-Ok "Release key fingerprint on file: $fpr"
    }
} catch {
    Write-Host ""
    Write-Err "Something went wrong:"
    Write-Err $_.Exception.Message
    exit 1
}
