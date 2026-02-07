"""
Модуль загрузки и валидации конфигурации через Pydantic.
Проверяет корректность адресов Solana и числовых параметров.
"""

from pathlib import Path
from typing import List
from pydantic import BaseModel, Field, validator
from pydantic_settings import BaseSettings
import yaml


class RPCConfig(BaseModel):
    """Конфигурация RPC подключений (HTTP и WebSocket)"""
    http: str
    ws: str
    fallback_http: str
    fallback_ws: str


class WalletConfig(BaseModel):
    """Конфигурация кошелька трейдера"""
    public_key: str
    key_path: str


class ProgramsConfig(BaseModel):
    """Pubkeys системных программ Solana"""
    raydium_amm: str = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
    token_program: str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


class SolanaConfig(BaseModel):
    """Группировка Solana-настроек"""
    rpc: RPCConfig
    wallet: WalletConfig
    programs: ProgramsConfig


class CopyTradingConfig(BaseModel):
    """Настройки модуля копитрейдинга"""
    enabled: bool = False
    mode: str = "fixed"  # fixed или proportional
    fixed_amount_sol: float = 0.1
    max_sol_per_trade: float = 0.5
    delay_ms: int = 2000
    target_wallets: List[str] = Field(default_factory=list)

    @validator('target_wallets')
    def validate_addresses(cls, v):
        """Проверка формата Solana-адресов (base58, длина 32-44 символа)"""
        for addr in v:
            if len(addr) < 32 or len(addr) > 44:
                raise ValueError(f"Неверный адрес Solana: {addr}")
        return v


class EntryConfig(BaseModel):
    """Параметры входа в позицию"""
    position_size_sol: float = 0.1
    min_liquidity_sol: float = 5.0


class FiltersConfig(BaseModel):
    """Фильтры безопасности токенов"""
    check_mint_authority: bool = True
    check_freeze_authority: bool = True
    max_top_holder_percent: float = 30.0
    check_liquidity: bool = True


class StrategyConfig(BaseModel):
    """Конфигурация торговой стратегии"""
    enabled: bool = True
    entry: EntryConfig
    filters: FiltersConfig


class ExitConfig(BaseModel):
    """Параметры выхода из позиции"""
    take_profit_percent: float = 50.0
    stop_loss_percent: float = 10.0
    max_hold_time_min: int = 60


class FeesConfig(BaseModel):
    """Размеры комиссий для транзакций (priority fees)"""
    buy: int = 10000
    sell: int = 10000


class LoggingConfig(BaseModel):
    """Настройки логирования"""
    level: str = "INFO"
    file: str = "logs/bot.log"
    format: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    max_size_mb: int = 50
    backup_count: int = 3


class DatabaseConfig(BaseModel):
    """Путь к файлу SQLite базы данных"""
    path: str = "data/trades.db"


class BotConfig(BaseSettings):
    """Корневой класс конфигурации, объединяющий все группы настроек"""
    solana: SolanaConfig
    copy_trading: CopyTradingConfig
    strategy: StrategyConfig
    exit: ExitConfig
    fees: FeesConfig
    logging: LoggingConfig
    database: DatabaseConfig

    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"


def load_config(config_path: str = "config/settings.yaml") -> BotConfig:
    """
    Загружает конфигурацию из YAML файла.

    Args:
        config_path: путь к файлу конфигурации

    Returns:
        Валидированный объект BotConfig

    Raises:
        FileNotFoundError: если файл не найден
        ValidationError: если параметры невалидны
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")

    with open(path, 'r', encoding='utf-8') as f:
        config_dict = yaml.safe_load(f)

    return BotConfig(**config_dict)