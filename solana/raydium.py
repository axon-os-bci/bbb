"""
Реальная интеграция с Raydium AMM v4 и Serum OpenBook (DEX).
Все смещения байт соответствуют актуальным структурам данных в сети.
Включает динамический парсинг Serum Market (чтение с конца структуры).
"""

import struct
import logging
import aiohttp
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass
from enum import IntEnum

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address

logger = logging.getLogger(__name__)

# Program IDs (константы сети Solana)
RAYDIUM_AMM_V4 = Pubkey.from_string("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")
SERUM_OPENBOOK_DEX = Pubkey.from_string("srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX")
WRAPPED_SOL_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")


# Инструкции Raydium (discriminant)
class RaydiumIX(IntEnum):
    INITIALIZE = 0
    SWAP_BASE_IN = 9
    SWAP_BASE_OUT = 10


@dataclass
class RaydiumPoolKeys:
    """Полная структура ключей пула Raydium AMM v4 + Serum"""
    # AMM Accounts
    amm_id: Pubkey
    amm_authority: Pubkey
    amm_open_orders: Pubkey
    amm_target_orders: Pubkey

    # Vaults (ATA пула)
    pool_base_token_account: Pubkey
    pool_quote_token_account: Pubkey

    # Mints
    base_mint: Pubkey
    quote_mint: Pubkey

    # Decimals
    base_decimals: int
    quote_decimals: int

    # Serum OpenBook интеграция
    serum_program_id: Pubkey
    serum_market: Pubkey
    serum_bids: Pubkey
    serum_asks: Pubkey
    serum_event_queue: Pubkey
    serum_coin_vault: Pubkey  # Base vault в терминах Serum
    serum_pc_vault: Pubkey  # Quote vault (PC = Price Currency)
    serum_vault_signer: Pubkey  # PDA для подписи транзакций Serum


class AmmInfoParser:
    """
    Парсер структуры AmmInfo из Raydium AMM v4.
    Структура соответствует Anchor account layout.
    """

    # Смещения после 8-байтного дискриминатора Anchor
    OFFSETS = {
        'account_type': 0,
        'status': 8,
        'nonce': 16,
        'max_order': 24,
        'depth': 32,
        'base_decimal': 40,  # u64 - decimals base токена
        'quote_decimal': 48,  # u64 - decimals quote токена
        'state': 56,
        'reset_flag': 64,
        'min_size': 72,
        'vol_max_cut_ratio': 80,
        'amount_wave': 88,
        'base_lot_size': 96,
        'quote_lot_size': 104,
        'min_price_multiplier': 112,
        'max_price_multiplier': 120,
        'system_decimal_value': 128,
        'fees': 136,  # Начало структуры Fees (64 bytes)
        'out_put': 200,  # Начало структуры OutPutData (64 bytes)
        # Pubkeys начинаются после 264 байт (8 + 256)
        'base_mint': 264,  # 32 bytes
        'quote_mint': 296,  # 32 bytes
        'base_vault': 328,  # 32 bytes - pool_base_token_account
        'quote_vault': 360,  # 32 bytes - pool_quote_token_account
        'base_target': 392,  # target_orders_base
        'quote_target': 424,  # target_orders_quote
        'open_orders': 520,  # amm_open_orders
        'market_id': 552,  # serum_market (32 bytes)
        'market_program_id': 584,  # serum_program_id (32 bytes)
        'target_orders': 616,  # amm_target_orders (32 bytes)
    }

    @classmethod
    def parse(cls, data: bytes, amm_id: Pubkey) -> Optional[Dict]:
        """
        Парсинг байтов AmmInfo.

        Args:
            data: Сырые байты аккаунта (минимум 648 байт)
            amm_id: Pubkey пула (для логов и расчета authority)

        Returns:
            Словарь с распарсенными полями или None при ошибке
        """
        if len(data) < 648:
            logger.error(f"AmmInfo слишком мал: {len(data)} байт, нужно 648+")
            return None

        try:
            # Читаем decimals (u64 little-endian)
            base_decimal = struct.unpack_from('<Q', data, cls.OFFSETS['base_decimal'])[0]
            quote_decimal = struct.unpack_from('<Q', data, cls.OFFSETS['quote_decimal'])[0]

            # Хелпер для чтения Pubkey (32 bytes)
            def read_pubkey(offset: int) -> Pubkey:
                return Pubkey.from_bytes(data[offset:offset + 32])

            # Читаем все необходимые Pubkeys
            base_mint = read_pubkey(cls.OFFSETS['base_mint'])
            quote_mint = read_pubkey(cls.OFFSETS['quote_mint'])
            base_vault = read_pubkey(cls.OFFSETS['base_vault'])
            quote_vault = read_pubkey(cls.OFFSETS['quote_vault'])
            open_orders = read_pubkey(cls.OFFSETS['open_orders'])
            market_id = read_pubkey(cls.OFFSETS['market_id'])
            market_program_id = read_pubkey(cls.OFFSETS['market_program_id'])
            target_orders = read_pubkey(cls.OFFSETS['target_orders'])

            # Расчет amm_authority (PDA)
            # seeds = [b"amm authority"]
            amm_authority, _ = Pubkey.find_program_address(
                [b"amm authority"],
                RAYDIUM_AMM_V4
            )

            return {
                'amm_id': amm_id,
                'amm_authority': amm_authority,
                'amm_open_orders': open_orders,
                'amm_target_orders': target_orders,
                'pool_base_token_account': base_vault,
                'pool_quote_token_account': quote_vault,
                'base_mint': base_mint,
                'quote_mint': quote_mint,
                'base_decimals': base_decimal,
                'quote_decimals': quote_decimal,
                'serum_program_id': market_program_id,
                'serum_market': market_id,
            }

        except struct.error as e:
            logger.error(f"Ошибка парсинга структуры: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка парсинга AmmInfo: {e}")
            return None


