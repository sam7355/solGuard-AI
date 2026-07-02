SolGuard AI — Risk Scoring Engine

Takes raw on-chain data (from scanner.py) and turns it into a structured,
weighted risk score for a token mint or a wallet.

This is the "basic-risk-engine" referenced by AI_MODEL in config — a
transparent, rule-based scorer. Swap in an ML/LLM-based model later by
implementing the same interface (analyze_token / analyze_wallet).
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from config import settings
from scanner import SolanaScanner, InvalidAddressError

logger = logging.getLogger("solguard.risk_engine")

# Well-known "burn" / null addresses — holdings here don't count as real concentration
BURN_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
}


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class RiskFlag:
    code: str
    description: str
    weight: int  # points added to the risk score (0-100 scale)


@dataclass
class RiskReport:
    target: str
    target_type: str  # "token" | "wallet"
    score: int
    level: RiskLevel
    flags: list[RiskFlag] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "target_type": self.target_type,
            "score": self.score,
            "level": self.level.value,
            "is_risky": self.score >= settings.RISK_SCORE_THRESHOLD,
            "flags": [
                {"code": f.code, "description": f.description, "weight": f.weight}
                for f in self.flags
            ],
        }


def _score_to_level(score: int) -> RiskLevel:
    if score >= 75:
        return RiskLevel.CRITICAL
    if score >= 50:
        return RiskLevel.HIGH
    if score >= 25:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


class RiskEngine:
    """Rule-based risk scorer for Solana tokens and wallets."""

    def _init_(self, scanner: Optional[SolanaScanner] = None):
        self.scanner = scanner or SolanaScanner()
        self.model_name = settings.AI_MODEL

    # ------------------------------------------------------------
    # Token analysis
    # ------------------------------------------------------------
    async def analyze_token(self, mint_address: str) -> RiskReport:
        flags: list[RiskFlag] = []
        raw: dict = {}

        try:
            supply_info = await self.scanner.get_token_supply(mint_address)
            account_info = await self.scanner.get_account_info(mint_address)
        except InvalidAddressError as exc:
            return RiskReport(
                target=mint_address,
                target_type="token",
                score=100,
                level=RiskLevel.CRITICAL,
                flags=[RiskFlag("INVALID_ADDRESS", str(exc), 100)],
            )

        if not supply_info or not account_info:
            flags.append(
                RiskFlag(
                    "MINT_NOT_FOUND",
                    "Mint account could not be found on-chain.",
                    40,
                )
            )
            score = min(sum(f.weight for f in flags), 100)
            return RiskReport(mint_address, "token", score, _score_to_level(score), flags, raw)

        raw["supply"] = supply_info

        parsed = (account_info.get("data") or {}).get("parsed", {})
        info = parsed.get("info", {})
        raw["mint_info"] = info

        mint_authority = info.get("mintAuthority")
        freeze_authority = info.get("freezeAuthority")

        if mint_authority:
            flags.append(
                RiskFlag(
                    "MINT_AUTHORITY_ACTIVE",
                    "Mint authority is not renounced — supply can be inflated at will.",
                    30,
                )
            )

        if freeze_authority:
            flags.append(
                RiskFlag(
                    "FREEZE_AUTHORITY_ACTIVE",
                    "Freeze authority is active — holder accounts can be frozen.",
                    25,
                )
            )

        # Holder concentration check via getTokenLargestAccounts
        try:
            largest = await self.scanner.rpc_call(
                "getTokenLargestAccounts", [mint_address]
            )
            holders = (largest or {}).get("value", [])
            raw["largest_accounts"] = holders
            total_supply = float(supply_info.get("amount", 0) or 0)

            if total_supply > 0 and holders:
                top_holder = holders[0]
                top_amount = float(top_holder.get("amount", 0) or 0)
                top_share = top_amount / total_supply

                if top_share >= 0.5:
                    flags.append(
                        RiskFlag(
                            "SUPPLY_CONCENTRATION_HIGH",
                            f"Top holder controls {top_share:.0%} of total supply.",
                            25,
                        )
                    )
                elif top_share >= 0.25:
                    flags.append(
                        RiskFlag(
                            "SUPPLY_CONCENTRATION_MODERATE",
                            f"Top holder controls {top_share:.0%} of total supply.",
                            10,
                        )
                    )
        except Exception as exc:
            logger.warning(f"Could not fetch largest accounts for {mint_address}: {exc}")

        decimals = info.get("decimals")
        if decimals is not None and decimals == 0:
            flags.append(
                RiskFlag(
                    "ZERO_DECIMALS",
                    "Token has 0 decimals — unusual for a fungible token, verify intent.",
                    5,
                )
            )

        score = min(sum(f.weight for f in flags), 100)
        return RiskReport(mint_address, "token", score, _score_to_level(score), flags, raw)

    # ------------------------------------------------------------
    # Wallet analysis
    # ------------------------------------------------------------
    async def analyze_wallet(self, wallet_address: str) -> RiskReport:
        flags: list[RiskFlag] = []
        raw: dict = {}

        try:
            balance = await self.scanner.get_balance(wallet_address)
            account_info = await self.scanner.get_account_info(wallet_address)
            token_accounts = await self.scanner.get_token_accounts_by_owner(wallet_address)
        except InvalidAddressError as exc:
            return RiskReport(
                target=wallet_address,
                target_type="wallet",
                score=100,
                level=RiskLevel.CRITICAL,
                flags=[RiskFlag("INVALID_ADDRESS", str(exc), 100)],
            )

        raw["balance"] = balance
        raw["token_account_count"] = len(token_accounts)

        if account_info is None:
            flags.append(
                RiskFlag(
                    "ACCOUNT_NOT_FOUND",
                    "Wallet has never been used on-chain (no account data).",
                    20,
                )
            )

        if balance["sol"] == 0 and not token_accounts:
            flags.append(
                RiskFlag(
                    "EMPTY_WALLET",
                    "Wallet holds no SOL and no SPL tokens — likely inactive or a burner.",
                    15,
                )
            )

        if len(token_accounts) > 200:
            flags.append(
                RiskFlag(
                    "TOKEN_HOARDER",
                    "Unusually high number of token accounts — possible bot/sybil wallet.",
                    15,
                )
            )

        score = min(sum(f.weight for f in flags), 100)
        return RiskReport(wallet_address, "wallet", score, _score_to_level(score), flags, raw)

    async def close(self):
        await self.scanner.close()


risk_engine = RiskEngine()
