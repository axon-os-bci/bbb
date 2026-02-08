"""
Управление состоянием бота и торговыми позициями.
Хранит открытые позиции в памяти (RAM) для быстрого доступа.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Set
from enum import Enum


class PositionStatus(Enum):
    """Статусы торговой позиции"""
    OPEN = "open"  # Позиция открыта, отслеживаем PnL
    CLOSED = "closed"  # Позиция закрыта по TP/SL/времени
    PENDING = "pending"  # Ожидает подтверждения в блокчейне


@dataclass
class Position:
    """
    Модель торговой позиции.
    Хранит всю информацию для расчета прибыли и восстановления после перезапуска.
    """
    token_address: str  # Mint адрес токена
    entry_price: float  # Цена входа (в SOL или USD)
    entry_sol_amount: float  # Сколько SOL было потрачено на вход
    token_amount: float  # Сколько токенов куплено
    entry_time: datetime  # Время открытия позиции
    status: PositionStatus = PositionStatus.OPEN
    exit_price: Optional[float] = None  # Цена выхода (заполняется при закрытии)
    exit_time: Optional[datetime] = None  # Время закрытия
    pnl_percent: float = 0.0  # Процент прибыли/убытка
    exit_reason: Optional[str] = None  # Причина закрытия (TP/SL/Time/Manual)
    copied_from: Optional[str] = None  # Адрес кошелька-источника (для copy-trading)
    pool_id: Optional[str] = None  # ID пула Raydium (для повторной продажи)


class BotState:
    """
    Центральное хранилище состояния бота в оперативной памяти.
    Использует паттерн Singleton (один экземпляр на приложение).
    """

    def __init__(self):
        # Словарь открытых позиций: ключ = адрес токена, значение = Position
        self.positions: Dict[str, Position] = {}

        # Множество для быстрой проверки: отслеживаем ли мы данный токен
        self.tracked_tokens: Set[str] = set()

        # Флаги состояния работы
        self.is_running: bool = False
        self.last_block: int = 0

        # Статистика сессии
        self.total_trades: int = 0
        self.successful_sells: int = 0

    def add_position(self, position: Position) -> None:
        """Добавление новой позиции в портфель"""
        self.positions[position.token_address] = position
        self.tracked_tokens.add(position.token_address)
        self.total_trades += 1

    def close_position(self, token_address: str, exit_price: float,
                       reason: str) -> Optional[Position]:
        """
        Закрытие позиции с расчетом финансового результата.

        Args:
            token_address: адрес токена
            exit_price: цена продажи
            reason: причина закрытия (для логов)

        Returns:
            Объект закрытой позиции или None если позиция не найдена/уже закрыта
        """
        if token_address not in self.positions:
            return None

        pos = self.positions[token_address]
        if pos.status != PositionStatus.OPEN:
            return None

        pos.status = PositionStatus.CLOSED
        pos.exit_price = exit_price
        pos.exit_time = datetime.utcnow()
        pos.exit_reason = reason

        # Расчет процента прибыли: (выход - вход) / вход * 100
        if pos.entry_price > 0:
            pos.pnl_percent = ((exit_price - pos.entry_price) / pos.entry_price) * 100

        if pos.pnl_percent > 0:
            self.successful_sells += 1

        # Удаляем из активного отслеживания (но оставляем в словаре для истории)
        self.tracked_tokens.discard(token_address)
        return pos

    def get_position(self, token_address: str) -> Optional[Position]:
        """Получение позиции по адресу токена"""
        return self.positions.get(token_address)

    def has_open_position(self, token_address: str) -> bool:
        """Проверка, есть ли открытая позиция по данному токену"""
        pos = self.positions.get(token_address)
        return pos is not None and pos.status == PositionStatus.OPEN

    def get_open_positions(self) -> Dict[str, Position]:
        """Возвращает словарь только открытых позиций"""
        return {
            addr: pos for addr, pos in self.positions.items()
            if pos.status == PositionStatus.OPEN
        }

    def update_position_price(self, token_address: str, current_price: float):
        """
        Обновление текущей цены для расчета PnL в реальном времени.
        Используется при мониторинге TP/SL.
        """
        pos = self.positions.get(token_address)
        if pos and pos.status == PositionStatus.OPEN:
            if pos.entry_price > 0:
                pos.pnl_percent = ((current_price - pos.entry_price) / pos.entry_price) * 100