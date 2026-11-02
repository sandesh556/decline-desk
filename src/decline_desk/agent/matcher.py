"""Decide which authorization a ticket refers to, using the hints triage extracted.

Every hint the employee gave must fit. A wrong merchant, country, amount or day rules a
transaction out. One fitting decline is a match. Several means we ask the employee which
one they mean. If none fit, we check whether the payment actually went through, and
otherwise tell them we never received the attempt.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from decline_desk.agent.triage import Hints
from decline_desk.domain.models import Authorization, Currency

WINDOW = timedelta(days=14)
AMOUNT_TOLERANCE = Decimal("0.10")


class MatchStatus(StrEnum):
    MATCH = "match"
    AMBIGUOUS = "ambiguous"
    APPROVED_NOT_DECLINED = "approved_not_declined"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class MatchResult:
    status: MatchStatus
    authorization: Authorization | None = None
    candidates: tuple[Authorization, ...] = ()


def match(
    hints: Hints, authorizations: list[Authorization], *, ticket_time: datetime, timezone: str
) -> MatchResult:
    tz = ZoneInfo(timezone)
    in_window = sorted(
        (a for a in authorizations if ticket_time - WINDOW <= a.created_at <= ticket_time),
        key=lambda a: (-a.created_at.timestamp(), a.id),  # newest first, ties by id
    )
    # Rates the employee's own authorizations captured, per currency, so an amount stated
    # in one currency can be compared with a charge made in another.
    rates = {a.currency: a.usd_per_unit for a in authorizations}
    fitting = [a for a in in_window if _fits(a, hints, ticket_time, tz, rates)]
    declines = [a for a in fitting if a.is_declined]

    if len(declines) == 1:
        return MatchResult(MatchStatus.MATCH, declines[0], tuple(declines))
    if len(declines) > 1:
        return MatchResult(MatchStatus.AMBIGUOUS, None, tuple(declines))
    approved = [a for a in fitting if not a.is_declined]
    if approved and _has_any(hints):
        return MatchResult(MatchStatus.APPROVED_NOT_DECLINED, approved[0], (approved[0],))
    return MatchResult(MatchStatus.NOT_FOUND)


def _has_any(h: Hints) -> bool:
    merchant = _norm(h.merchant or "") or None  # letterless text is not a usable hint
    return any(v is not None for v in (merchant, h.country, h.amount, h.days_ago))


def _fits(a: Authorization, h: Hints, ticket_time: datetime, tz: ZoneInfo, rates: dict) -> bool:
    if _norm(h.merchant or "") and not _merchant_fits(h.merchant, a.merchant_name):
        return False
    if h.country and h.country != a.country:
        return False
    if h.amount is not None and not _amount_fits(h.amount, h.currency, a, rates):
        return False
    if h.days_ago is not None:
        wanted = ticket_time.astimezone(tz).date() - timedelta(days=h.days_ago)
        if a.created_at.astimezone(tz).date() != wanted:
            return False
    return True


def _norm(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


def _merchant_fits(hint: str, merchant: str) -> bool:
    h, m = _norm(hint), _norm(merchant)
    return bool(h) and (h in m or m in h)


def _amount_fits(amount: Decimal, currency: Currency | None, a: Authorization, rates: dict) -> bool:
    if currency is None or currency is a.currency:
        return abs(a.amount - amount) <= amount * AMOUNT_TOLERANCE
    if currency is Currency.USD:
        stated_usd = amount
    elif currency in rates:
        stated_usd = amount * rates[currency]
    else:
        return True  # no rate for the stated currency: don't rule the charge out
    return abs(a.amount_usd - stated_usd) <= stated_usd * AMOUNT_TOLERANCE
