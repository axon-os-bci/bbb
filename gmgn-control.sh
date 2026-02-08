#!/bin/bash
# Утилита управления ботом GMGN

SERVICE="gmgn-bot"
BOT_DIR="/opt/gmgn_bot"
USER="solbot"

show_help() {
    echo "GMGN Bot Control Utility"
    echo ""
    echo "Использование: $0 {start|stop|restart|status|logs|config|backup|update|wallet}"
    echo ""
    echo "Команды:"
    echo "  start     - Запустить бота"
    echo "  stop      - Остановить бота"
    echo "  restart   - Перезапустить бота"
    echo "  status    - Показать статус и статистику"
    echo "  logs      - Показать логи в реальном времени (Ctrl+C для выхода)"
    echo "  config    - Открыть конфигурацию в nano"
    echo "  backup    - Создать бэкап базы данных"
    echo "  update    - Обновить бота (git pull + restart)"
    echo "  wallet    - Проверить баланс кошелька бота"
    echo ""
}

case "$1" in
    start)
        echo "Запуск бота..."
        sudo systemctl start ${SERVICE}
        sleep 2
        sudo systemctl status ${SERVICE} --no-pager
        ;;

    stop)
        echo "Остановка бота..."
        sudo systemctl stop ${SERVICE}
        echo "Бот остановлен"
        ;;

    restart)
        echo "Перезапуск бота..."
        sudo systemctl restart ${SERVICE}
        sleep 2
        sudo systemctl status ${SERVICE} --no-pager
        ;;

    status)
        echo "=== Статус сервиса ==="
        sudo systemctl status ${SERVICE} --no-pager

        echo ""
        echo "=== Последние позиции ==="
        if [ -f "${BOT_DIR}/data/trades.db" ]; then
            # Исправленный SQL под реальную схему БД
            sudo sqlite3 ${BOT_DIR}/data/trades.db \
                "SELECT token_address, entry_sol_amount, COALESCE(pnl_percent, 0), exit_reason
                 FROM positions ORDER BY rowid DESC LIMIT 5;" 2>/dev/null || echo "База данных пуста или ошибка доступа"
        else
            echo "База данных не найдена"
        fi

        echo ""
        echo "=== Дисковое пространство ==="
        df -h ${BOT_DIR}

        echo ""
        echo "=== Процессы Python ==="
        ps aux | grep -E "(python|gmgn)" | grep -v grep || echo "Процессы не найдены"
        ;;

    logs)
        echo "Просмотр логов (Ctrl+C для выхода)..."
        sudo journalctl -u ${SERVICE} -f --output=short
        ;;

    config)
        echo "Открытие конфигурации..."
        if [ -f "${BOT_DIR}/config/settings.yaml" ]; then
            sudo nano ${BOT_DIR}/config/settings.yaml
            echo "Перезапуск бота для применения изменений..."
            sudo systemctl restart ${SERVICE}
        else
            echo "Ошибка: Файл конфигурации не найден!"
        fi
        ;;

    backup)
        BACKUP_DIR="${BOT_DIR}/backups"
        mkdir -p ${BACKUP_DIR}
        TIMESTAMP=$(date +%Y%m%d_%H%M%S)
        BACKUP_FILE="${BACKUP_DIR}/trades_${TIMESTAMP}.db"

        echo "Создание бэкапа базы данных..."
        if [ -f "${BOT_DIR}/data/trades.db" ]; then
            sudo cp ${BOT_DIR}/data/trades.db ${BACKUP_FILE}
            sudo gzip ${BACKUP_FILE}
            echo "Бэкап создан: ${BACKUP_FILE}.gz"

            # Удаление старых бэкапов (оставляем последние 5)
            ls -t ${BACKUP_DIR}/trades_*.gz 2>/dev/null | tail -n +6 | xargs -r sudo rm
        else
            echo "Ошибка: База данных не найдена"
        fi
        ;;

    update)
        echo "Обновление бота..."
        sudo systemctl stop ${SERVICE}

        cd ${BOT_DIR}
        if [ -d ".git" ]; then
            sudo -u ${USER} git pull
        else
            echo "Предупреждение: Не git репозиторий. Пропускаю git pull."
        fi

        echo "Обновление зависимостей..."
        sudo -u ${USER} ${BOT_DIR}/venv/bin/pip install -r requirements.txt --upgrade

        echo "Запуск бота..."
        sudo systemctl start ${SERVICE}
        echo "Обновление завершено"
        ;;

    wallet)
        echo "Проверка баланса кошелька..."
        if [ -f "${BOT_DIR}/venv/bin/python" ]; then
            sudo -u ${USER} ${BOT_DIR}/venv/bin/python -c "
import sys
sys.path.insert(0, '${BOT_DIR}')
import asyncio
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
import os

async def check():
    try:
        # Пытаемся прочитать из окружения или config
        pubkey_str = os.environ.get('PUBLIC_KEY', '')
        if not pubkey_str:
            # Читаем из settings.yaml если есть
            import yaml
            with open('${BOT_DIR}/config/settings.yaml', 'r') as f:
                config = yaml.safe_load(f)
                pubkey_str = config['solana']['wallet']['public_key']

        pubkey = Pubkey.from_string(pubkey_str)
        client = AsyncClient('https://api.mainnet-beta.solana.com')
        resp = await client.get_balance(pubkey)
        balance = resp.value / 1e9
        print(f'Адрес: {pubkey_str}')
        print(f'Баланс: {balance:.4f} SOL')
        await client.close()
    except Exception as e:
        print(f'Ошибка: {e}')

asyncio.run(check())
"
        fi
        ;;

    *)
        show_help
        exit 1
        ;;
esac