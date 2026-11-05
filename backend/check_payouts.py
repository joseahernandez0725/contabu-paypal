"""
PRUEBA DE LOS PAGOS A CONTRATISTAS CON PAYPAL PAYOUTS, SIN RED (FASE 16, 4-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_payouts.py

Reemplaza a PayPal por uno de mentira que se comporta como el sandbox (✅ 4-nov: PENDING → SUCCESS con `transaction_id` y
comisión de $0.25) y comprueba: el interruptor (apagado: `enabled: false` y 404); solo el dueño agrega y paga; no se paga
más que el saldo disponible; PayPal recibe un `sender_batch_id` único; confirmado, entra al libro como COGS · Subcontractors
(la cuenta se agrega al plan si falta) y la comisión aparte en Merchant fees; la sincronización no lo duplica; lo pagado en
el año por contratista lo suma el código sobre lo confirmado.
Código de salida 1 si algo falla. No sale a internet (usa el bloqueo de check_permissions).
"""

import os
import sys
from datetime import date
from decimal import Decimal

os.environ.setdefault("AUTH_DEV_MODE", "true")

import check_permissions as cp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import paypal  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import AccountType, CompanyCategory, ContractorPayment, PayPalConnection, Transaction  # noqa: E402

fails: list[str] = []
CALLS: list[tuple] = []
STATE = {"balance": "100.00", "item": "PENDING", "n": 0}


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def fake_api(method, path, **kw):
    CALLS.append((method, path, kw.get("json")))
    if path == "/v1/reporting/balances":
        return {"balances": [{"available_balance": {"currency_code": "USD", "value": STATE["balance"]}}]}
    if method == "POST" and path == "/v1/payments/payouts":
        STATE["n"] += 1
        return {"batch_header": {"payout_batch_id": f"BATCH{STATE['n']}", "batch_status": "PENDING"}}
    if method == "GET" and path.startswith("/v1/payments/payouts/"):
        done = STATE["item"] == "SUCCESS"
        return {"batch_header": {"batch_status": "SUCCESS" if done else "PROCESSING"},
                "items": [{"transaction_status": STATE["item"], "payout_item_id": "ITEM1",
                           **({"transaction_id": "PAYOUTTX1", "payout_item_fee": {"currency": "USD", "value": "0.25"}} if done else {})}]}
    return {}


