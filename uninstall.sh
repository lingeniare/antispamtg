#!/usr/bin/env bash
set -e
# Полное удаление бота — для чистой переустановки
# Usage: sudo bash uninstall.sh [--keep-config]

INSTALL_DIR="/opt/tg-antispam"
KEEP_CONFIG=false
if [[ "$1" == "--keep-config" ]]; then KEEP_CONFIG=true; fi

echo "=== Удаление TG AntiSpam Bot ==="
echo "INSTALL_DIR: $INSTALL_DIR"
echo "KEEP_CONFIG: $KEEP_CONFIG"
read -p "Удалить? (Y/N): " c
if [[ "$c" != "Y" && "$c" != "y" ]]; then echo "Отмена"; exit 0; fi

sudo systemctl stop tg-antispam 2>/dev/null || true
sudo systemctl disable tg-antispam 2>/dev/null || true
sudo rm -f /etc/systemd/system/tg-antispam.service
sudo systemctl daemon-reload

if $KEEP_CONFIG; then
  echo "Сохраняю .env, config.yaml, bot.db..."
  sudo mkdir -p /tmp/tg-antispam-backup
  sudo cp -a $INSTALL_DIR/.env /tmp/tg-antispam-backup/ 2>/dev/null || true
  sudo cp -a $INSTALL_DIR/config/config.yaml /tmp/tg-antispam-backup/ 2>/dev/null || true
  sudo cp -a $INSTALL_DIR/data/bot.db /tmp/tg-antispam-backup/ 2>/dev/null || true
fi

sudo rm -rf $INSTALL_DIR
echo "Удалено: $INSTALL_DIR"

if $KEEP_CONFIG; then
  echo "Бэкап в /tmp/tg-antispam-backup/ (восстанови после reinstall)"
  ls -lh /tmp/tg-antispam-backup/ 2>/dev/null || true
else
  # чистим и webhook чтобы не висел
  echo "Webhook не трогаем (удалится при новой установке через delete_webhook)"
fi

echo "Готово. Для чистой установки: sudo bash install.sh"
