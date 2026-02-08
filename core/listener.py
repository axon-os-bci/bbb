"""
WebSocket Listener для подписки на события Solana в реальном времени.
Обрабатывает логи программы Raydium и изменения балансов кошельков (copy-trading).
"""

import asyncio
import json
import logging
from typing import Callable, List, Optional
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from core.config import BotConfig

logger = logging.getLogger(__name__)


class SolanaListener:
    """
    Слушатель WebSocket соединения с Solana RPC.
    Поддерживает автоматическое переподключение с экспоненциальной задержкой.
    """

    # Program ID Raydium AMM v4 (константа сети Solana)
    RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

    def __init__(self, config: BotConfig):
        self.config = config
        self.ws_url = config.solana.rpc.ws
        self.fallback_ws = config.solana.rpc.fallback_ws

        # Списки callback-функций для обработки событий
        self.raydium_callbacks: List[Callable] = []
        self.copy_trade_callbacks: List[Callable] = []

        # WebSocket соединение и подписки
        self.websocket = None
        self.subscriptions: dict = {}
        self.running = False

        # Счетчик переподключений для расчета задержки
        self.reconnect_count = 0

    def add_raydium_callback(self, callback: Callable):
        """
        Добавление обработчика событий Raydium (новые пулы, свопы).

        Args:
            callback: async функция, принимающая (logs: list, signature: str)
        """
        self.raydium_callbacks.append(callback)
        logger.info(f"Добавлен Raydium callback, всего: {len(self.raydium_callbacks)}")

    def add_copy_trade_callback(self, callback: Callable):
        """
        Добавление обработчика для copy-trading (изменения балансов).

        Args:
            callback: async функция, принимающая (account_data: dict, wallet: str)
        """
        self.copy_trade_callbacks.append(callback)
        logger.info(f"Добавлен CopyTrade callback, всего: {len(self.copy_trade_callbacks)}")

    async def connect(self):
        """
        Установка WebSocket соединения и подписка на логи.
        При неудаче переключается на fallback endpoint.
        """
        try:
            logger.info(f"Подключение к WebSocket: {self.ws_url}")
            self.websocket = await websockets.connect(
                self.ws_url,
                ping_interval=20,  # Пинг каждые 20 сек для поддержания соединения
                ping_timeout=10  # Таймаут ожидания понга
            )
            self.reconnect_count = 0

            # Подписка на логи Raydium (новые пулы, свопы)
            await self._subscribe_raydium()

            # Подписка на кошельки для copy-trading если включено
            if self.config.copy_trading.enabled:
                await self._subscribe_copy_trading_wallets()

        except (ConnectionRefusedError, InvalidStatusCode, OSError) as e:
            logger.error(f"Не удалось подключиться к основному RPC: {e}")
            if self.fallback_ws != self.ws_url:
                logger.info("Переключение на fallback WebSocket...")
                self.ws_url = self.fallback_ws
                await self.connect()

    async def _subscribe_raydium(self):
        """Подписка на логи программы Raydium AMM через logsSubscribe"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [self.RAYDIUM_AMM_PROGRAM]},
                {"commitment": "confirmed"}  # Ждем подтверждения блока
            ]
        }
        await self.websocket.send(json.dumps(payload))
        response = await self.websocket.recv()
        data = json.loads(response)

        if "result" in data:
            self.subscriptions['raydium'] = data["result"]
            logger.info(f"Подписка на Raydium активна, id: {data['result']}")
        else:
            logger.error(f"Ошибка подписки на Raydium: {data}")

    async def _subscribe_copy_trading_wallets(self):
        """Подписка на изменения аккаунтов отслеживаемых кошельков (китов)"""
        wallets = self.config.copy_trading.target_wallets

        for idx, wallet in enumerate(wallets):
            payload = {
                "jsonrpc": "2.0",
                "id": 100 + idx,
                "method": "accountSubscribe",
                "params": [
                    wallet,
                    {"commitment": "confirmed", "encoding": "jsonParsed"}
                ]
            }
            try:
                await self.websocket.send(json.dumps(payload))
                response = await self.websocket.recv()
                data = json.loads(response)

                if "result" in data:
                    self.subscriptions[f'wallet_{wallet}'] = data["result"]
                    logger.debug(f"Подписка на кошелек {wallet[:8]}... активна")

            except Exception as e:
                logger.error(f"Ошибка подписки на кошелек {wallet}: {e}")

    async def listen(self):
        """
        Основной цикл прослушивания.
        Обрабатывает входящие сообщения и переподключается при разрыве.
        """
        self.running = True

        while self.running:
            try:
                if not self.websocket or self.websocket.closed:
                    await self._reconnect()

                # Получение сообщения с таймаутом 1 сек (для проверки флага running)
                message = await asyncio.wait_for(self.websocket.recv(), timeout=1.0)
                await self._process_message(message)

            except asyncio.TimeoutError:
                # Нормальное поведение - проверяем флаг running
                continue
            except ConnectionClosed:
                logger.warning("WebSocket соединение закрыто")
                await self._reconnect()
            except Exception as e:
                logger.error(f"Ошибка в цикле прослушивания: {e}")
                await asyncio.sleep(1)

    async def _process_message(self, message: str):
        """Обработка входящего JSON сообщения от RPC"""
        try:
            data = json.loads(message)

            if "method" not in data:
                return  # Игнорируем ответы на запросы (не уведомления)

            method = data["method"]
            params = data.get("params", {})
            result = params.get("result", {})

            if method == "logsNotification":
                # Логи транзакций (Raydium)
                value = result.get("value", {})
                logs = value.get("logs", [])
                signature = value.get("signature", "")

                if logs:
                    await self._notify_raydium_callbacks(logs, signature)

            elif method == "accountNotification":
                # Изменения аккаунтов (Copy-trading)
                value = result.get("value", {})
                sub_id = params.get("subscription")
                wallet = self._get_wallet_by_subscription(sub_id)

                if wallet:
                    await self._notify_copy_trade_callbacks(value, wallet)

        except json.JSONDecodeError:
            logger.warning(f"Невалидный JSON получен")
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def _notify_raydium_callbacks(self, logs: list, signature: str):
        """Вызов всех зарегистрированных обработчиков Raydium событий"""
        for callback in self.raydium_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(logs, signature)
                else:
                    callback(logs, signature)
            except Exception as e:
                logger.error(f"Ошибка в Raydium callback: {e}")

    async def _notify_copy_trade_callbacks(self, account_data: dict, wallet: str):
        """Вызов обработчиков copy-trading"""
        for callback in self.copy_trade_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(account_data, wallet)
                else:
                    callback(account_data, wallet)
            except Exception as e:
                logger.error(f"Ошибка в CopyTrade callback: {e}")

    def _get_wallet_by_subscription(self, sub_id: int) -> Optional[str]:
        """Поиск адреса кошелька по ID подписки (subscription id)"""
        for key, sid in self.subscriptions.items():
            if sid == sub_id and key.startswith('wallet_'):
                return key.replace('wallet_', '')
        return None

    async def _reconnect(self):
        """Переподключение с экспоненциальной задержкой (max 60 сек)"""
        self.reconnect_count += 1
        delay = min(2 ** self.reconnect_count, 60)  # 2, 4, 8, 16, 32, 60...

        logger.info(f"Переподключение через {delay} сек (попытка {self.reconnect_count})...")
        await asyncio.sleep(delay)
        await self.connect()

    async def stop(self):
        """Корректное закрытие WebSocket соединения"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            logger.info("WebSocket соединение закрыто")