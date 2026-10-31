#!/usr/bin/env bash
set -e

APP_DIR=/opt/app
REPO=https://github.com/dewaldhugo/Frontline-PPE-Scorecard.git

echo "==> Updating code"
if [ ! -d "$APP_DIR" ]; then
    sudo git clone "$REPO" "$APP_DIR"
    sudo chown -R "$USER:$USER" "$APP_DIR"
fi
cd "$APP_DIR"
git pull

echo "==> Installing dependencies"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -r requirements.txt

echo "==> Restarting service"
sudo systemctl daemon-reload
sudo systemctl restart ppe-app
sudo systemctl status ppe-app --no-pager