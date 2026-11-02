"""End-to-end smoke run over everything built so far. `make smoke` runs it before each
phase tag, so you can see the system working on real seeded data, not just unit tests.
"""

import sqlite3
from datetime import timedelta

from decline_desk.data.seed import BASE_TIME, seed
from decline_desk.pii import mask_pii
from decline_desk.repo import NotFound, Repo


def main() -> None:
    repo = Repo(sqlite3.connect(":memory:"))
    seed(repo)

    print("== Companies")
    for co_id in ("co_andes", "co_paulista"):
        co = repo.company(co_id)
        print(f"  {co.name:<20} tz={co.timezone:<18} wallet=${co.wallet_balance_usd}")
    for topup in repo.pending_topups("co_andes"):
        print(f"  pending top-up: ${topup.amount_usd} {topup.asset}, settles {topup.eta:%H:%M} UTC")

    print("\n== Maria's authorizations, last 48h (newest first)")
    for a in repo.authorizations_for_employee(
        "emp_maria", since=BASE_TIME - timedelta(hours=48), until=BASE_TIME
    ):
        status = "DECLINED" if a.is_declined else "approved"
        print(
            f"  {a.created_at:%m-%d %H:%M}Z  {a.merchant_name:<18} {a.amount:>9} {a.currency}"
            f" = ${a.amount_usd:>7}  {a.country}  {a.response_code.value} {status}"
        )

    print("\n== IDOR guard: Maria asks for Carlos's card")
    try:
        repo.card_for_employee("emp_maria", "card_carlos")
        print("  FAIL: card returned")
    except NotFound:
        print("  ok: NotFound (same as a missing card)")

    print("\n== PII masking on a raw ticket")
    raw = "Hola, mi tarjeta 4111 1111 1111 1111 fue rechazada. Escribanme a maria@andes.co o +57 300 123 4567. Pago 1500000 COP."
    print(f"  in : {raw}")
    print(f"  out: {mask_pii(raw)}")


if __name__ == "__main__":
    main()
