# GMGN Solana Trading Bot

Рабочий торговый бот для Solana с реальной интеграцией Raydium AMM v4 + Serum OpenBook.

## Особенности

- **Реальный парсинг структур**: Загружает и парсит AmmInfo и Serum Market из сети по реальным смещениям байт
- **Динамический парсинг Serum**: Читает event_queue/bids/asks с конца структуры (96 байт), работает с любой версией OpenBook
- **WebSocket мониторинг**: Отслеживает новые пулы Raydium в реальном времени
- **Copy-trading**: Следует за сделками указанных кошельков
- **Jupiter Price API**: Получает реальные цены для расчета TP/SL
- **SQLite**: Сохраняет историю и восстанавливается после перезапуска


## Настройка
1. Получите API ключ от Helius (бесплатный тариф)
2. Отредактируйте config/settings.yaml:
- **Вставьте API ключ в .env**
- **Укажите d .env свой публичный адрес**

## Установка
```bash
# Из root, создаем временную папку для установки
mkdir ~/gmgn_deploy && cd ~/gmgn_deploy
# Копируем в деплой-скрипт нужный файл
nano deploy.sh
# Создаем .env файл - нужен для подгрузки файлов из репозитория
nano .env
# Запускаем
chmod +x deploy.sh
./deploy.sh
```

## ОБЯЗАТЕЛЬНО ПОСЛЕ УСТАНОВКИ!
```
# Создаем wallet.key с приватным ключом в base58 и меняем права
sudo chmod 600 /opt/gmgn_bot/config/wallet.key
sudo chown solbot:solbot /opt/gmgn_bot/config/wallet.key
# Создаем .emv и меняем права
sudo chmod 600 /opt/gmgn_bot/.env
sudo chown solbot:solbot /opt/gmgn_bot/.env
```

## Схема базы данных 
```sql
token_address     -- Адрес токена (mint)
entry_price       -- Цена входа
entry_sol_amount  -- Количество SOL на входе
pnl_percent       -- Процент прибыли/убытка
exit_reason       -- Причина выхода (TP/SL/Time)
copied_from       -- Адрес кита (для copy-trading)
pool_id           -- Идентификатор пула Raydium
```

## Запуск
```bash
systemctl start gmgn-bot
```

## Как это работает
1. Поиск пула: Использует Raydium HTTP API для поиска AMM ID по mint адресам
2. Загрузка ключей: Выполняет 2 RPC запроса (AMM account + Serum Market account)
3. Парсинг:
- **AmmInfo: Читает поля по фиксированным смещениям (264+ байт)**
- **Serum Market: Динамически читает последние 96 байт для event_queue/bids/asks**
4. Своп: Строит реальную инструкцию SwapBaseIn с 18 account_metas
5. Мониторинг: Каждую минуту проверяет цену через Jupiter API для TP/SL

## Безопасность
- **Проверка Mint Authority (возможность чеканки)**
- **Проверка Freeze Authority (возможность заморозки)**
- **Лимиты на размер сделок (max_sol_per_trade)**

1. Если хочется использовать основной кошелек OKX:

Ваш бот ожидает приватный ключ в формате Base58 (88 символов для полного keypair или 44 для seed), а не сид-фразу (12/24 слова).
OKX Extension → Настройки → Аккаунт Solana → "Export Private Key" (не "Seed Phrase"). Скопируйте строку длиной ~88 символов и вставьте в config/wallet.key.
Если OKX не дает экспортировать конкретно private key для Solana (а только общую seed-фразу), вам нужно конвертировать:
```bash
# Установите solana-cli
solana-keygen recover 'prompt:?key=0/0' --outfile bot-wallet.json
# Затем извлеките base58: cat bot-wallet.json | jq -r '[.[0:32][]] | @base58'
Важно: OKX использует derivation path m/44'/501'/0'/0' для Solana. Убедитесь, что конвертируете именно первый аккаунт Solana из сид-фразы.
```
2. Главное
НЕ используйте свой основной кошелек OKX для бота, даже если технически можете:
- **Риск 1: Бот пишет ключ в файл wallet.key на диске VPS. Если сервер взломают, украдут ключ.**
- **Риск 2: При ошибке в коде (бесконечный цикл покупок, неправильная сумма) потеряете все средства.**
- **Риск 3: Если используете copy-trading с 9 кошельками, бот будет часто подписывать транзакции — повышается риск утечки ключа через логи или память.**

