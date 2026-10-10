# Show the paper-bot results table from the Oracle VM.  Usage:  .\results.ps1        (add -Health for the full status)
param([switch]$Health)
$key = Join-Path $HOME '.ssh\oracle_vm.key'
$vm = 'opc@129.225.83.139'
$cmd = if ($Health) { 'bash ~/trade/deploy/vps/status.sh' } else { '~/trade/venv/bin/python ~/trade/deploy/vps/results.py' }
ssh -i $key -o LogLevel=ERROR -o ConnectTimeout=20 $vm $cmd
if ($LASTEXITCODE -ne 0) { Write-Host "Could not reach the VM (exit $LASTEXITCODE). Check your internet / that the VM is running." -ForegroundColor Red }
