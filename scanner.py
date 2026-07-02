SolGuard AI — Solana RPC Scanner

Handles low-level JSON-RPC communication with Solana, with:
- Connection pooling (single reused client)
- Automatic retry with exponential backoff
- Fallback RPC endpoint if the primary fails
- Proper JSON-RPC error handling
- Basic address validation
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

from config import settings

logger = logging.getLogger("solguard.scanner")

LAMPORTS_PER_SOL = 1_000_000_000


class SolanaRPCError(Exception):
    """Raised when the Solana RPC node returns a JSON-RPC error."""

    def _init_(self, code: int, message: str):
        self.code = code
        self.message = message
        super()._init_(f"Solana RPC error {code}: {message}")


class InvalidAddressError(ValueError):
    """Raised when a wallet/mint address fails basic format validation."""


def is_valid_address(address: str) -> bool:
    """Cheap sanity check: base58 alphabet, correct-ish length. Not a full checksum."""
    if not address or not (32 <= len(address) <= 44):
        return False
    base58_alphabet = set(
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    )
    return all(c in base58_alphabet for c in address)


class SolanaScanner:
    def _init_(self):
        self.rpc_url = settings.SOLANA_RPC_URL
        self.fallback_url = settings.SOLANA_RPC_FALLBACK_URL
        self.timeout = settings.SOLANA_TIMEOUT_SECONDS
        self.max_retries = settings.SOLANA_MAX_RETRIES
        self.commitment = settings.SOLANA_COMMITMENT
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------
    # Lifecycle — reuse one pooled client instead of one-per-call
    # ------------------------------------------------------------
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _aenter_(self):
        return self

    async def _aexit_(self, *exc_info):
        await self.close()

    # ------------------------------------------------------------
    # Core RPC call with retries + fallback endpoint
    # ------------------------------------------------------------
    async def rpc_call(self, method: str, params: list) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        urls = [self.rpc_url]
        if self.fallback_url and self.fallback_url != self.rpc_url:
            urls.append(self.fallback_url)

        last_error: Optional[Exception] = None

        for url in urls:
            for attempt in range(1, self.max_retries + 1):
                try:
                    client = await self._get_client()
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    data = response.json()

                    if "error" in data:
                        err = data["error"]
                        raise SolanaRPCError(
                            code=err.get("code", -1),
                            message=err.get("message", "Unknown RPC error"),
                        )

                    return data.get("result")

                except SolanaRPCError:
                    # RPC-level error (bad request/method) — retrying won't help
                    raise

                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_error = exc
                    wait = 2 ** (attempt - 1)  # 1s, 2s, 4s...
                    logger.warning(
                        f"[{method}] attempt {attempt}/{self.max_retries} on "
                        f"{url} failed ({exc}); retrying in {wait}s"
                    )
                    if attempt < self.max_retries:
                        await asyncio.sleep(wait)

                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    logger.warning(f"[{method}] HTTP {exc.response.status_code} from {url}")
                    break  # try next URL rather than retrying same bad endpoint

            logger.error(f"[{method}] exhausted retries on {url}, trying next endpoint if any")

        raise ConnectionError(
            f"All Solana RPC endpoints failed for method '{method}': {last_error}"
        )

    # ------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------
    async def get_balance(self, wallet_address: str) -> dict:
        if not is_valid_address(wallet_address):
            raise InvalidAddressError(f"Invalid wallet address: {wallet_address}")
        result = await self.rpc_call(
            "getBalance",
            [wallet_address, {"commitment": self.commitment}],
        )
        lamports = result.get("value", 0) if result else 0
        return {"lamports": lamports, "sol": lamports / LAMPORTS_PER_SOL}

    async def get_account_info(self, wallet_address: str) -> Optional[dict]:
        if not is_valid_address(wallet_address):
            raise InvalidAddressError(f"Invalid wallet address: {wallet_address}")
        result = await self.rpc_call(
            "getAccountInfo",
            [
                wallet_address,
                {"encoding": "jsonParsed", "commitment": self.commitment},
            ],
        )
        return result.get("value") if result else None

    async def get_token_supply(self, mint_address: str) -> Optional[dict]:
        if not is_valid_address(mint_address):
            raise InvalidAddressError(f"Invalid mint address: {mint_address}")
        result = await self.rpc_call(
            "getTokenSupply",
            [mint_address, {"commitment": self.commitment}],
        )
        return result.get("value") if result else None

    async def get_token_accounts_by_owner(self, wallet_address: str) -> list:
        if not is_valid_address(wallet_address):
            raise InvalidAddressError(f"Invalid wallet address: {wallet_address}")
        result = await self.rpc_call(
            "getTokenAccountsByOwner",
            [
                wallet_address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed", "commitment": self.commitment},
            ],
        )
        return result.get("value", []) if result else []

    async def get_multiple_accounts(self, addresses: list[str]) -> list:
        invalid = [a for a in addresses if not is_valid_address(a)]
        if invalid:
            raise InvalidAddressError(f"Invalid addresses: {invalid}")
        result = await self.rpc_call(
            "getMultipleAccounts",
            [addresses, {"encoding": "jsonParsed", "commitment": self.commitment}],
        )
        return result.get("value", []) if result else []


scanner = SolanaScanner()