Рекомендация: Создайте в OKX отдельный аккаунт (sub-account) или отдельный кошелек Phantom специально для бота, перешлите туда только необходимую сумму SOL (например, 1-2 SOL для теста).

4. Права доступа

Если все же используете основной кошелек, обязательно:
```bash
chmod 600 config/wallet.key
chattr +i config/wallet.key  # Защита от удаления/изменения (Linux)
```
5. Альтернатива (Безопаснее)

Создайте новый кошелек через solana-keygen:

```bash
solana-keygen new --outfile config/wallet.key --force
```
Пополните его с вашего OKX кошелька на нужную сумму. Это изолирует риски — даже если бот скомпрометирован, основные средства в OKX останутся в безопасности.

6. Проверка совместимости

OKX использует стандартный ED25519 для Solana, так что с solders.Keypair.from_base58_string() совместимость полная. Но убедитесь, что публичный адрес в логах бота (str(self.keypair.pubkey())) совпадает с адресом в OKX Extension перед первой торговлей.

Итог: Технически можно использовать основной кошелек, но в целях безопасности лучше создать для бота dedicated-кошелек с минимальным балансом, а не используйте основной аккаунт OKX с всеми средствами.

## Требования к боту
- **Python 3.12+**
- **Helius API key (или QuickNode)**
- **Баланс SOL на кошельке (минимум 0.1 для комиссий)**
- **Ubuntu 22.04/24.04 (рекомендуется)**

## Деплой
1. Bash скрипт деплоя deploy.sh

2. Скрипт управления gmgn-control.sh

3. Инструкция по эксплуатации


Ежедневное управление
```bash
# Проверка статуса
sudo systemctl status gmgn-bot

# Просмотр логов в реальном времени
sudo journalctl -u gmgn-bot -f

# Или используя скрипт управления (если скопировали gmgn-control.sh)
sudo ./gmgn-control.sh status
sudo ./gmgn-control.sh logs
sudo ./gmgn-control.sh restart
```

Автоматизация (Cron)
```bash
# Добавьте в cron для автоматического бэкапа
sudo crontab -e

# Строки для добавления:
# Бэкап базы каждые 6 часов
0 */6 * * * /opt/gmgn_bot/gmgn-control.sh backup > /dev/null 2>&1

# Проверка баланса каждое утро (если < 0.1 SOL - отправить уведомление)
0 9 * * * /opt/gmgn_bot/gmgn-control.sh wallet | mail -s "GMGN Bot Balance" admin@yourdomain.com
```

Мониторинг ресурсов
```bash
# Проверка потребления памяти (для 4GB RAM)
ps aux | grep python | awk '{print $4 " " $11}'

# Проверка места на диске (HDD)
df -h /opt/gmgn_bot

# Очистка старых логов (ручная)
sudo journalctl --vacuum-time=7d  # Удалить логи старше 7 дней
```
Экстренная остановка (если бот "сошел с ума")
```bash
# Мгновенная остановка
sudo systemctl stop gmgn-bot

# Или убить процесс принудительно
sudo pkill -f "python main.py"

# Проверка, что остановлен
sudo systemctl status gmgn-bot
```

Обновление без потери данных
```bash
# 1. Остановка
sudo systemctl stop gmgn-bot

# 2. Бэкап
sudo cp /opt/gmgn_bot/data/trades.db /root/trades_backup_$(date +%Y%m%d).db

# 3. Обновление кода (копирование новых файлов)
scp -r gmgn_bot/* root@your-server-ip:/opt/gmgn_bot/

# 4. Обновление зависимостей (если requirements изменился)
cd /opt/gmgn_bot && sudo -u solbot ./venv/bin/pip install -r requirements.txt

# 5. Права
sudo chown -R solbot:solbot /opt/gmgn_bot
sudo chmod 600 /opt/gmgn_bot/config/wallet.key

# 6. Запуск
sudo systemctl start gmgn-bot
```