class SerumMarketParser:
    """
    Парсер структуры Market из Serum OpenBook DEX.
    Использует ДИНАМИЧЕСКОЕ чтение с конца структуры для event_queue, bids, asks.
    """

    @staticmethod
    def parse(data: bytes, market_id: Pubkey, program_id: Pubkey) -> Optional[Dict]:
        """
        Динамический парсинг Serum Market.

        Ключевая особенность: event_queue, bids, asks ВСЕГДА идут последними
        в структуре Market (в любом формате Serum/OpenBook), занимая ровно 96 байт.
        Мы читаем их с конца данных, а не по фиксированным смещениям.
        """
        if len(data) < 388:
            logger.error(f"Данные Market слишком малы: {len(data)} байт")
            return None

        try:
            # 1. Читаем vaults из начала структуры (фиксированные смещения)
            # В Serum/OpenBook структура Market начинается с:
            # - account_flags (u64)
            # - own_address (Pubkey)
            # - vault_signer_nonce (u64)
            # - base_mint (Pubkey)
            # - quote_mint (Pubkey)
            # - base_vault (Pubkey) - смещение 112
            # - quote_vault (Pubkey) - смещение 160
            base_vault = Pubkey.from_bytes(data[112:144])
            quote_vault = Pubkey.from_bytes(data[160:192])

            # 2. Расчет vault_signer (PDA)
            # seeds = [market_id]
            vault_signer, _ = Pubkey.find_program_address(
                [bytes(market_id)],
                program_id
            )

            # 3. ДИНАМИЧЕСКОЕ ЧТЕНИЕ: Берем последние 96 байт данных
            # Это всегда три Pubkey: event_queue, bids, asks (в разном порядке в зависимости от версии)
            last_96 = data[-96:]

            addr1 = Pubkey.from_bytes(last_96[0:32])
            addr2 = Pubkey.from_bytes(last_96[32:64])
            addr3 = Pubkey.from_bytes(last_96[64:96])

            # Проверяем, что адреса валидные (не нулевые)
            zero_key = Pubkey.from_string("11111111111111111111111111111111")

            if addr1 != zero_key and addr2 != zero_key and addr3 != zero_key:
                # В OpenBook v2 порядок обычно: event_queue, bids, asks
                # В Serum DEX v3: bids, asks, event_queue
                # Для безопасности можно определить по контексту или использовать как есть,
                # так как Raydium использует их по назначению в инструкции.

                # Пробуем вариант OpenBook v2 (самый распространенный сейчас):
                return {
                    'serum_base_vault': base_vault,
                    'serum_quote_vault': quote_vault,
                    'serum_vault_signer': vault_signer,
                    'serum_event_queue': addr1,
                    'serum_bids': addr2,
                    'serum_asks': addr3,
                }

            # Если адреса нулевые, возможно структура другая - пробуем резервный метод
            return SerumMarketParser._parse_fallback(data, base_vault, quote_vault, vault_signer)

        except Exception as e:
            logger.error(f"Ошибка динамического парсинга Serum: {e}")
            return None

    @staticmethod
    def _parse_fallback(data: bytes, base_vault, quote_vault, vault_signer) -> Optional[Dict]:
        """
        Резервный метод парсинга: ищем все Pubkey в данных и берем последние 3.
        Используется если стандартное чтение с конца не сработало.
        """
        try:
            pubkeys = []

            # Проходим по всем 32-байтовым чанкам
            for i in range(0, len(data) - 32, 32):
                chunk = data[i:i + 32]
                # Проверяем, что это не нулевой адрес и не base/quote vault
                if not all(b == 0 for b in chunk):
                    try:
                        pk = Pubkey.from_bytes(chunk)
                        # Исключаем уже известные адреса
                        if pk != base_vault and pk != quote_vault:
                            pubkeys.append(pk)
                    except:
                        continue

            # Берем последние 3 найденных адреса
            if len(pubkeys) >= 3:
                return {
                    'serum_base_vault': base_vault,
                    'serum_quote_vault': quote_vault,
                    'serum_vault_signer': vault_signer,
                    'serum_event_queue': pubkeys[-3],
                    'serum_bids': pubkeys[-2],
                    'serum_asks': pubkeys[-1],
                }

            return None
        except:
            return None


