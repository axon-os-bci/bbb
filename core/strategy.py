"""
Торговая стратегия и логика принятия решений.
Определяет когда входить в позицию и когда выходить (TP/SL).
"""

import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from core.config import BotConfig
from core.state import BotState, Position, PositionStatus
from core.analyzer import TokenAnalyzer, SafetyReport

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """
    Торговый сигнал на покупку или продажу.
    Создается стратегией и исполняется Executor'ом.
    """
    action: str  # "BUY" или "SELL"
    token_address: str
    amount_sol: float  # Для BUY: сколько тратить, для SELL: обычно 0 (продать все)
    reason: str  # Причина сигнала (для логов)
    confidence: float = 1.0  # Уверенность 0.0-1.0
    copied_from: Optional[str] = None  # Для copy-trading: адрес кита
    pool_id: Optional[str] = None  # ID пула для покупки


class Strategy:
    """
    Центральный класс торговой стратегии.
    """

    def __init__(self, config: BotConfig, state: BotState, analyzer: TokenAnalyzer):
        self.config = config
        self.state = state
        self.analyzer = analyzer

    async def on_new_pool(self, token_address: str, pool_address: str) -> Optional[TradeSignal]:
        """
        Стратегия входа при обнаружении нового пула Raydium.

        Логика:
        1. Проверяем, нет ли уже открытой позиции по этому токену
        2. Проверяем безопасность токена (mint/freeze authority)
        3. Если проходит фильтры - создаем сигнал на покупку

        Args:
            token_address: Mint адрес токена в пуле
            pool_address: Адрес пула ликвидности (AMM ID)

        Returns:
            TradeSignal для покупки или None
        """
        if not self.config.strategy.enabled:
            return None

        # Проверка: нет ли уже открытой позиции
        if self.state.has_open_position(token_address):
            return None

        # Быстрая проверка безопасности (1-2 сек)
        safety = await self.analyzer.quick_check(token_address)

        if not safety.is_safe:
            logger.warning(f"Токен {token_address[:8]}... отклонен: {safety.risks}")
            return None

        # Создаем сигнал на покупку
        return TradeSignal(
            action="BUY",
            token_address=token_address,
            amount_sol=self.config.strategy.entry.position_size_sol,
            reason=f"Новый пул, проверки пройдены: {safety.risks if safety.risks else 'OK'}",
            confidence=0.8,
            pool_id=pool_address
        )

    def on_copy_trade(self, source_wallet: str, token_address: str,
                      action: str, amount: float) -> Optional[TradeSignal]:
        """
        Обработка сигнала от copy-trading модуля.

        Args:
            source_wallet: Адрес кошелька-источника (кита)
            token_address: Адрес токена
            action: "BUY" или "SELL" (что сделал кит)
            amount: Сумма сделки источника в SOL (для расчета пропорции)

        Returns:
            TradeSignal если решили копировать сделку
        """
        if not self.config.copy_trading.enabled:
            return None

        # Проверка, что кошелек в списке отслеживаемых
        if source_wallet not in self.config.copy_trading.target_wallets:
            return None

        if action == "BUY":
            # Проверка: нет ли уже позиции
            if self.state.has_open_position(token_address):
                return None

            # Определяем размер позиции
            if self.config.copy_trading.mode == "fixed":
                trade_amount = min(
                    self.config.copy_trading.fixed_amount_sol,
                    self.config.copy_trading.max_sol_per_trade
                )
            else:
                # Пропорциональный режим: 10% от суммы кита
                trade_amount = min(amount * 0.1, self.config.copy_trading.max_sol_per_trade)

            return TradeSignal(
                action="BUY",
                token_address=token_address,
                amount_sol=trade_amount,
                reason=f"Copy-trade от {source_wallet[:8]}...",
                confidence=0.7,
                copied_from=source_wallet
            )

        elif action == "SELL":
            # Для продажи проверяем, есть ли у нас эта позиция
            if self.state.has_open_position(token_address):
                return TradeSignal(
                    action="SELL",
                    token_address=token_address,
                    amount_sol=0,  # Продадим все что есть
                    reason=f"Copy-sell от {source_wallet[:8]}...",
                    confidence=0.7,
                    copied_from=source_wallet
                )

        return None

    def check_exit_conditions(self, position: Position, current_price: float) -> Optional[TradeSignal]:
        """
        Проверка условий выхода из позиции (TP/SL/Time).
        Вызывается периодически для каждой открытой позиции.

        Args:
            position: Объект открытой позиции
            current_price: Текущая цена токена (полученная извне)

        Returns:
            TradeSignal для продажи или None
        """
        if position.status != PositionStatus.OPEN:
            return None

        if position.entry_price <= 0 or current_price <= 0:
            return None

        # Расчет текущей прибыли/убытка в процентах
        pnl_percent = ((current_price - position.entry_price) / position.entry_price) * 100

        # Проверка Take Profit (+50% по умолчанию)
        if pnl_percent >= self.config.exit.take_profit_percent:
            return TradeSignal(
                action="SELL",
                token_address=position.token_address,
                amount_sol=0,
                reason=f"Take Profit {pnl_percent:.1f}% достигнут",
                confidence=1.0
            )

        # Проверка Stop Loss (если включен)
        if self.config.exit.stop_loss_percent > 0:
            if pnl_percent <= -self.config.exit.stop_loss_percent:
                return TradeSignal(
                    action="SELL",
                    token_address=position.token_address,
                    amount_sol=0,
                    reason=f"Stop Loss {pnl_percent:.1f}%",
                    confidence=1.0
                )

        # Проверка времени удержания (если включено)
        if self.config.exit.max_hold_time_min > 0:
            hold_time = (datetime.utcnow() - position.entry_time).total_seconds() / 60
            if hold_time >= self.config.exit.max_hold_time_min:
                return TradeSignal(
                    action="SELL",
                    token_address=position.token_address,
                    amount_sol=0,
                    reason=f"Time limit {hold_time:.0f} минут",
                    confidence=0.9
                )

        return None