Безопасность (Чек-лист)
1. Права на ключ: Обязательно chmod 600 на wallet.key
2. Firewall: Только SSH (порт 22) открыт наружу
3. Fail2ban: Установлен и настроен (включено в deploy.sh)
4. Пользователь: Бот работает от пользователя solbot (не root)
5. Логи: Ротируются автоматически (logrotate), не переполнят диск
6. Бэкап: Настройте автоматический бэкап базы (через cron)

Проверка работоспособности после запуска
```bash
# 1. Сервис активен?
sudo systemctl is-active gmgn-bot

# 2. Логи без ошибок?
sudo journalctl -u gmgn-bot --since "5 minutes ago" | tail -20

# 3. База данных растет (есть записи)?
sudo sqlite3 /opt/gmgn_bot/data/trades.db "SELECT COUNT(*) FROM positions;"

# 4. WebSocket подключен (есть входящие данные)?
sudo netstat -tnp | grep ESTABLISHED | grep python
```

## Управление стратегией
### Пример потока
- [Сеть Solana] Новый токен XYZ создает пул

  ↓
- [Raydium Program] Выполняет initialize2

  ↓
- [WebSocket] Бот мгновенно получает логи (0.1-0.5 сек)

  ↓
- [Decoder] Распознает "NEW_POOL", извлекает адрес токена

  ↓
- [Analyzer] Проверяет Mint Authority (0.1 сек)

  ↓
- [Strategy] Создает сигнал BUY

  ↓
- [Executor] Отправляет транзакцию через Raydium AMM

  ↓
- [Вы] Владеете токеном на 2-3 секунде после старта торгов

### Принцип работы 
#### Бот использует два независимых триггера для входа:
### 1. Fresh Pool Sniper (Ловля новых пулов)
Триггер: WebSocket обнаруживает событие initialize2 в логах Raydium (создание нового пула ликвидности)
- Фильтры безопасности (обязательные):
  - Проверка Mint Authority — токен не должен иметь права на дополнительную эмиссию
  - Проверка Freeze Authority — токен не должен иметь права на заморозку переводов
  - Минимальная ликвидность — проверка, что в пуле достаточно SOL (по умолчанию ≥5 SOL)
- Размер позиции: Фиксированная сумма в SOL (по умолчанию 0.1 SOL)
### 2. Copy-Trading (Следование за китами)
- Триггер: Изменение баланса одного из 9 отслеживаемых кошельков (список в конфиге)
- Логика: При обнаружении покупки (BUY) на кошельке-ките, бот повторяет сделку
- Размер позиции:
  - Режим fixed — фиксированная сумма (по умолчанию 0.1 SOL, независимо от суммы кита)
  - Режим proportional — пропорционально сделке кита (10% от его суммы, но не более max)
- Задержка: 2 секунды перед копированием (защита от фронтраннинга)

### 3. Стратегия выхода из позиции (Exit)
После входа бот не использует трейлинг-стоп, а проверяет условия выхода каждые 60 секунд:

#### Take Profit (TP)
- Условие: Цена выросла на +50% от цены входа
- Действие: Полная продажа всей позиции через тот же пул Raydium

#### Stop Loss (SL) — Опционально
- Условие: Цена упала на -10% от цены входа
- Статус: Активен (можно отключить, установив 0)

#### Time Stop
- Условие: Позиция открыта более 60 минут
- Действие: Принудительная продажа (чтобы не держать "мёртвые" токены)

### 4. Где находятся ключевые настройки стратегии?

Все параметры находятся в файле config/settings.yaml по следующим путям:
Включение/отключение стратегий

```
#yaml
strategy:
  enabled: true                    # true/false — включить ловлю новых пулов
  
copy_trading:
  enabled: true                    # true/false — включить копитрейдинг
  mode: "fixed"                    # "fixed" или "proportional" — режим копирования
```

Параметры входа (Entry)
```
strategy:
  entry:
    position_size_sol: 0.1         # Размер покупки в SOL (для Fresh Pool)
    min_liquidity_sol: 5.0         # Минимальная ликвидность пула для входа
  
  filters:
    check_mint_authority: true     # Проверять ли Mint Authority (true/false)
    check_freeze_authority: true   # Проверять ли Freeze Authority
    max_top_holder_percent: 30.0   # Макс % у топ-холдера (не используется в текущей версии)

copy_trading:
  fixed_amount_sol: 0.1            # Сумма для копирования в режиме fixed
  max_sol_per_trade: 0.5           # Лимит на одну сделку (не купит больше этого)
  delay_ms: 2000                   # Задержка перед копированием (милисекунды)
  
  target_wallets:                  # Список отслеживаемых кошельков (адреса)
    - "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f"
    - "DkpMvCWwdNZL5UScy1eKjMkQCsyeFjhMLG2WTtmE9zQ"
    # ... остальные адреса
```


