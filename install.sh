#!/usr/bin/env bash
set -e
# One-click installer for Ubuntu 22/24
# Usage: curl -fsSL https://tg.vega.chat/install.sh | bash
# или: bash install.sh

INSTALL_DIR="/opt/tg-antispam"
REPO_URL=""

# Требуется root: /opt и systemd — нужен sudo. Без root установи в ~/tg-antispam: INSTALL_DIR="$HOME/tg-antispam" bash install.sh
if [ "$EUID" -ne 0 ] && ! sudo -n true 2>/dev/null; then
  echo "⚠️  Нужен sudo/root для установки в $INSTALL_DIR и systemd."
  echo "   Запусти: sudo bash install.sh"
  echo "   Или без root: INSTALL_DIR=\$HOME/tg-antispam bash install.sh (без systemd, запуск через .venv/bin/python -m src.bot.bot)"
fi

echo "=============================================="
echo " TG AntiSpam Bot — установка (Python + Vega AI)"
echo "=============================================="
echo ""
echo "Инструкция перед установкой:"
echo " 1) Создайте бота: Telegram -> @BotFather -> /newbot -> придумайте name_bot (напр. antispamtg_bot) -> скопируйте токен"
echo " 2) Получите API key:"
echo "    - VEGA (по умолчанию, дешевле): https://api.vega.chat -> регистрация -> Dashboard -> скопируйте API key"
echo "    - OpenRouter (альтернатива): https://openrouter.ai -> Keys -> Create API Key"
echo "    Разница только в base URL, API совместимы."
echo ""
read -p "Продолжить установку? (Y/N): " cont
if [[ "$cont" != "Y" && "$cont" != "y" ]]; then echo "Остановлено."; exit 0; fi

# deps (Ubuntu 22/24: нужен python3-venv / python3.12-venv для ensurepip)
echo "[1/6] Проверка зависимостей..."
sudo apt update
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "3.12")
sudo apt install -y python3 python3-pip python3-venv git curl || true
# на Ubuntu 24.04 пакет называется python3.12-venv
sudo apt install -y python3.12-venv 2>/dev/null || sudo apt install -y python${PYVER}-venv 2>/dev/null || true

# dir — разделяем исходник и установку
PROJECT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
echo "[2/6] Копирование файлов в $INSTALL_DIR ..."
echo "  Исходник: $PROJECT_DIR"
echo "  Установка: $INSTALL_DIR"
if [[ "$PROJECT_DIR" == "$INSTALL_DIR" ]]; then
  echo "  -> исходник = установка, копирование пропускаем"
  cd "$INSTALL_DIR"
else
  sudo mkdir -p "$INSTALL_DIR"
  if [[ -f "$PROJECT_DIR/pyproject.toml" ]]; then
    # rsync если есть, иначе cp с исключениями (не трогаем .env/config.yaml/data/.venv в назначении)
    if command -v rsync &>/dev/null; then
      sudo rsync -a --delete \
        --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
        --exclude='data/' --exclude='.env' --exclude='config/config.yaml' \
        "$PROJECT_DIR"/ "$INSTALL_DIR"/
    else
      # fallback: копируем только код
      sudo cp -r "$PROJECT_DIR/src" "$PROJECT_DIR/pyproject.toml" "$PROJECT_DIR/config" "$PROJECT_DIR/systemd" "$PROJECT_DIR/README.md" "$INSTALL_DIR"/ 2>/dev/null || sudo cp -r "$PROJECT_DIR"/. "$INSTALL_DIR"/
    fi
    # создаём папки если нет
    sudo mkdir -p "$INSTALL_DIR/config" "$INSTALL_DIR/data"
    [[ -f "$INSTALL_DIR/config/config.example.yaml" ]] || sudo cp "$PROJECT_DIR/config/config.example.yaml" "$INSTALL_DIR/config/" 2>/dev/null || true
    [[ -f "$INSTALL_DIR/config/filter_prompt.txt" ]] || sudo cp "$PROJECT_DIR/config/filter_prompt.txt" "$INSTALL_DIR/config/" 2>/dev/null || true
  else
    if [[ -n "$REPO_URL" ]]; then
      sudo git clone "$REPO_URL" "$INSTALL_DIR" 2>/dev/null || (cd "$INSTALL_DIR" && sudo git pull)
    else
      echo "Скопируйте проект в $INSTALL_DIR вручную и перезапустите скрипт."
      exit 1
    fi
  fi
  cd "$INSTALL_DIR"
fi

# venv (нужен root — /opt требует sudo)
echo "[3/6] Создание venv и установка зависимостей..."
sudo rm -rf .venv 2>/dev/null || true
sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -e .
sudo chown -R $(whoami):$(whoami) .venv 2>/dev/null || true

