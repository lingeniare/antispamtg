#!/usr/bin/env bash
set -e
# Обновление без потери настроек: не трогает .env, config/config.yaml, data/bot.db
INSTALL_DIR="/opt/tg-antispam"
cd $INSTALL_DIR
echo "Обновление..."
if [ -d ".git" ]; then
  git pull --rebase || git pull
fi
.venv/bin/pip install -e . --upgrade
sudo systemctl daemon-reload
sudo systemctl restart tg-antispam
echo "Готово. Настройки сохранены (.env, config/config.yaml, data/bot.db не перетерты)"
sudo systemctl status tg-antispam --no-pager | head -n 20
