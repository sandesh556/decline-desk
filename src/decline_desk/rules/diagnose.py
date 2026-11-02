"""Deterministic decline diagnosis.

Decline codes are structured data, so they are explained by code, not by an LLM. Each
code is checked against the card, its controls and the company wallet. The LLM later
handles only the fuzzy parts: which transaction the person means, and wording the reply.
"""

from dataclasses import dataclass, field
from enum import StrEnum

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
    CARD_FROZEN_FRAUD = "card_frozen_fraud"
    SUSPECTED_FRAUD = "suspected_fraud"
    CARD_FROZEN_BY_USER = "card_frozen_by_user"
    CARD_EXPIRED = "card_expired"
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
    return Diagnosis(auth.id, approved=False, findings=(_primary(auth, ctx),))


def _primary(auth: Authorization, ctx: Context) -> Finding:
    card, controls, code = ctx.card, ctx.controls, auth.response_code
    card_ref = f"card ending {card.last4}"

    match code:
        case ResponseCode.RESTRICTED_CARD:
            allowed = ", ".join(sorted(controls.allowed_countries))
            return Finding(
                Cause.COUNTRY_RESTRICTED,
                f"{card_ref} is allowed in {allowed}; the purchase was in {auth.country}",
            )
        case ResponseCode.NOT_PERMITTED:
            return Finding(Cause.MCC_BLOCKED, f"{card_ref} blocks merchant category {auth.mcc}")
        case ResponseCode.LIMIT_EXCEEDED:
            return Finding(
                Cause.LIMIT_PER_TXN,
                f"${auth.amount_usd} is above the ${controls.per_txn_limit_usd} "
                f"per-transaction limit on {card_ref}",
            )
        case ResponseCode.INSUFFICIENT_FUNDS:
            return Finding(
                Cause.COMPANY_FUNDS_LOW,
                f"company wallet holds ${ctx.company.wallet_balance_usd}; "
                f"the purchase needed ${auth.amount_usd}",
            )
        case ResponseCode.SUSPECTED_FRAUD:
            return Finding(Cause.SUSPECTED_FRAUD, f"the network flagged {card_ref} for review")
        case ResponseCode.CARD_BLOCKED if card.status is CardStatus.FROZEN_FRAUD:
            return Finding(Cause.CARD_FROZEN_FRAUD, f"{card_ref} is frozen for security review")
        case ResponseCode.CARD_BLOCKED:
            return Finding(Cause.CARD_FROZEN_BY_USER, f"{card_ref} was frozen by its holder")
        case ResponseCode.EXPIRED_CARD:
            return Finding(Cause.CARD_EXPIRED, f"{card_ref} expired on {card.expires}")
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
