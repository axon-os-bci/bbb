"""
Точка входа в бота.
Инициализирует все компоненты и запускает основной цикл.
"""

import asyncio
import logging
import signal
from pathlib import Path
from datetime import datetime

from solders.keypair import Keypair
from solana.rpc.async_api import AsyncClient

from core.config import load_config, BotConfig
from core.state import BotState, Position, PositionStatus
from core.listener import SolanaListener
from core.decoder import LogDecoder, RaydiumEvent
from core.analyzer import TokenAnalyzer
from core.strategy import Strategy, TradeSignal
from core.executor import TradeExecutor
from storage.database import Database
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


class GMGNBot:
    """Основной класс торгового бота"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.state = BotState()
        self.db = Database(config.database.path)
        self.client = AsyncClient(config.solana.rpc.http)
        self.executor = None
        self.keypair = None
        self.running = False

    async def initialize(self):
        """Инициализация всех компонентов"""
        setup_logging(self.config.logging)
        logger.info("=== Инициализация GMGN Bot ===")

        await self._load_wallet()
        await self.db.init()
        await self._restore_positions()

        self.analyzer = TokenAnalyzer(self.client, self.config)
        self.strategy = Strategy(self.config, self.state, self.analyzer)
        self.executor = TradeExecutor(
            self.client, self.config, self.state, self.keypair
        )

        self.listener = SolanaListener(self.config)
        self.listener.add_raydium_callback(self._on_raydium_event)

        if self.config.copy_trading.enabled:
            self.listener.add_copy_trade_callback(self._on_copy_trade)
            logger.info(f"Copy-trading активен: {len(self.config.copy_trading.target_wallets)} кошельков")

        logger.info("Инициализация завершена")

    async def _load_wallet(self):
        """Загрузка приватного ключа из файла"""
        key_path = Path(self.config.solana.wallet.key_path)
        if not key_path.exists():
            raise FileNotFoundError(f"Файл ключа не найден: {key_path}")

        with open(key_path, 'r') as f:
            key_data = f.read().strip()

        self.keypair = Keypair.from_base58_string(key_data)
        logger.info(f"Кошелек загружен: {str(self.keypair.pubkey())[:16]}...")

    async def _restore_positions(self):
        """Восстановление открытых позиций из БД после рестарта"""
        positions = await self.db.get_open_positions()

        for pos_data in positions:
            position = Position(
                token_address=pos_data['token_address'],
                entry_price=pos_data['entry_price'] or 0.0,
                entry_sol_amount=pos_data['entry_sol_amount'],
                token_amount=pos_data['token_amount'] or 0.0,
                entry_time=datetime.fromisoformat(pos_data['entry_time']),
                status=PositionStatus.OPEN,
                copied_from=pos_data['copied_from'],
                pool_id=pos_data.get('pool_id')
            )
            self.state.positions[position.token_address] = position
            self.state.tracked_tokens.add(position.token_address)

        logger.info(f"Восстановлено позиций: {len(positions)}")

    async def _on_raydium_event(self, logs: list, signature: str):
        """Обработка событий Raydium (новые пулы)"""
        try:
            event = LogDecoder.decode(logs, signature)
            if not event:
                return

            if event.event_type == "NEW_POOL":
                token = LogDecoder.extract_mint_from_logs(logs)
                if token:
                    signal = await self.strategy.on_new_pool(token, event.pool_address or "")
                    if signal:
                        await self._execute(signal)

        except Exception as e:
            logger.error(f"Ошибка обработки Raydium: {e}")

    async def _on_copy_trade(self, account_data: dict, wallet: str):
        """Обработка изменений баланса отслеживаемых кошельков"""
        try:
            logger.info(f"Изменение аккаунта {wallet[:8]}...")
            # В production здесь парсинг preBalance/postBalance для определения сделки

        except Exception as e:
            logger.error(f"Ошибка copy-trade: {e}")

    async def _execute(self, signal: TradeSignal):
        """Исполнение торгового сигнала с сохранением в БД"""
        success = await self.executor.execute(signal)

        if success:
            position = self.state.get_position(signal.token_address)

            if signal.action == "BUY" and position:
                await self.db.save_position(position)
                logger.info(f"Сохранена новая позиция для {signal.token_address[:8]}")
            elif signal.action == "SELL":
                pos = self.state.get_position(signal.token_address)
                if pos and pos.status == PositionStatus.CLOSED:
                    await self.db.update_position_exit(pos)
                    logger.info(f"Обновлена закрытая позиция для {signal.token_address[:8]}")

    async def _monitor_prices(self):
        """Фоновая задача: мониторинг цен для TP/SL раз в минуту"""
        while self.running:
            try:
                for token_addr, position in list(self.state.get_open_positions().items()):
                    current_price = await self.executor.get_token_price(token_addr)

                    if current_price > 0:
                        self.state.update_position_price(token_addr, current_price)

                        signal = self.strategy.check_exit_conditions(position, current_price)
                        if signal:
                            logger.info(f"Сигнал на выход: {token_addr[:8]}... ({signal.reason})")
                            await self._execute(signal)

                await asyncio.sleep(60)  # Проверка раз в минуту

            except Exception as e:
                logger.error(f"Ошибка мониторинга цен: {e}")
                await asyncio.sleep(60)

    async def run(self):
        """Запуск основного цикла бота"""
        self.running = True

        # Запускаем слушатель WebSocket и мониторинг цен параллельно
        listener_task = asyncio.create_task(self.listener.listen())
        monitor_task = asyncio.create_task(self._monitor_prices())

        # Обработка сигналов остановки (Ctrl+C)
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        logger.info("Бот запущен. Нажмите Ctrl+C для остановки.")

        try:
            await asyncio.gather(listener_task, monitor_task)
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        """Корректное завершение работы"""
        logger.info("Завершение работы...")
        self.running = False

        if self.listener:
            await self.listener.stop()

        await self.client.close()
        await self.db.close()
        logger.info("Бот остановлен")


async def main():
    try:
        config = load_config("config/settings.yaml")
        bot = GMGNBot(config)
        await bot.initialize()
        await bot.run()
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())