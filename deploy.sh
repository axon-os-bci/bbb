#!/bin/bash
# GMGN Solana Bot Deployment Script
# Для Ubuntu 24.04 LTS (4GB RAM, 2 core)

set -euo pipefail

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== GMGN Solana Bot Deployment ===${NC}"

# ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ .env
ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}❌ Файл $ENV_FILE не найден в текущей директории ($(pwd))!${NC}"
    exit 1
fi

# Безопасная загрузка .env
set -a
source "$ENV_FILE"
set +a

# Проверка обязательных переменных
if [ -z "${REPO_URL:-}" ]; then
    echo -e "${RED}❌ REPO_URL не задан в $ENV_FILE${NC}"
    exit 1
fi

if [ -z "${HELIUS_API_KEY:-}" ]; then
    echo -e "${RED}❌ HELIUS_API_KEY не задан в $ENV_FILE${NC}"
    exit 1
fi

if [ -z "${PUBLIC_KEY:-}" ]; then
    echo -e "${RED}❌ PUBLIC_KEY не задан в $ENV_FILE${NC}"
    exit 1
fi

echo -e "${YELLOW}📦 Репозиторий: $REPO_URL${NC}"
echo -e "${YELLOW}🔑 Кошелек: ${PUBLIC_KEY:0:16}...${NC}"

# Конфигурация
BOT_USER="solbot"
BOT_DIR="/opt/gmgn_bot"
BOT_SERVICE="gmgn-bot"
PYTHON_VERSION="3.12"

echo -e "${YELLOW}Начинаю установку...${NC}"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Пожалуйста, запустите скрипт с sudo${NC}"
    exit 1
fi

# 1. Обновление системы
echo -e "${YELLOW}[1/9] Обновление пакетов...${NC}"
apt-get update && apt-get upgrade -y

# 2. Установка Python 3.12 и зависимостей
echo -e "${YELLOW}[2/9] Установка Python ${PYTHON_VERSION}...${NC}"
apt-get install -y \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python${PYTHON_VERSION}-dev \
    python3-pip \
    git \
    curl \
    wget \
    htop \
    tmux \
    ufw \
    fail2ban \
    logrotate \
    sqlite3 \
    libssl-dev \
    libffi-dev \
    build-essential

# 3. Создание пользователя для бота
echo -e "${YELLOW}[3/9] Создание пользователя ${BOT_USER}...${NC}"
if ! id "$BOT_USER" &>/dev/null; then
    useradd -r -s /bin/false -d ${BOT_DIR} -m ${BOT_USER}
    usermod -aG systemd-journal ${BOT_USER}
fi

# 4. Настройка директорий
echo -e "${YELLOW}[4/9] Настройка директорий...${NC}"
mkdir -p ${BOT_DIR}/{config,logs,data}
chown -R ${BOT_USER}:${BOT_USER} ${BOT_DIR}
chmod 750 ${BOT_DIR}
chmod 700 ${BOT_DIR}/config

# 5. Клонирование репозитория
echo -e "${YELLOW}[5/9] Клонирование репозитория...${NC}"

cd /tmp
rm -rf bbb_temp_clone

if ! git clone ${REPO_URL} bbb_temp_clone; then
    echo -e "${RED}Ошибка: Не удалось клонировать репозиторий ${REPO_URL}${NC}"
    exit 1
fi

if [ ! -d "/tmp/bbb_temp_clone" ]; then
    echo -e "${RED}Ошибка: Директория после клонирования не найдена${NC}"
    exit 1
fi