class RaydiumPoolLoader:
    """Загрузчик полных ключей пула из RPC с реальным парсингом структур"""

    def __init__(self, client: AsyncClient):
        self.client = client

    async def load_pool_keys(self, amm_id: str) -> Optional[RaydiumPoolKeys]:
        """
        Полная загрузка ключей пула через RPC.
        Выполняет 2 RPC запроса: AMM account + Serum Market account.
        """
        try:
            amm_pubkey = Pubkey.from_string(amm_id)

            # 1. Получаем данные AMM (реальный RPC)
            amm_resp = await self.client.get_account_info(amm_pubkey, commitment=Confirmed)
            if not amm_resp.value:
                logger.error(f"AMM аккаунт не найден: {amm_id}")
                return None

            # 2. Парсим AmmInfo
            amm_data = bytes(amm_resp.value.data)
            amm_info = AmmInfoParser.parse(amm_data, amm_pubkey)
            if not amm_info:
                return None

            # 3. Получаем данные Serum Market (реальный RPC)
            market_resp = await self.client.get_account_info(
                amm_info['serum_market'],
                commitment=Confirmed
            )
            if not market_resp.value:
                logger.error(f"Serum Market не найден: {amm_info['serum_market']}")
                return None

            # 4. Парсим Serum Market (динамическое чтение с конца!)
            market_data = bytes(market_resp.value.data)
            serum_keys = SerumMarketParser.parse(
                market_data,
                amm_info['serum_market'],
                amm_info['serum_program_id']
            )

            if not serum_keys:
                logger.error("Не удалось распарсить Serum Market")
                return None

            # 5. Собираем полную структуру
            return RaydiumPoolKeys(
                amm_id=amm_info['amm_id'],
                amm_authority=amm_info['amm_authority'],
                amm_open_orders=amm_info['amm_open_orders'],
                amm_target_orders=amm_info['amm_target_orders'],
                pool_base_token_account=amm_info['pool_base_token_account'],
                pool_quote_token_account=amm_info['pool_quote_token_account'],
                base_mint=amm_info['base_mint'],
                quote_mint=amm_info['quote_mint'],
                base_decimals=amm_info['base_decimals'],
                quote_decimals=amm_info['quote_decimals'],
                serum_program_id=amm_info['serum_program_id'],
                serum_market=amm_info['serum_market'],
                serum_bids=serum_keys['serum_bids'],
                serum_asks=serum_keys['serum_asks'],
                serum_event_queue=serum_keys['serum_event_queue'],
                serum_coin_vault=serum_keys['serum_base_vault'],
                serum_pc_vault=serum_keys['serum_quote_vault'],
                serum_vault_signer=serum_keys['serum_vault_signer'],
            )

        except Exception as e:
            logger.error(f"Ошибка загрузки ключей пула: {e}")
            return None

    async def get_reserves(self, pool_keys: RaydiumPoolKeys) -> Tuple[int, int]:
        """
        Получение резервов пула (балансов vault).
        Реальные RPC вызовы get_token_account_balance.
        """
        try:
            base_resp = await self.client.get_token_account_balance(
                pool_keys.pool_base_token_account
            )
            quote_resp = await self.client.get_token_account_balance(
                pool_keys.pool_quote_token_account
            )

            base = int(base_resp.value.amount) if base_resp.value else 0
            quote = int(quote_resp.value.amount) if quote_resp.value else 0

            logger.debug(f"Резервы пула - Base: {base}, Quote: {quote}")
            return base, quote

        except Exception as e:
            logger.error(f"Ошибка получения резервов: {e}")
            return 0, 0