def main() -> int:
    paypal.api = fake_api
    client = TestClient(app, raise_server_exceptions=False)
    cl = {"X-Dev-Uid": cp.CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": cp.BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    with Session(engine) as s:
        cp.drop_fixtures(s)
        company_id, _ = cp.build_fixtures(s)
        s.add(PayPalConnection(company_id=company_id, mode="first_party", environment="sandbox"))
        s.commit()
    base = f"/api/owner/companies/{company_id}/contractors"
    try:
        print("\n  --- pagos a contratistas (sin red) ---")
        os.environ["PAYPAL_PAYOUTS"] = ""
        r = client.get(base, headers=cl)
        expect("interruptor APAGADO: la lista dice enabled=false", r.json() == {"enabled": False}, r.json())
        r = client.post(base, headers=cl, json={"name": "Carlos", "email": "carlos@test.local"})
        expect("interruptor APAGADO: agregar → 404", r.status_code == 404, r.status_code)
        os.environ["PAYPAL_PAYOUTS"] = "true"

        r = client.post(base, headers=bk, json={"name": "Carlos", "email": "carlos@test.local"})
        expect("el bookkeeper NO agrega contratistas", r.status_code == 403, r.status_code)
        r = client.post(base, headers=cl, json={"name": "Carlos Ruiz", "email": "carlos@test.local"})
        cid = (r.json().get("contractors") or [{}])[0].get("id")
        expect("el dueño lo agrega", r.status_code == 200 and cid, r.status_code)

        r = client.post(f"{base}/{cid}/pay", headers=bk, json={"amount": "40.00"})
        expect("el bookkeeper NO paga", r.status_code == 403, r.status_code)
        r = client.post(f"{base}/{cid}/pay", headers=cl, json={"amount": "150.00"})
        expect("más que el saldo disponible ($100) → 409 y PayPal no paga", r.status_code == 409
               and not any(c[1] == "/v1/payments/payouts" for c in CALLS), r.status_code)
        r = client.post(f"{base}/{cid}/pay", headers=cl, json={"amount": "0"})
        expect("monto inválido → 422", r.status_code == 422, r.status_code)

        CALLS.clear()
        r = client.post(f"{base}/{cid}/pay", headers=cl, json={"amount": "40.00", "memo": "Smith install - helper"})
        post = next((c for c in CALLS if c[1] == "/v1/payments/payouts"), None)
        b = post[2] if post else {}
        with Session(engine) as s:
            pid = s.exec(select(ContractorPayment).where(ContractorPayment.company_id == company_id)).first().id
        expect("PayPal recibe un sender_batch_id único por pago, el correo y el monto",
               b.get("sender_batch_header", {}).get("sender_batch_id") == f"CB-{company_id}-{pid}"
               and b["items"][0]["receiver"] == "carlos@test.local" and b["items"][0]["amount"] == {"value": "40.00", "currency": "USD"},
               b.get("sender_batch_header"))
        pay = (r.json().get("payments") or [{}])[0]
        expect("todavía PENDING: no entra al libro", pay.get("status") == "PENDING" and not pay.get("in_books"), pay)

        STATE["item"] = "SUCCESS"
        r = client.post(f"{base}/refresh", headers=cl)
        pay = (r.json().get("payments") or [{}])[0]
        expect("confirmado: SUCCESS, en contabilidad, con su comisión", pay.get("status") == "SUCCESS" and pay.get("in_books")
               and pay.get("fee") == "0.25", pay)
        with Session(engine) as s:
            rows = s.exec(select(Transaction).where(Transaction.company_id == company_id)
                          .where(Transaction.description.like("PAYPAL%"))).all()
            main = next((t for t in rows if "PAYOUT TO" in t.description), None)
            fee = next((t for t in rows if t.description == "PAYPAL FEE"), None)
            expect("el pago → COGS · Subcontractors, salida de $40", main and (main.account_type, main.category, main.amount, main.is_inflow)
                   == (AccountType.COGS, "Subcontractors", Decimal("40.00"), False), main and (main.account_type, main.category))
            expect("la comisión → Merchant fees, $0.25", fee and (fee.category, fee.amount) == (paypal.FEE, Decimal("0.25")),
                   fee and (fee.category, fee.amount))
            cat = s.exec(select(CompanyCategory).where(CompanyCategory.company_id == company_id)
                         .where(CompanyCategory.name == "Subcontractors")).first()
            expect("'Subcontractors' se agregó al plan del libro (COGS)", cat and cat.account_type == AccountType.COGS, cat and cat.account_type)
            from models import Company
            company = s.get(Company, company_id)
            before = len(rows)
            ev = {"transaction_info": {"transaction_id": "PAYOUTTX1", "transaction_event_code": "T0001", "transaction_status": "S",
                                       "transaction_initiation_date": f"{date.today()}T10:00:00+0000",
                                       "transaction_amount": {"currency_code": "USD", "value": "-40.00"},
                                       "fee_amount": {"currency_code": "USD", "value": "-0.25"}}}
            from models import PayPalEntry
            known = {e.paypal_id for e in s.exec(select(PayPalEntry).where(PayPalEntry.company_id == company_id)).all()}
            new = [rw for rw in paypal.rows_for(ev) if rw["paypal_id"] not in known]
            expect("la sincronización reconoce el pago y su comisión (mismo transaction_id): nada que duplicar", new == [],
                   [rw["paypal_id"] for rw in new])
        print("\n  --- editar y borrar (5-nov) ---")
        r = client.put(f"{base}/{cid}", headers=bk, json={"name": "X", "email": "x@test.local"})
        expect("el bookkeeper NO edita contratistas", r.status_code == 403, r.status_code)
        r = client.put(f"{base}/{cid}", headers=cl, json={"name": "Carlos A. Ruiz", "email": "carlos.ruiz@test.local"})
        c0 = next((c for c in r.json().get("contractors", []) if c["id"] == cid), {})
        expect("el dueño corrige nombre y correo", (c0.get("name"), c0.get("email")) == ("Carlos A. Ruiz", "carlos.ruiz@test.local"), c0)
        r = client.delete(f"{base}/{cid}", headers=cl)
        expect("con pagos NO se borra → 409 has_payments", r.status_code == 409 and r.json().get("detail") == "has_payments", r.status_code)
        r = client.post(base, headers=cl, json={"name": "Temp", "email": "temp@test.local"})
        tmp = next(c["id"] for c in r.json()["contractors"] if c["name"] == "Temp")
        r = client.delete(f"{base}/{tmp}", headers=cl)
        expect("sin pagos sí se borra", r.status_code == 200 and all(c["id"] != tmp for c in r.json()["contractors"]), r.status_code)
        r = client.get(base, headers=bk)
        c0 = (r.json().get("contractors") or [{}])[0]
        expect("lo pagado en el año (lo suma el código): $40; el bookkeeper lo ve", r.status_code == 200 and c0.get("paid_this_year") == "40.00", c0)
    finally:
        os.environ.pop("PAYPAL_PAYOUTS", None)
        with Session(engine) as s:
            cp.drop_fixtures(s)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
