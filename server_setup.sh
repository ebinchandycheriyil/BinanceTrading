#!/bin/sh
# Run in the uploaded project directory on an Ubuntu VPS.
set -eu
cd "$(dirname "$0")"
sudo apt update
sudo apt install -y python3 python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
printf '%s\n' 'Ready for paper trading: .venv/bin/python main.py paper' 'Edit paths/User in trading-bot.service before installing it.'