# интерактивные вопросы
echo ""
echo "[4/6] Настройка бота (можно пропустить Enter — потом через CLI):"
read -p "  BOT_TOKEN (от @BotFather): " BOT_TOKEN
echo ""
echo "  Выберите AI провайдера:"
echo "    1) VEGA API — https://api.vega.chat/v1 (по умолчанию, дешевле)"
echo "    2) OpenRouter — https://openrouter.ai/api/v1"
read -p "  Провайдер [1/2, default 1]: " AI_PROVIDER
AI_PROVIDER=${AI_PROVIDER:-1}
if [[ "$AI_PROVIDER" == "2" ]]; then
  VEGA_BASE_URL="https://openrouter.ai/api/v1"
  read -p "  OPENROUTER_API_KEY (openrouter.ai): " VEGA_KEY
else
  VEGA_BASE_URL="https://api.vega.chat/v1"
  read -p "  VEGA_API_KEY (api.vega.chat): " VEGA_KEY
fi
read -p "  Группы/каналы для фильтрации (через запятую, ID или @username, пусто=все): " ALLOWED
read -p "  Белый список user_id (через запятую): " WHITELIST
read -p "  Основной язык (ru/en/tr/uk, default ru): " LANG
LANG=${LANG:-ru}
read -p "  Модель (default gpt-4o-mini): " MODEL
MODEL=${MODEL:-gpt-4o-mini}

# сохраняем .env и config.yaml (не перетираем при обновлении если уже есть)
if [ ! -f ".env" ]; then
  cat > .env <<EOF
BOT_TOKEN=$BOT_TOKEN
VEGA_API_KEY=$VEGA_KEY
VEGA_BASE_URL=$VEGA_BASE_URL
VEGA_MODEL=$MODEL
ALLOWED_CHATS=$ALLOWED
WHITELIST_USERS=$WHITELIST
DEFAULT_LANGUAGE=$LANG
FILTER_PROMPT_PATH=config/filter_prompt.txt
CAPTCHA_TIMEOUT_SEC=120
DELETE_SPAM=true
EOF
  echo "  -> .env создан (провайдер: $VEGA_BASE_URL)"
else
  echo "  -> .env уже существует, не перетерт (обнови вручную при нужде)"
fi

if [ ! -f "config/config.yaml" ]; then
  mkdir -p config
  cat > config/config.yaml <<EOF
bot_token: "$BOT_TOKEN"
vega_api_key: "$VEGA_KEY"
vega_base_url: "$VEGA_BASE_URL"
vega_model: "$MODEL"
allowed_chats: [$(echo $ALLOWED | sed 's/,/, /g')]
whitelist_users: [$(echo $WHITELIST | sed 's/,/, /g')]
default_language: "$LANG"
filter_prompt_path: "config/filter_prompt.txt"
captcha_timeout_sec: 120
delete_spam: true
EOF
  echo "  -> config/config.yaml создан"
else
  echo "  -> config/config.yaml уже существует, не перетерт"
fi

# systemd user
if ! id tg-antispam &>/dev/null; then
  sudo useradd --system --no-create-home --shell /usr/sbin/nologin tg-antispam 2>/dev/null || true
fi
sudo chown -R tg-antispam:tg-antispam "$INSTALL_DIR/data" 2>/dev/null || true
sudo chown tg-antispam:tg-antispam "$INSTALL_DIR/.env" 2>/dev/null || true
sudo chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true

# systemd
echo "[5/6] Настройка systemd..."
sudo cp systemd/tg-antispam.service /etc/systemd/system/tg-antispam.service
sudo systemctl daemon-reload
sudo systemctl enable tg-antispam
sudo systemctl restart tg-antispam || sudo systemctl start tg-antispam
sleep 2
sudo systemctl status tg-antispam --no-pager | head -n 30 || true

echo ""
echo "[6/6] Готово!"
echo "  Логи: sudo journalctl -u tg-antispam -f"
echo "  Настройка через терминал:"
echo "    .venv/bin/python -m src.cli.manage status"
echo "    .venv/bin/python -m src.cli.manage add-chat -1001234567890"
echo "    .venv/bin/python -m src.cli.manage add-whitelist 12345678"
echo "    .venv/bin/python -m src.cli.manage set-model claude-3.5-sonnet"
echo "    nano config/filter_prompt.txt  # затем sudo systemctl restart tg-antispam"
echo ""
echo "  ВАЖНО: Добавьте бота в группу/канал и выдайте права АДМИНА (удаление сообщений + бан), иначе не сможет чистить спам."
echo "  Для КАНАЛА: обязательно добавьте бота ещё и в группу комментариев (Канал -> Настройки -> Обсуждение -> Группа),"
echo "  иначе бот не увидит комментарии. Дайте там тоже права админа."
echo "  Хотите больше настроек без сервера? Используйте https://tg.vega.chat"
echo ""
echo "  Обновление без потери настроек: bash update.sh"
