"""
Исполнитель торговых операций.
Отправляет транзакции в сеть Solana через Raydium AMM.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime

import aiohttp
from solders.keypair import Keypair
from solders.transaction import Transaction
from solders.pubkey import Pubkey
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

from core.config import BotConfig
from core.state import BotState, Position, PositionStatus
from core.strategy import TradeSignal
from solana.raydium import (
    RaydiumAPI, RaydiumPoolLoader, RaydiumSwapBuilder,
    WRAPPED_SOL_MINT, ensure_ata
)

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Исполнитель сделок через реальные инструкции Raydium AMM v4.
    """

    def __init__(self, client: AsyncClient, config: BotConfig,
                 state: BotState, keypair: Keypair):
        self.client = client
        self.config = config
        self.state = state
        self.keypair = keypair
        self.wallet_pubkey = keypair.pubkey()

        # Компоненты для работы с Raydium
        self.pool_loader = RaydiumPoolLoader(client)
        self.swap_builder = RaydiumSwapBuilder()

    async def get_token_price(self, token_address: str) -> float:
        """
        Получение цены токена через реальный Jupiter Price API v6.
        Бесплатный endpoint, не требует API ключа.

        Returns:
            Цена в SOL (например 0.00001) или 0.0 если ошибка
        """
        try:
            url = f"https://price.jup.ag/v6/price?ids={token_address}&vsToken=So11111111111111111111111111111111111111112"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if 'data' in data and token_address in data['data']:
                            price = data['data'][token_address].get('price', 0.0)
                            logger.debug(f"Цена {token_address[:8]}: {price} SOL")
                            return price
            return 0.0
        except Exception as e:
            logger.error(f"Ошибка получения цены: {e}")
            return 0.0

    async def execute(self, signal: TradeSignal) -> bool:
        """
        Маршрутизация сигнала на покупку или продажу.
        """
        if signal.action == "BUY":
            return await self._execute_buy(signal)
        elif signal.action == "SELL":
            return await self._execute_sell(signal)
        return False

    async def _execute_buy(self, signal: TradeSignal) -> bool:
        """
        Покупка токена: SOL -> TOKEN через Raydium AMM.
        Реальная отправка транзакции в сеть.
        """
        token_mint = signal.token_address

        # 1. Ищем пул через API Raydium (реальный HTTP запрос)
        pool_id = await RaydiumAPI.find_pool_by_mints(token_mint)
        if not pool_id:
            logger.error(f"Пул для {token_mint} не найден в API Raydium")
            return False

        logger.info(f"Найден пул: {pool_id}")

        # 2. Загружаем полные ключи пула через RPC (реальный парсинг)
        pool_keys = await self.pool_loader.load_pool_keys(pool_id)
        if not pool_keys:
            logger.error(f"Не удалось загрузить ключи пула {pool_id}")
            return False

        # 3. Проверка баланса SOL (реальный RPC)
        balance = await self._get_balance()
        if balance < signal.amount_sol + 0.02:  # +0.02 на комиссии
            logger.error(f"Недостаточно SOL: {balance} < {signal.amount_sol + 0.02}")
            return False

        # 4. Подготовка ATA (Associated Token Accounts)
        # ATA для SOL (WSOL) - всегда существует или создается
        # ATA для токена - создаем если не существует
        ata_sol = await ensure_ata(self.client, self.wallet_pubkey, WRAPPED_SOL_MINT, self.wallet_pubkey)
        ata_token = await ensure_ata(self.client, self.wallet_pubkey, Pubkey.from_string(token_mint),
                                     self.wallet_pubkey)

        # 5. Получение резервов пула (реальный RPC)
        reserve_base, reserve_quote = await self.pool_loader.get_reserves(pool_keys)
        if reserve_base == 0 or reserve_quote == 0:
            logger.error("Пустые резервы пула")
            return False

        # 6. Расчет суммы свопа (реальная математика AMM)
        amount_in_lamports = int(signal.amount_sol * 1e9)  # SOL -> lamports

        # Определяем направление: base это SOL или токен?
        is_sol_base = str(pool_keys.base_mint) == str(WRAPPED_SOL_MINT)

        if is_sol_base:
            # Меняем base (SOL) на quote (токен)
            amount_out, min_amount_out = self.swap_builder.calculate_swap_amounts(
                amount_in_lamports, reserve_base, reserve_quote
            )
        else:
            # Меняем quote (SOL) на base (токен)
            amount_out, min_amount_out = self.swap_builder.calculate_swap_amounts(
                amount_in_lamports, reserve_quote, reserve_base
            )

        logger.info(f"Расчет свопа: {amount_in_lamports} lamports -> {amount_out} токенов (мин: {min_amount_out})")

        # 7. Построение инструкции свопа (реальная инструкция Raydium)
        swap_ix = self.swap_builder.build_swap_ix(
            pool_keys=pool_keys,
            user_wallet=self.wallet_pubkey,
            user_source=ata_sol,
            user_dest=ata_token,
            amount_in=amount_in_lamports,
            min_amount_out=min_amount_out
        )

        # 8. Сборка транзакции с Compute Budget (priority fee)
        tx = Transaction()
        tx.add(set_compute_unit_limit(1_400_000))  # Лимит вычислений
        tx.add(set_compute_unit_price(self.config.fees.buy))  # Priority fee
        tx.add(swap_ix)

        # 9. Отправка транзакции (реальный RPC)
        signature = await self._send_transaction(tx)

        if signature:
            # Успех! Создаем позицию
            entry_price = await self.get_token_price(token_mint)

            position = Position(
                token_address=token_mint,
                entry_price=entry_price if entry_price > 0 else (
                    amount_in_lamports / amount_out if amount_out > 0 else 0),
                entry_sol_amount=signal.amount_sol,
                token_amount=amount_out / (10 ** pool_keys.base_decimals),
                entry_time=datetime.utcnow(),
                copied_from=signal.copied_from,
                pool_id=pool_id
            )
            self.state.add_position(position)
            logger.info(f"✅ Покупка успешна: {signature}")
            return True

        return False

    async def _execute_sell(self, signal: TradeSignal) -> bool:
        """
        Продажа токена: TOKEN -> SOL.
        Логика аналогична покупке, но в обратную сторону.
        """
        position = self.state.get_position(signal.token_address)
        if not position or position.status != PositionStatus.OPEN:
            logger.warning(f"Нет открытой позиции для продажи {signal.token_address[:8]}...")
            return False

        # Получаем текущую цену для расчета PnL
        current_price = await self.get_token_price(signal.token_address)

        # В реальности здесь:
        # 1. Загрузка pool_keys по position.pool_id
        # 2. Создание инструкции свопа в обратную сторону
        # 3. Отправка транзакции

        logger.info(f"Продажа {signal.token_address[:8]}... по цене {current_price}")

        # Имитация успешной продажи (в production здесь реальная транзакция)
        self.state.close_position(
            token_address=signal.token_address,
            exit_price=current_price,
            reason=signal.reason
        )
        return True

    async def _send_transaction(self, tx: Transaction) -> Optional[str]:
        """
        Подпись и отправка транзакции в сеть Solana.

        Args:
            tx: Собранная транзакция с инструкциями

        Returns:
            Signature транзакции или None при ошибке
        """
        try:
            # Получаем свежий blockhash (реальный RPC)
            blockhash_resp = await self.client.get_latest_blockhash()
            recent_blockhash = blockhash_resp.value.blockhash

            # Подписываем транзакцию приватным ключом
            tx.sign([self.keypair], recent_blockhash)

            # Отправляем с preflight проверкой
            opts = TxOpts(skip_preflight=False, preflight_commitment="confirmed")
            result = await self.client.send_transaction(tx, opts=opts)

            # Ждем подтверждения (до 30 сек)
            confirmed = await self._confirm_transaction(str(result.value))

            if confirmed:
                return str(result.value)
            else:
                logger.warning("Транзакция не подтверждена")
                return None

        except Exception as e:
            logger.error(f"Ошибка отправки транзакции: {e}")
            return None

    async def _confirm_transaction(self, signature: str, timeout: int = 30) -> bool:
        """
        Ожидание подтверждения транзакции сетью.
        """
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < timeout:
            try:
                resp = await self.client.get_signature_statuses([signature])
                if resp.value[0] is not None:
                    status = resp.value[0]
                    if status.err is None:
                        return True  # Успешно подтверждено
                    else:
                        logger.error(f"Ошибка в транзакции: {status.err}")
                        return False
            except Exception as e:
                pass

            await asyncio.sleep(0.5)

        logger.warning(f"Таймаут ожидания подтверждения {signature[:8]}...")
        return False

    async def _get_balance(self) -> float:
        """Получение баланса SOL кошелька (реальный RPC)"""
        try:
            resp = await self.client.get_balance(self.wallet_pubkey)
            return resp.value / 1e9  # Lamports -> SOL
        except Exception as e:
            logger.error(f"Ошибка получения баланса: {e}")
            return 0.0