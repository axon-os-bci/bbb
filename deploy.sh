#!/bin/bash
# GMGN Solana Bot Deployment Script
# Для Ubuntu 24.04 LTS (4GB RAM, 2 core)

set -e  # Остановка при любой ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== GMGN Solana Bot Deployment ===${NC}"

# ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ .env
ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}❌ Файл $ENV_FILE не найден в текущей директории!${NC}"
    echo "Создайте .env файл на основе .env.example"
    exit 1
fi

# Загружаем переменные, игнорируя комментарии и пустые строки
export $(grep -v '^#' "$ENV_FILE" | grep -v '^$' | xargs -d '\n')

# Проверка обязательных переменных
if [ -z "$REPO_URL" ]; then
    echo -e "${RED}❌ REPO_URL не задан в $ENV_FILE${NC}"
    exit 1
fi

if [ -z "$HELIUS_API_KEY" ]; then
    echo -e "${RED}❌ HELIUS_API_KEY не задан в $ENV_FILE${NC}"
    exit 1
fi

echo -e "${YELLOW}📦 Репозиторий: $REPO_URL${NC}"

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
echo -e "${YELLOW}[1/8] Обновление пакетов...${NC}"
apt-get update && apt-get upgrade -y

# 2. Установка Python 3.12 и зависимостей
echo -e "${YELLOW}[2/8] Установка Python ${PYTHON_VERSION} и системных зависимостей...${NC}"
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

# 3. Создание пользователя для бота (без логина)
echo -e "${YELLOW}[3/8] Создание пользователя ${BOT_USER}...${NC}"
if ! id "$BOT_USER" &>/dev/null; then
    useradd -r -s /bin/false -d ${BOT_DIR} -m ${BOT_USER}
    usermod -aG systemd-journal ${BOT_USER}
fi

# 4. Настройка директорий
echo -e "${YELLOW}[4/8] Настройка директорий...${NC}"
mkdir -p ${BOT_DIR}/{config,logs,data}
chown -R ${BOT_USER}:${BOT_USER} ${BOT_DIR}
chmod 750 ${BOT_DIR}

# 4.1. Клонирование репозитория с GitHub
echo -e "${YELLOW}[5/8] Клонирование репозитория...${NC}"

# Очистка временной директории
cd /tmp
rm -rf bbb_temp_clone

# Клонирование от имени пользователя бота
if ! sudo -u ${BOT_USER} git clone ${REPO_URL} bbb_temp_clone; then
    echo -e "${RED}Ошибка: Не удалось клонировать репозиторий ${REPO_URL}${NC}"
    echo -e "${YELLOW}Проверьте:${NC}"
    echo "  - Доступность GitHub"
    echo "  - Правильность URL в .env"
    echo "  - Доступ к приватному репо (если нужен SSH ключ)"
    exit 1
fi

# Копирование файлов
echo -e "${YELLOW}Копирование файлов бота...${NC}"
cp -r /tmp/bbb_temp_clone/* ${BOT_DIR}/

# Очистка
rm -rf /tmp/bbb_temp_clone

# Настройка прав
chown -R ${BOT_USER}:${BOT_USER} ${BOT_DIR}
chmod 700 ${BOT_DIR}/config

# 6. Создание виртуального окружения
echo -e "${YELLOW}[6/8] Установка Python зависимостей...${NC}"
cd ${BOT_DIR}
sudo -u ${BOT_USER} python${PYTHON_VERSION} -m venv venv
sudo -u ${BOT_USER} ${BOT_DIR}/venv/bin/pip install --upgrade pip
sudo -u ${BOT_USER} ${BOT_DIR}/venv/bin/pip install -r requirements.txt

# 7. Создание systemd сервиса
echo -e "${YELLOW}[7/8] Создание системного сервиса...${NC}"
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
Environment="HELIUS_API_KEY=${HELIUS_API_KEY}"

# Запуск бота
ExecStart=${BOT_DIR}/venv/bin/python main.py

# Перезапуск при падении
Restart=always
RestartSec=10
StartLimitInterval=60s
StartLimitBurst=3

# Безопасность
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${BOT_DIR}/logs ${BOT_DIR}/data
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

# Логирование
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gmgn-bot

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${BOT_SERVICE}

# 8. Настройка логов (logrotate)
echo -e "${YELLOW}[8/8] Настройка ротации логов...${NC}"
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

# Финальные инструкции
echo -e "${GREEN}=== Установка завершена! ===${NC}"
echo ""
echo -e "${YELLOW}⚠️  ВАЖНЫЕ СЛЕДУЮЩИЕ ШАГИ:${NC}"
echo ""
echo "1. 🔑 Скопируйте приватный ключ кошелька:"
echo "   sudo cp /path/to/wallet.key ${BOT_DIR}/config/"
echo "   sudo chmod 600 ${BOT_DIR}/config/wallet.key"
echo "   sudo chown ${BOT_USER}:${BOT_USER} ${BOT_DIR}/config/wallet.key"
echo ""
echo "2. 📝 Создайте config/settings.yaml с плейсхолдерами:"
echo "   sudo nano ${BOT_DIR}/config/settings.yaml"
echo "   (Используйте {HELIUS_API_KEY} и {PUBLIC_KEY} для подстановки)"
echo ""
echo "3. 🚀 Запустите бота:"
echo "   sudo systemctl start ${BOT_SERVICE}"
echo ""
echo "4. 📊 Проверьте статус:"
echo "   sudo systemctl status ${BOT_SERVICE}"
echo "   sudo journalctl -u ${BOT_SERVICE} -f"
echo ""
echo -e "${GREEN}Управление:${NC}"
echo "  start | stop | restart | status | logs"