# Копирование файлов
echo -e "${YELLOW}Копирование файлов...${NC}"
cp -r /tmp/bbb_temp_clone/* ${BOT_DIR}/
cp -r /tmp/bbb_temp_clone/.[^.]* ${BOT_DIR}/ 2>/dev/null || true

# Очистка
rm -rf /tmp/bbb_temp_clone

# 6. ИСПРАВЛЕНИЕ КОНФЛИКТА ИМЕН (критично!)
echo -e "${YELLOW}[6/9] Исправление конфликта имён модулей...${NC}"
if [ -d "${BOT_DIR}/solana" ]; then
    echo "Переименование solana/ -> solana_modules/ ..."
    mv ${BOT_DIR}/solana ${BOT_DIR}/solana_modules

    # Создаем __init__.py если отсутствует
    touch ${BOT_DIR}/solana_modules/__init__.py
    chown ${BOT_USER}:${BOT_USER} ${BOT_DIR}/solana_modules/__init__.py

    # Обновляем импорты: ТОЛЬКО локальные модули (raydium), НЕ трогаем solana.rpc (PyPI)
    # Используем точечное исправление только для известных локальных модулей
    find ${BOT_DIR} -name "*.py" -type f -exec sed -i 's/from solana\.raydium/from solana_modules.raydium/g' {} \;
    find ${BOT_DIR} -name "*.py" -type f -exec sed -i 's/import solana\.raydium/import solana_modules.raydium/g' {} \;

    echo -e "${GREEN}✅ Модуль переименован в solana_modules, импорты исправлены${NC}"
fi

# Настройка прав
chown -R ${BOT_USER}:${BOT_USER} ${BOT_DIR}
chmod 700 ${BOT_DIR}/config

# 7. Создание виртуального окружения
echo -e "${YELLOW}[7/9] Установка Python зависимостей...${NC}"
cd ${BOT_DIR}

# Исправляем requirements.txt (добавляем недостающие зависимости)
if [ ! -f "requirements.txt" ]; then
    echo -e "${RED}❌ requirements.txt не найден! Создаю стандартный...${NC}"
    cat > requirements.txt << 'REQEOF'
solders>=0.23.0,<0.24.0
solana>=0.36.0,<0.37.0
websockets>=12.0
aiohttp>=3.9.0
PyYAML>=6.0.1
pydantic>=2.5.0
pydantic-settings>=2.1.0
python-dotenv>=1.0.0
aiosqlite>=0.19.0
REQEOF
fi

# Проверяем и добавляем aiosqlite если отсутствует
if ! grep -q "^aiosqlite" requirements.txt; then
    echo "aiosqlite>=0.19.0" >> requirements.txt
    echo -e "${YELLOW}Добавлен aiosqlite в requirements.txt${NC}"
fi

# Проверяем совместимость solders и solana
if grep -q "solders>=0.21.0" requirements.txt; then
    sed -i 's/solders>=0.21.0/solders>=0.23.0,<0.24.0/' requirements.txt
fi

sudo -u ${BOT_USER} python${PYTHON_VERSION} -m venv venv
sudo -u ${BOT_USER} ${BOT_DIR}/venv/bin/pip install --upgrade pip
sudo -u ${BOT_USER} ${BOT_DIR}/venv/bin/pip install -r requirements.txt

# 8. Проверка установки и исправление импортов (на всякий случай)
echo -e "${YELLOW}[8/9] Проверка установки solana...${NC}"

# Восстанавливаем PyPI импорты solana.rpc, если они были случайно заменены
find ${BOT_DIR} -name "*.py" -type f -exec sed -i 's/from solana_modules\.rpc/from solana.rpc/g' {} \;
find ${BOT_DIR} -name "*.py" -type f -exec sed -i 's/import solana_modules\.rpc/import solana.rpc/g' {} \;

if ! sudo -u ${BOT_USER} ${BOT_DIR}/venv/bin/python -c "from solana.rpc.async_api import AsyncClient; print('OK')" 2>/dev/null; then
    echo -e "${RED}❌ Ошибка: Не удалось импортировать solana.rpc (PyPI)${NC}"
    exit 1
fi
echo -e "${GREEN}✅ PyPI пакет solana установлен корректно${NC}"

if ! sudo -u ${BOT_USER} ${BOT_DIR}/venv/bin/python -c "from solana_modules.raydium import RaydiumAPI; print('OK')" 2>/dev/null; then
    echo -e "${YELLOW}⚠️ Предупреждение: Локальный модуль raydium не найден в solana_modules${NC}"
fi

# 9. Создание .env файла для бота (для systemd)
echo -e "${YELLOW}[9/9] Настройка окружения...${NC}"
cat > ${BOT_DIR}/.env << EOF
HELIUS_API_KEY=${HELIUS_API_KEY}
PUBLIC_KEY=${PUBLIC_KEY}
EOF
chmod 600 ${BOT_DIR}/.env
chown ${BOT_USER}:${BOT_USER} ${BOT_DIR}/.env

# 10. Создание systemd сервиса
echo -e "${YELLOW}[Дополнительно] Создание системного сервиса...${NC}"
cat > /etc/systemd/system/${BOT_SERVICE}.service << EOF
[Unit]
Description=GMGN Solana Trading Bot
After=network.target
Wants=network.target

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_USER}
WorkingDirectory=${BOT_DIR}
Environment="PATH=${BOT_DIR}/venv/bin:/usr/local/bin:/usr/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONDONTWRITEBYTECODE=1"
EnvironmentFile=${BOT_DIR}/.env

ExecStart=${BOT_DIR}/venv/bin/python main.py

Restart=always
RestartSec=10
StartLimitInterval=60s
StartLimitBurst=3

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${BOT_DIR}/logs ${BOT_DIR}/data ${BOT_DIR}/.env
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

StandardOutput=journal
StandardError=journal
SyslogIdentifier=gmgn-bot

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${BOT_SERVICE}

# Настройка logrotate
echo -e "${YELLOW}[Дополнительно] Настройка ротации логов...${NC}"
cat > /etc/logrotate.d/${BOT_SERVICE} << EOF
${BOT_DIR}/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 640 ${BOT_USER} ${BOT_USER}
    sharedscripts
    postrotate
        systemctl reload ${BOT_SERVICE} > /dev/null 2>&1 || true
    endscript
}
EOF

# Настройка firewall
echo -e "${YELLOW}[Дополнительно] Настройка Firewall...${NC}"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw --force enable

echo -e "${GREEN}=== Установка завершена! ===${NC}"
echo ""
echo -e "${YELLOW}⚠️  ВАЖНЫЕ СЛЕДУЮЩИЕ ШАГИ:${NC}"
echo "1. 🔑 Скопируйте приватный ключ:"
echo "   sudo cp /path/to/wallet.key ${BOT_DIR}/config/"
echo "   sudo chmod 600 ${BOT_DIR}/config/wallet.key"
echo "   sudo chown ${BOT_USER}:${BOT_USER} ${BOT_DIR}/config/wallet.key"
echo ""
echo "2. 📝 Проверьте конфиг: sudo nano ${BOT_DIR}/config/settings.yaml"
echo "   (должны быть плейсхолдеры \${HELIUS_API_KEY} и \${PUBLIC_KEY})"
echo ""
echo "3. 🚀 Запустите: sudo systemctl start ${BOT_SERVICE}"
echo "   Логи: sudo journalctl -u ${BOT_SERVICE} -f"
echo ""
echo "4. 🛑 Остановить: sudo systemctl stop ${BOT_SERVICE}"
echo "   Статус: sudo systemctl status ${BOT_SERVICE}"