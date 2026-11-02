"""Deterministic decline diagnosis.

Decline codes are structured data, so they are explained by code, not by an LLM. Each
code is checked against the card, its controls and the company wallet. The LLM later
handles only the fuzzy parts: which transaction the person means, and wording the reply.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from decline_desk.domain.models import (
    Authorization,
    Card,
    CardControls,
    CardStatus,
    Company,
    PendingTopup,
    ResponseCode,
)


class Cause(StrEnum):
    """Declaration order is fix-first priority: card usability, then where it may be used,
    then how much, then funds, then retryable checkout errors."""

    CARD_FROZEN_FRAUD = "card_frozen_fraud"
    SUSPECTED_FRAUD = "suspected_fraud"
    CARD_EXPIRED = "card_expired"  # before user-frozen: unfreezing an expired card is pointless
    CARD_FROZEN_BY_USER = "card_frozen_by_user"
    COUNTRY_RESTRICTED = "country_restricted"
    MCC_BLOCKED = "mcc_blocked"
    LIMIT_PER_TXN = "limit_per_txn"
    LIMIT_DAILY = "limit_daily"
    COMPANY_FUNDS_LOW = "company_funds_low"
    FUNDS_PENDING_SETTLEMENT = "funds_pending_settlement"
    THREE_DS_FAILED = "three_ds_failed"
    INCORRECT_CVV = "incorrect_cvv"
    INCONSISTENT = "inconsistent"
    UNKNOWN = "unknown"


# A human (fraud team or ops) must look at these; the agent must not act on its own.
ESCALATE = {Cause.CARD_FROZEN_FRAUD, Cause.SUSPECTED_FRAUD, Cause.INCONSISTENT, Cause.UNKNOWN}


@dataclass(frozen=True)
class Context:
    card: Card
    controls: CardControls
    company: Company
    pending_topups: list[PendingTopup] = field(default_factory=list)
    recent_authorizations: list[Authorization] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    cause: Cause
    evidence: str


@dataclass(frozen=True)
class Diagnosis:
    authorization_id: str
    approved: bool
    findings: tuple[Finding, ...] = ()

    @property
    def causes(self) -> tuple[Cause, ...]:
        return tuple(f.cause for f in self.findings)

    @property
    def escalate(self) -> bool:
        return any(c in ESCALATE for c in self.causes)


def diagnose(auth: Authorization, ctx: Context) -> Diagnosis:
    if auth.card_id != ctx.card.id:
        raise ValueError(f"card {ctx.card.id} does not match authorization card {auth.card_id}")
    if not auth.is_declined:
        return Diagnosis(auth.id, approved=True)

    findings = [_primary(auth, ctx)]
    if findings[0].cause is not Cause.UNKNOWN:  # never pile guesses onto an unknown code
        findings += _control_violations(auth, ctx)

    unique: dict[Cause, Finding] = {}
    for f in findings:
        unique.setdefault(f.cause, f)
    order = list(Cause)
    ranked = sorted(unique.values(), key=lambda f: order.index(f.cause))
    return Diagnosis(auth.id, approved=False, findings=tuple(ranked))


def _card_ref(card: Card) -> str:
    return f"card ending {card.last4}"


def _expired(auth: Authorization, ctx: Context) -> bool:
    """A card is valid through its expiry date, in the company's local time."""
    local_day = auth.created_at.astimezone(ZoneInfo(ctx.company.timezone)).date()
    return local_day > ctx.card.expires


def _control_violations(auth: Authorization, ctx: Context) -> list[Finding]:
    """Every control the transaction breaks, whatever code the network sent."""
    card, controls, ref = ctx.card, ctx.controls, _card_ref(ctx.card)
    out: list[Finding] = []
    if _expired(auth, ctx):
        out.append(Finding(Cause.CARD_EXPIRED, f"{ref} expired on {card.expires}"))
    if card.status is CardStatus.FROZEN_FRAUD:
        out.append(Finding(Cause.CARD_FROZEN_FRAUD, f"{ref} is frozen for security review"))
    if card.status is CardStatus.FROZEN_BY_USER:
        out.append(Finding(Cause.CARD_FROZEN_BY_USER, f"{ref} was frozen by its holder"))
    if auth.country not in controls.allowed_countries:
        allowed = ", ".join(sorted(controls.allowed_countries))
        out.append(
            Finding(
                Cause.COUNTRY_RESTRICTED,
                f"{ref} is allowed in {allowed}; the purchase was in {auth.country}",
            )
        )
    if auth.mcc in controls.blocked_mccs:
        out.append(Finding(Cause.MCC_BLOCKED, f"{ref} blocks merchant category {auth.mcc}"))
    if auth.amount_usd > controls.per_txn_limit_usd:
        out.append(
            Finding(
                Cause.LIMIT_PER_TXN,
                f"${auth.amount_usd} is above the ${controls.per_txn_limit_usd} "
                f"per-transaction limit on {ref}",
            )
        )
    return out


