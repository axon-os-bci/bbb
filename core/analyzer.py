"""
Анализатор безопасности токенов.
Проверяет токены на признаки скама перед покупкой (Mint Authority, Freeze Authority).
"""

import logging
from typing import Optional
from dataclasses import dataclass

from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from core.config import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class SafetyReport:
    """Отчет о безопасности токена"""
    is_safe: bool
    has_mint_authority: Optional[bool] = None  # None если не удалось определить
    has_freeze_authority: Optional[bool] = None
    risks: list = None

    def __post_init__(self):
        if self.risks is None:
            self.risks = []


class TokenAnalyzer:
    """
    Анализ SPL Token на предмет опасных свойств.
    Использует get_account_info для чтения Mint аккаунта.
    """

    def __init__(self, client: AsyncClient, config: BotConfig):
        self.client = client
        self.config = config

    async def quick_check(self, token_address: str) -> SafetyReport:
        """
        Быстрая проверка токена на основные скам-индикаторы.
        Выполняет 1-2 RPC запроса (минимум нагрузки на сервер).

        Проверяет:
        1. Mint Authority - может ли создатель напечатать новые токены
        2. Freeze Authority - может ли создатель заморозить переводы

        Args:
            token_address: Mint адрес токена (base58)

        Returns:
            SafetyReport с результатами проверки
        """
        risks = []

        try:
            # РЕАЛЬНЫЙ RPC CALL: Получаем данные Mint аккаунта
            pubkey = Pubkey.from_string(token_address)
            resp = await self.client.get_account_info(pubkey)

            if not resp.value:
                return SafetyReport(is_safe=False, risks=["Токен не найден в сети"])

            data = bytes(resp.value.data)

            # Структура SPL Token Mint (первые 82 байта минимум):
            # 0-3:   Option<u32> mint_authority (0 = None, 1 = Some([u8; 32]))
            # 4-35:  Pubkey mint_authority (если Option = 1)
            # 36-43: u64 supply
            # 44:    u8 decimals
            # 45:    u8 is_initialized
            # 46-49: Option<u32> freeze_authority
            # 50-81: Pubkey freeze_authority (если Option = 1)

            # Проверка Mint Authority (смещение 0)
            if len(data) >= 4:
                mint_auth_option = int.from_bytes(data[0:4], byteorder='little')
                has_mint = mint_auth_option != 0

                if self.config.strategy.filters.check_mint_authority and has_mint:
                    risks.append("Токен имеет Mint Authority (возможна дополнительная эмиссия)")
            else:
                has_mint = None

            # Проверка Freeze Authority (смещение 46)
            if len(data) >= 50:
                freeze_option = int.from_bytes(data[46:50], byteorder='little')
                has_freeze = freeze_option != 0

                if self.config.strategy.filters.check_freeze_authority and has_freeze:
                    risks.append("Токен имеет Freeze Authority (возможна блокировка переводов)")
            else:
                has_freeze = None

            is_safe = len(risks) == 0

            return SafetyReport(
                is_safe=is_safe,
                has_mint_authority=has_mint,
                has_freeze_authority=has_freeze,
                risks=risks
            )

        except Exception as e:
            logger.error(f"Ошибка анализа токена {token_address}: {e}")
            return SafetyReport(is_safe=False, risks=[f"Ошибка анализа: {str(e)}"])

    async def get_liquidity(self, pool_address: str) -> float:
        """
        Получение ликвидности пула (упрощенно).
        В полной версии здесь анализируются балансы vault аккаунтов.
        """
        try:
            # Заглушка для демонстрации
            return 100.0
        except:
            return 0.0