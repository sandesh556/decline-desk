"""The action guard. LLM output reaches the system only through here.

1. parse_action: the proposal must be one of four closed action types (no extra fields).
2. check_action: the card must belong to the ticket's employee, the diagnosis must not
   be escalated, the action must fix a cause the rules found, and it must stay inside hard
   bounds (at most 2x the current limit, never above the company cap, 1-24 hours).

A proposal that passes still does nothing on its own. It only becomes a request an admin
can approve. Prompt injection is therefore beaten by structure, not by prompt wording.
"""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from decline_desk.domain.models import CardStatus, Frozen, Mcc, Text
from decline_desk.repo import NotFound, Repo
from decline_desk.rules.diagnose import Cause, Diagnosis

MAX_HOURS = 24
MAX_LIMIT_MULTIPLIER = Decimal("2")


class PolicyViolation(ValueError):
    """The proposed action is not allowed. The message is safe to log and show to ops."""


class AllowCountry(Frozen):
    type: Literal["allow_country"] = "allow_country"
    card_id: Text
    country: str = Field(pattern=r"^[A-Za-z]{2}$")
    hours: int

    @field_validator("country")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class UnblockMcc(Frozen):
    type: Literal["unblock_mcc"] = "unblock_mcc"
    card_id: Text
    mcc: Mcc
    hours: int


class TempLimitIncrease(Frozen):
    type: Literal["temp_limit_increase"] = "temp_limit_increase"
    card_id: Text
    new_per_txn_limit_usd: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    hours: int


class UnfreezeCard(Frozen):
    type: Literal["unfreeze_card"] = "unfreeze_card"
    card_id: Text


Action = Annotated[
    AllowCountry | UnblockMcc | TempLimitIncrease | UnfreezeCard, Field(discriminator="type")
]
_ACTION = TypeAdapter(Action)

# Which diagnosed cause each action type is allowed to fix.
FIXES: dict[type, set[Cause]] = {
    AllowCountry: {Cause.COUNTRY_RESTRICTED},
    UnblockMcc: {Cause.MCC_BLOCKED},
    TempLimitIncrease: {Cause.LIMIT_PER_TXN, Cause.LIMIT_DAILY},
    UnfreezeCard: {Cause.CARD_FROZEN_BY_USER},
}


def parse_action(raw: object) -> Action:
    try:
        return _ACTION.validate_python(raw)
    except ValidationError as exc:
        raise PolicyViolation(f"not a valid action: {exc.error_count()} problem(s)") from exc


def check_action(action: Action, *, employee_id: str, diagnosis: Diagnosis, repo: Repo) -> Action:
    if diagnosis.escalate:
        raise PolicyViolation("diagnosis is escalated to a human; no automated action")
    try:
        card = repo.card_for_employee(employee_id, action.card_id)
    except NotFound:
        raise PolicyViolation("card not available to this employee") from None

    if not FIXES[type(action)] & set(diagnosis.causes):
        raise PolicyViolation(f"{action.type} does not address the diagnosed cause")

    if hasattr(action, "hours") and not 1 <= action.hours <= MAX_HOURS:
        raise PolicyViolation(f"temporary actions must last 1-{MAX_HOURS} hours")

    controls = repo.controls_for_card(card.id)
    match action:
        case AllowCountry() if action.country in controls.allowed_countries:
            raise PolicyViolation(f"{action.country} is already allowed")
        case UnblockMcc() if action.mcc not in controls.blocked_mccs:
            raise PolicyViolation(f"category {action.mcc} is not blocked")
        case TempLimitIncrease():
            _check_limit(action, controls.per_txn_limit_usd, repo.company(card.company_id))
        case UnfreezeCard() if card.status is not CardStatus.FROZEN_BY_USER:
            raise PolicyViolation("only a card frozen by its holder can be unfrozen")
        case UnfreezeCard() if Cause.CARD_EXPIRED in diagnosis.causes:
            raise PolicyViolation("card is expired; unfreezing it would not help")
    return action


def _check_limit(action: TempLimitIncrease, current: Decimal, company) -> None:
    new = action.new_per_txn_limit_usd
    if new <= current:
        raise PolicyViolation(f"${new} is not an increase over ${current}")
    if new > current * MAX_LIMIT_MULTIPLIER:
        raise PolicyViolation(f"${new} exceeds 2x the current ${current} limit")
    if new > company.limit_cap_usd:
        raise PolicyViolation(f"${new} exceeds the company cap of ${company.limit_cap_usd}")