def _primary(auth: Authorization, ctx: Context) -> Finding:
    card, controls, code = ctx.card, ctx.controls, auth.response_code
    card_ref = _card_ref(card)

    match code:
        case ResponseCode.RESTRICTED_CARD if auth.country in controls.allowed_countries:
            return _inconsistent(code, f"{auth.country} is an allowed country")
        case ResponseCode.RESTRICTED_CARD:
            allowed = ", ".join(sorted(controls.allowed_countries))
            return Finding(
                Cause.COUNTRY_RESTRICTED,
                f"{card_ref} is allowed in {allowed}; the purchase was in {auth.country}",
            )
        case ResponseCode.NOT_PERMITTED if auth.mcc not in controls.blocked_mccs:
            return _inconsistent(code, f"merchant category {auth.mcc} is not blocked")
        case ResponseCode.NOT_PERMITTED:
            return Finding(Cause.MCC_BLOCKED, f"{card_ref} blocks merchant category {auth.mcc}")
        case ResponseCode.LIMIT_EXCEEDED:
            return _limit(auth, ctx, card_ref)
        case ResponseCode.INSUFFICIENT_FUNDS:
            return _funds(auth, ctx)
        case ResponseCode.SUSPECTED_FRAUD:
            return Finding(Cause.SUSPECTED_FRAUD, f"the network flagged {card_ref} for review")
        case ResponseCode.CARD_BLOCKED if card.status is CardStatus.FROZEN_FRAUD:
            return Finding(Cause.CARD_FROZEN_FRAUD, f"{card_ref} is frozen for security review")
        case ResponseCode.CARD_BLOCKED if card.status is CardStatus.FROZEN_BY_USER:
            return Finding(Cause.CARD_FROZEN_BY_USER, f"{card_ref} was frozen by its holder")
        case ResponseCode.CARD_BLOCKED:
            return _inconsistent(code, f"{card_ref} is active")
        case ResponseCode.EXPIRED_CARD if _expired(auth, ctx):
            return Finding(Cause.CARD_EXPIRED, f"{card_ref} expired on {card.expires}")
        case ResponseCode.EXPIRED_CARD:
            return _inconsistent(code, f"{card_ref} is valid until {card.expires}")
        case ResponseCode.AUTH_REQUIRED:
            return Finding(
                Cause.THREE_DS_FAILED, f"3-D Secure verification was not completed on {card_ref}"
            )
        case ResponseCode.CVV_MISMATCH:
            return Finding(
                Cause.INCORRECT_CVV, f"the security code entered for {card_ref} was wrong"
            )
        case _:
            return Finding(Cause.UNKNOWN, f"unrecognised decline code on {card_ref}")


def _inconsistent(code: ResponseCode, why: str) -> Finding:
    return Finding(Cause.INCONSISTENT, f"code {code.value} does not match the data: {why}")


def _limit(auth: Authorization, ctx: Context, card_ref: str) -> Finding:
    amount, controls = auth.amount_usd, ctx.controls
    if amount > controls.per_txn_limit_usd:  # exactly at the limit is allowed
        return Finding(
            Cause.LIMIT_PER_TXN,
            f"${amount} is above the ${controls.per_txn_limit_usd} "
            f"per-transaction limit on {card_ref}",
        )
    day_total = _spent_same_local_day(auth, ctx) + amount
    if day_total > controls.daily_limit_usd:
        local_day = auth.created_at.astimezone(ZoneInfo(ctx.company.timezone)).date()
        return Finding(
            Cause.LIMIT_DAILY,
            f"${day_total} spent on {local_day} ({ctx.company.timezone}) would exceed the "
            f"${controls.daily_limit_usd} daily limit on {card_ref}",
        )
    return _inconsistent(
        auth.response_code,
        f"${amount} is within the per-transaction limit and ${day_total} within the daily limit",
    )


def _spent_same_local_day(auth: Authorization, ctx: Context) -> Decimal:
    """Approved spend earlier the same calendar day in the company's timezone."""
    tz = ZoneInfo(ctx.company.timezone)
    day = auth.created_at.astimezone(tz).date()
    return sum(
        (
            a.amount_usd
            for a in ctx.recent_authorizations
            if a.card_id == auth.card_id
            and not a.is_declined
            and a.created_at < auth.created_at
            and a.created_at.astimezone(tz).date() == day
        ),
        Decimal("0"),
    )


def _funds(auth: Authorization, ctx: Context) -> Finding:
    wallet, amount = ctx.company.wallet_balance_usd, auth.amount_usd
    if wallet >= amount:
        return _inconsistent(auth.response_code, f"wallet holds ${wallet}, enough for ${amount}")
    if ctx.pending_topups:
        first = min(ctx.pending_topups, key=lambda t: t.eta)
        return Finding(
            Cause.FUNDS_PENDING_SETTLEMENT,
            f"company wallet holds ${wallet}, below the ${amount} needed; a ${first.amount_usd} "
            f"{first.asset} top-up is settling, expected {first.eta:%Y-%m-%d %H:%M} UTC",
        )
    return Finding(
        Cause.COMPANY_FUNDS_LOW,
        f"company wallet holds ${wallet}; the purchase needed ${amount} and no top-up is pending",
    )
