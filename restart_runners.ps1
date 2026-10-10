# Relaunch all paper-trading runners and the data recorder after a reboot or sleep.
# Each one resumes from its saved state; a runner that is already running refuses to start a second copy.
# Usage (PowerShell, from anywhere):  powershell -ExecutionPolicy Bypass -File "<project>\restart_runners.ps1" -OnLaptop
# Since 2026-10-02 19:00 UTC the bots run on the Oracle VM (deploy/vps). Running them here as well would trade the
# same paper accounts twice, so this script refuses unless -OnLaptop is given (stop the VM services first).
param([switch]$OnLaptop)
if (-not $OnLaptop) {
    'The paper bots now run on the Oracle VM. Stop them there first (sudo systemctl disable --now trade-cycle.timer trade-recorder),'
    'copy the run folders back, then re-run this script with -OnLaptop.'
    exit 1
}
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pg = Join-Path $root 'playground'
$py = Join-Path $root '.venv\Scripts\python.exe'

function Launch($name, $wd, $runDir, $argList) {
    New-Item -ItemType Directory -Force $runDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    if (Test-Path "$runDir\stdout.log") { Move-Item "$runDir\stdout.log" "$runDir\stdout_$stamp.log" }
    if (Test-Path "$runDir\stderr.log") { Move-Item "$runDir\stderr.log" "$runDir\stderr_$stamp.log" }
    $p = Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $wd -WindowStyle Hidden `
        -RedirectStandardOutput "$runDir\stdout.log" -RedirectStandardError "$runDir\stderr.log" -PassThru
    $p.Id | Out-File -Encoding ascii "$runDir\pid.txt"
    "{0,-22} started (pid {1})" -f $name, $p.Id
}

$r1 = Join-Path $root 'artifacts\daytrade\live_squeeze_oi'
Launch 'squeeze paper run' $root $r1 @('-m', 'daytrade.live', 'start', '--run', "`"$r1`"")

$r2 = Join-Path $pg 'artifacts\daytrade\live_volume_breakout'
Launch 'volume breakout run' $pg $r2 @('-m', 'daytrade.live', 'start', '--run', "`"$r2`"", '--strategy', 'lab',
    '--params', "`"@$r2\params.json`"", '--symbols', 'BTCUSDT', 'ETHUSDT')

$r3 = Join-Path $pg 'artifacts\daytrade\live_carry'
Launch 'funding carry run' $pg $r3 @('-m', 'daytrade.carry_live', 'start', '--run', "`"$r3`"")

$r4 = Join-Path $pg 'artifacts\daytrade\recorder'
Launch 'data recorder' $pg $r4 @('-m', 'daytrade.recorder')

$ml = Join-Path $root 'playgroundML'
$r5 = Join-Path $ml 'artifacts\ml\live_rf'
Launch 'ML random forest run' $ml $r5 @('-m', 'daytrade.live', 'start', '--run', "`"$r5`"", '--strategy', 'ml_rf',
    '--params', "`"@$r5\params.json`"", '--symbols', 'BTCUSDT', 'ETHUSDT')

Start-Sleep -Seconds 20
foreach ($d in @($r1, $r2, $r3, $r4, $r5)) {
    $err = Get-Content "$d\stderr.log" -ErrorAction SilentlyContinue | Select-Object -Last 1
    $out = Get-Content "$d\stdout.log" -ErrorAction SilentlyContinue | Select-Object -Last 1
    "{0}: {1} {2}" -f (Split-Path $d -Leaf), $out, $err
}
