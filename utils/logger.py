"""
Настройка логирования с ротацией файлов.
Важно для долгой работы бота (не переполняет диск).
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from core.config import LoggingConfig


def setup_logging(config: LoggingConfig):
    """
    Инициализация логирования в файл и консоль.

    Args:
        config: настройки логирования из конфига
    """
    log_path = Path(config.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(config.format)

    # Файловый handler с ротацией (чтобы не переполнять диск)
    file_handler = logging.handlers.RotatingFileHandler(
        config.file,
        maxBytes=config.max_size_mb * 1024 * 1024,
        backupCount=config.backup_count,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)

    # Консольный handler (вывод в терминал)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Корневой logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.level.upper()))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Уменьшаем шум от библиотек
    logging.getLogger("solana").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)