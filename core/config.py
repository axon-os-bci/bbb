"""
Модуль загрузки и валидации конфигурации через Pydantic.
Поддерживает подстановку переменных окружения из .env без конфликтов с Python-форматами логов.
"""

import os
import re
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field, validator
import yaml
from dotenv import load_dotenv

# Загружаем .env при импорте модуля
load_dotenv()


class RPCConfig(BaseModel):
    """Конфигурация RPC подключений"""
    http: str
    ws: str
    fallback_http: str
    fallback_ws: str


class WalletConfig(BaseModel):
    """Конфигурация кошелька"""
    public_key: str
    key_path: str


class ProgramsConfig(BaseModel):
    """Program IDs Solana"""
    raydium_amm: str
    token_program: str
    openbook: str = "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX"
    ata_program: str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"


class SolanaConfig(BaseModel):
    """Группировка Solana-настроек"""
    rpc: RPCConfig
    wallet: WalletConfig
    programs: ProgramsConfig


class CopyTradingConfig(BaseModel):
    """Настройки копитрейдинга"""
    enabled: bool = False
    mode: str = "fixed"
    fixed_amount_sol: float = 0.1
    max_sol_per_trade: float = 0.5
    delay_ms: int = 2000
    target_wallets: List[str] = Field(default_factory=list)

    @validator('target_wallets')
    def validate_addresses(cls, v):
        for addr in v:
            if len(addr) < 32 or len(addr) > 44:
                raise ValueError(f"Неверный адрес Solana: {addr}")
        return v


class EntryConfig(BaseModel):
    position_size_sol: float = 0.1
    min_liquidity_sol: float = 5.0


class FiltersConfig(BaseModel):
    check_mint_authority: bool = True
    check_freeze_authority: bool = True
    max_top_holder_percent: float = 30.0
    check_liquidity: bool = True


class StrategyConfig(BaseModel):
    enabled: bool = True
    entry: EntryConfig
    filters: FiltersConfig


class ExitConfig(BaseModel):
    take_profit_percent: float = 50.0
    stop_loss_percent: float = 10.0
    max_hold_time_min: int = 60


class FeesConfig(BaseModel):
    buy: int = 10000
    sell: int = 10000


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/bot.log"
    format: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    max_size_mb: int = 50
    backup_count: int = 3


class DatabaseConfig(BaseModel):
    path: str = "data/trades.db"


class BotConfig(BaseModel):
    """Корневой класс конфигурации"""
    solana: SolanaConfig
    copy_trading: CopyTradingConfig
    strategy: StrategyConfig
    exit: ExitConfig
    fees: FeesConfig
    logging: LoggingConfig
    database: DatabaseConfig


def load_config(config_path: str = "config/settings.yaml") -> BotConfig:
    """
    Загружает конфиг с подстановкой переменных окружения.
    Поддерживает синтаксис ${VAR_NAME} и {VAR_NAME}.
    Сохраняет Python-форматы типа %(asctime)s в логах (не трогает их).
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")

    # Читаем YAML как текст
    with open(path, 'r', encoding='utf-8') as f:
        template = f.read()

    # Проверка обязательных переменных ДО подстановки
    required_vars = ['HELIUS_API_KEY', 'PUBLIC_KEY']
    missing = [var for var in required_vars if var not in os.environ]
    if missing:
        raise ValueError(f"Отсутствуют обязательные переменные окружения: {missing}")

    # Безопасная подстановка: заменяем только ${VAR} или {VAR}, но не %(format)s
    # Используем регулярное выражение для поиска ${VAR} или {VAR}
    pattern = re.compile(r'\$\{(\w+)\}|\{(\w+)\}')

    def replace_var(match):
        var_name = match.group(1) or match.group(2)
        if var_name in os.environ:
            return os.environ[var_name]
        # Если переменная не найдена, оставляем как есть (для Python форматов в логах)
        return match.group(0)

    filled_template = pattern.sub(replace_var, template)

    # Проверяем, остались ли неподставленные {VAR} (не связанные с логированием)
    # Это может быть ошибкой, если это не Python форматирование
    remaining_vars = re.findall(r'\{(?!%\w+|asctime|levelname|name|message)\w+\}', filled_template)
    if remaining_vars:
        # Проверяем, что это не форматы логов
        log_formats = ['%(asctime)s', '%(levelname)s', '%(name)s', '%(message)s']
        for var in remaining_vars[:]:
            if any(fmt in var for fmt in log_formats):
                remaining_vars.remove(var)
        if remaining_vars:
            raise ValueError(f"Не удалось подставить переменные: {remaining_vars}")

    # Парсим YAML
    config_dict = yaml.safe_load(filled_template)

    return BotConfig(**config_dict)


# Singleton для импорта
_config = None


def get_config() -> BotConfig:
    """Возвращает singleton конфигурации"""
    global _config
    if _config is None:
        _config = load_config()
    return _config