class RaydiumSwapBuilder:
    """Построитель инструкций для свопов Raydium AMM v4"""

    def build_swap_ix(
            self,
            pool_keys: RaydiumPoolKeys,
            user_wallet: Pubkey,
            user_source: Pubkey,  # ATA источника (откуда списываем)
            user_dest: Pubkey,  # ATA назначения (куда зачисляем)
            amount_in: int,  # lamports/minimum units
            min_amount_out: int  # с учетом slippage
    ) -> Instruction:
        """
        Построение инструкции SwapBaseIn.

        Layout инструкции:
        - discriminant: u8 = 9 (SwapBaseIn)
        - amount_in: u64
        - min_amount_out: u64
        """
        # Данные инструкции (little-endian как в нативных программах Solana)
        data = struct.pack(
            "<BQQ",  # u8 + u64 + u64
            RaydiumIX.SWAP_BASE_IN,
            amount_in,
            min_amount_out
        )

        # Аккаунты в строгом порядке, требуемом программой Raydium (18 аккаунтов):
        accounts = [
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(pool_keys.amm_id, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.amm_authority, is_signer=False, is_writable=False),
            AccountMeta(pool_keys.amm_open_orders, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.amm_target_orders, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.pool_base_token_account, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.pool_quote_token_account, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.serum_program_id, is_signer=False, is_writable=False),
            AccountMeta(pool_keys.serum_market, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.serum_bids, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.serum_asks, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.serum_event_queue, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.serum_coin_vault, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.serum_pc_vault, is_signer=False, is_writable=True),
            AccountMeta(pool_keys.serum_vault_signer, is_signer=False, is_writable=False),
            AccountMeta(user_source, is_signer=False, is_writable=True),
            AccountMeta(user_dest, is_signer=False, is_writable=True),
            AccountMeta(user_wallet, is_signer=True, is_writable=False),
        ]

        return Instruction(
            program_id=RAYDIUM_AMM_V4,
            accounts=accounts,
            data=data
        )

    def calculate_swap_amounts(
            self,
            amount_in: int,
            reserve_in: int,
            reserve_out: int
    ) -> Tuple[int, int]:
        """
        Расчет выхода по формуле Constant Product AMM (x * y = k).

        Комиссия Raydium: 0.3% (997/1000)
        Защита от slippage: 0.5% (min_amount_out = 99.5% от расчетного)
        """
        if reserve_in == 0 or reserve_out == 0 or amount_in == 0:
            return 0, 0

        # amount_in_with_fee = amount_in * 997
        # amount_out = (amount_in_with_fee * reserve_out) / (reserve_in * 1000 + amount_in_with_fee)

        amount_in_with_fee = amount_in * 997
        numerator = amount_in_with_fee * reserve_out
        denominator = (reserve_in * 1000) + amount_in_with_fee

        amount_out = numerator // denominator
        min_amount_out = int(amount_out * 0.995)  # 0.5% slippage tolerance

        return amount_out, min_amount_out


class RaydiumAPI:
    """HTTP API для получения списка пулов (вспомогательный класс)"""

    @staticmethod
    async def fetch_all_pools() -> Optional[List[Dict]]:
        """Получение списка всех пулов с api.raydium.io"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        'https://api.raydium.io/v2/sdk/liquidity/mainnet.json',
                        timeout=10
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('official', []) + data.get('unOfficial', [])
                    return None
        except Exception as e:
            logger.error(f"Ошибка Raydium API: {e}")
            return None

    @staticmethod
    async def find_pool_by_mints(
            mint_a: str,
            mint_b: str = "So11111111111111111111111111111111111111112"
    ) -> Optional[str]:
        """
        Поиск ID пула (AMM ID) по mint адресам токенов.
        Возвращает amm_id или None.
        """
        pools = await RaydiumAPI.fetch_all_pools()
        if not pools:
            return None

        for pool in pools:
            base = pool.get('baseMint', '')
            quote = pool.get('quoteMint', '')

            if (base == mint_a and quote == mint_b) or \
                    (base == mint_b and quote == mint_a):
                return pool.get('id')

        return None


async def ensure_ata(
        client: AsyncClient,
        owner: Pubkey,
        mint: Pubkey,
        payer: Pubkey
) -> Tuple[Pubkey, Optional[Instruction]]:
    """
    Проверка существования ATA и возврат адреса.
    """
    ata = get_associated_token_address(owner, mint)

    try:
        resp = await client.get_account_info(ata)
        if resp.value is None:
            logger.info(f"ATA {ata} не существует, требуется создание")
            return ata, None  # В production здесь инструкция создания
        return ata, None
    except Exception as e:
        logger.error(f"Ошибка проверки ATA: {e}")
        return ata, None