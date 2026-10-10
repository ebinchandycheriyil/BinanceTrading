#!/bin/bash
# One-time setup on the Oracle VM (Oracle Linux 9, 1 GB RAM). Run as opc:  bash ~/trade/deploy/vps/setup.sh
set -euo pipefail
T=/home/opc/trade

# 1. more swap (1 GB RAM + 1 GB swap is too tight for dnf/pip); 2 GB extra file, kept across reboots
if [ ! -f /swapfile2 ]; then
    sudo fallocate -l 2G /swapfile2 && sudo chmod 600 /swapfile2 && sudo mkswap /swapfile2 && sudo swapon /swapfile2
    echo '/swapfile2 none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# 2. Python 3.12 + the three packages the bots need (no scikit-learn/torch: the ML bot uses exported numpy trees)
sudo dnf -y --setopt=install_weak_deps=False install python3.12 python3.12-pip
[ -d $T/venv ] || python3.12 -m venv $T/venv
$T/venv/bin/pip install --no-cache-dir -q --upgrade pip
$T/venv/bin/pip install --no-cache-dir -q numpy==2.5.3 pandas==3.0.6 aiohttp==3.14.3

# 3. services: the 5-minute cycle timer and the always-on recorder
mkdir -p $T/logs
sudo cp $T/deploy/vps/trade-cycle.service $T/deploy/vps/trade-cycle.timer $T/deploy/vps/trade-recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
echo "setup done. start with:  sudo systemctl enable --now trade-cycle.timer trade-recorder.service"
