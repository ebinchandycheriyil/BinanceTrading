#!/usr/bin/env bash
# One-time setup of the ML environment inside WSL Ubuntu (Python venv on the Linux disk).
# Run from Windows:  wsl -d Ubuntu -u root -- bash /mnt/c/Users/ebinc/OneDrive/Documents/Projects/Trade/playgroundML/wsl_setup.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip libgomp1 > /dev/null
python3 -m venv /opt/trademl
/opt/trademl/bin/pip install -q --upgrade pip
/opt/trademl/bin/pip install -q "numpy==2.5.3" "pandas==3.0.6" "scikit-learn==1.9.1" "lightgbm==4.7.0"
/opt/trademl/bin/python - <<'EOF'
import numpy, pandas, sklearn, lightgbm
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
print('WSL ML env OK: numpy', numpy.__version__, 'pandas', pandas.__version__, 'sklearn', sklearn.__version__,
      'lightgbm', lightgbm.__version__)
EOF
