"""
SQLite хранилище для персистентности данных.
Хранит историю сделок и позволяет восстановить состояние после перезапуска.
"""

import aiosqlite
import logging
from pathlib import Path
from typing import Optional, List, Dict

from core.state import Position, PositionStatus

logger = logging.getLogger(__name__)


class Database:
    """Асинхронная SQLite база данных"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.conn = None

    async def init(self):
        """Инициализация таблиц базы данных"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row

        # Создание таблицы позиций
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                entry_price REAL,
                entry_sol_amount REAL,
                token_amount REAL,
                entry_time TIMESTAMP,
                exit_price REAL,
                exit_time TIMESTAMP,
                pnl_percent REAL,
                exit_reason TEXT,
                copied_from TEXT,
                status TEXT DEFAULT 'open',
                pool_id TEXT
            )
        """)

        # Индекс для быстрого поиска по токену
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_token ON positions(token_address)
        """)

        await self.conn.commit()
        logger.info("База данных инициализирована")

    async def save_position(self, position: Position):
        """Сохранение новой позиции (покупка)"""
        await self.conn.execute("""
            INSERT INTO positions 
            (token_address, entry_price, entry_sol_amount, token_amount, 
             entry_time, copied_from, status, pool_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.token_address,
            position.entry_price,
            position.entry_sol_amount,
            position.token_amount,
            position.entry_time,
            position.copied_from,
            position.status.value,
            position.pool_id
        ))
        await self.conn.commit()

    async def update_position_exit(self, position: Position):
        """Обновление позиции при закрытии (продажа)"""
        await self.conn.execute("""
            UPDATE positions 
            SET exit_price = ?, exit_time = ?, pnl_percent = ?, 
                exit_reason = ?, status = ?
            WHERE token_address = ? AND status = 'open'
        """, (
            position.exit_price,
            position.exit_time,
            position.pnl_percent,
            position.exit_reason,
            position.status.value,
            position.token_address
        ))
        await self.conn.commit()

    async def get_open_positions(self) -> List[Dict]:
        """Загрузка всех открытых позиций (для восстановления после рестарта)"""
        async with self.conn.execute(
                "SELECT * FROM positions WHERE status = 'open'"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def close(self):
        """Закрытие соединения с базой"""
        if self.conn:
            await self.conn.close()