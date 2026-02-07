"""
Декодер логов транзакций Raydium.
Преобразует сырые текстовые логи Solana в структурированные события.
"""

import re
import logging
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RaydiumEvent:
    """Структурированное событие из логов Raydium"""
    event_type: str  # NEW_POOL, SWAP, ADD_LIQUIDITY, REMOVE_LIQUIDITY
    pool_address: Optional[str] = None  # Адрес пула (если удалось извлечь)
    token_address: Optional[str] = None  # Адрес токена (mint)
    signature: str = ""  # Подпись транзакции (для отслеживания)


class LogDecoder:
    """Декодирование инструкций Raydium AMM из логов"""

    @classmethod
    def decode(cls, logs: List[str], signature: str) -> Optional[RaydiumEvent]:
        """
        Основной метод декодирования.
        Анализирует логи и определяет тип события по ключевым словам.

        Args:
            logs: список строк логов транзакции
            signature: подпись транзакции (для отладки)

        Returns:
            RaydiumEvent или None если событие не распознано
        """
        # Объединяем логи в одну строку для поиска паттернов
        log_text = " ".join(logs).lower()

        # Проверка на ошибки выполнения (пропускаем failed транзакции)
        if "error" in log_text or "failed" in log_text:
            logger.debug(f"Транзакция {signature[:8]}... содержит ошибку, пропускаем")
            return None

        # Проверка на создание нового пула (initialize2)
        if "initialize2" in log_text:
            # Пытаемся извлечь адрес пула из логов
            pool_match = re.search(r'initialize2[:\s]+([A-Za-z0-9]{32,44})', " ".join(logs))
            pool_addr = pool_match.group(1) if pool_match else None

            logger.info(f"Обнаружено создание нового пула: {signature[:16]}...")
            return RaydiumEvent(
                event_type="NEW_POOL",
                pool_address=pool_addr,
                signature=signature
            )

        # Проверка на своп (обмен токенов)
        if any(x in log_text for x in ["swapbasein", "swapbaseout", "swap"]):
            return RaydiumEvent(event_type="SWAP", signature=signature)

        # Проверка на добавление ликвидности
        if "deposit" in log_text:
            return RaydiumEvent(event_type="ADD_LIQUIDITY", signature=signature)

        # Проверка на удаление ликвидности (важно для сигналов выхода)
        if "withdraw" in log_text:
            return RaydiumEvent(event_type="REMOVE_LIQUIDITY", signature=signature)

        return None

    @staticmethod
    def extract_mint_from_logs(logs: List[str]) -> Optional[str]:
        """
        Извлечение mint адреса токена из логов инициализации.
        В реальности парсится из инструкции initialize2.
        """
        log_text = " ".join(logs)
        # Ищем все похожие на Pubkey строки (base58, длина 32-44)
        matches = re.findall(r'[A-Za-z0-9]{32,44}', log_text)

        # Фильтрация: исключаем очевидно невалидные адреса (Program IDs и т.д.)
        excluded = [
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "11111111111111111111111111111111",
            "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
        ]

        valid = [m for m in matches if len(m) >= 32 and m not in excluded]
        return valid[0] if valid else None