Параметры выхода (Exit)
```
exit:
  take_profit_percent: 50.0        # Тейк-профит в процентах (50 = +50%)
  stop_loss_percent: 10.0          # Стоп-лосс (0 = отключен)
  max_hold_time_min: 60            # Максимальное время удержания (0 = бесконечно)
```
Комиссии (влияют на скорость исполнения)
```
fees:
  buy: 10000      # Priority fee для покупки (в lamports, 10000 = 0.00001 SOL)
  sell: 10000     # Priority fee для продажи
```

4. Быстрая настройка под разные стили торговли

Консервативный (безопасный)
```
#yaml
strategy:
  entry:
    position_size_sol: 0.05        # Маленькие позиции
  filters:
    check_mint_authority: true     # Строгие фильтры
    check_freeze_authority: true

exit:
  take_profit_percent: 20.0        # Быстрый фикс прибыли +20%
  stop_loss_percent: 5.0           # Жесткий стоп -5%
  max_hold_time_min: 15            # Не держать дольше 15 минут
```

Агрессивный (холд до луны)
```
strategy:
  entry:
    position_size_sol: 0.5         # Крупные позиции
  filters:
    check_mint_authority: false    # Меньше фильтров, больше сделок

exit:
  take_profit_percent: 100.0       # Ждем +100% (удвоение)
  stop_loss_percent: 0             # Без стопа (или 20%)
  max_hold_time_min: 0             # Бесконечный холд
```

Только Copy-Trading (без ловли пулов)
```
strategy:
  enabled: false                   # Отключаем снайпинг пулов
  
copy_trading:
  enabled: true
  fixed_amount_sol: 0.2            # Копируем крупнее
  delay_ms: 500                    # Меньше задержки (быстрее копируем)
```

Важно: После изменения settings.yaml необходимо перезапустить бота:
```bash
sudo systemctl restart gmgn-bot
```

## Требования к серверу
### Рекомендуемые
| Компонент   | Спецификация                             | Эффект                                             |
| ----------- | ---------------------------------------- | -------------------------------------------------- |
| **CPU**     | 2 vCPU (shared) или 1 dedicated          | Запас на пиковые нагрузки при копировании сделок   |
| **RAM**     | **4 GB**                                 | Комфортная работа с SQLite, место для роста базы   |
| **Диск**    | 20 GB SSD                                | Быстрее запись логов и БД, важно для HDD-хостингов |
| **Сеть**    | 100 Mbps, latency < 50ms до RPC          | Быстрая отправка транзакций при копитрейдинге      |
| **Локация** | США (Ashburn, NY) или Европа (Frankfurt) | Ближе к серверам Helius/QuickNode                  |

### Оптимальные (для агрессивного скальпинга)
| Компонент | Спецификация                   | Для чего                                              |
| --------- | ------------------------------ | ----------------------------------------------------- |
| **CPU**   | 2-4 dedicated cores (AMD EPYC) | Если планируете одновременно мониторить 50+ кошельков |
| **RAM**   | 8 GB                           | Кэширование цен, большая история сделок               |
| **Диск**  | 50 GB NVMe                     | Мгновенная запись при высокой частоте сделок          |
| **Сеть**  | 1 Gbps, < 20ms до RPC          | Фронтраннинг других ботов (требует premium RPC)       |

### Что НЕ подойдет (красные флаги)
❌ Raspberry Pi Zero (слишком медленный CPU для криптографии)

❌ Shared хостинг (нельзя установить systemd сервис, нет SSH)

❌ Windows Server (работает, но сложнее с автозапуском, больше ресурсов жрет сама ОС)

❌ Мобильный интернет/4G (WebSocket будет рваться, вы пропустите входы)

❌ Сервер в Китае/России с блокировками (проблемы с подключением к api.raydium.io и Helius)