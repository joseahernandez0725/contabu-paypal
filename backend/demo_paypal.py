"""
EL LIBRO SEMBRADO DE `paypal.contabu.com` (FASE 16 Paso 7, 2-nov-2026).

Decisión de Jose: un solo libro de prueba (Rivera HVAC LLC, ficticio) con todo lo que el portal muestra: tres meses
cerrados, ventas de PayPal con su comisión, un reembolso, retiros emparejados con su depósito, una compra emparejada con
su cargo y facturas cobradas. La visita guiada lo lleva `linked` a la cuenta de sandbox de la app; en el libro de un juez
no hay conexión: entra con las credenciales de su propia app.

Las filas pasan por el mismo camino que una sincronización real (`paypal.rows_for` → `paypal.insert_row`). Los ids son
inventados ("DEMO…"): nada de esto existe en PayPal, por eso las facturas abiertas solo van en la visita, que no consulta.
Solo `flush()`: el commit es de quien llama.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import Session

import demo_data
import month_close
import paypal
from ingestion import make_fingerprint
from models import (AccountType, Company, Firm, Invoice, InvoiceCustomer, MonthClose, PayPalConnection, PayPalTransferMatch,
                    Transaction, User)
from rules import load_rules

CUSTOMERS = [("Maple Grove Apartments", "billing@maplegrove.example.com"),
             ("Bright Path Dental", "office@brightpath.example.com"),
             ("Oak Street Bakery", "owner@oakstreet.example.com")]
# (cliente, concepto, monto, día del pago)
PAID_INVOICES = [(0, "Rooftop unit repair", "1850.00", date(2026, 7, 9)),
                 (1, "Quarterly maintenance", "640.00", date(2026, 7, 22)),
                 (2, "Walk-in cooler service", "925.00", date(2026, 8, 6)),
                 (0, "Boiler inspection", "480.00", date(2026, 8, 19)),
                 (1, "Thermostat replacement", "1275.00", date(2026, 9, 3)),
                 (2, "Quarterly maintenance", "640.00", date(2026, 9, 17))]
REFUND = (2, "-125.00", date(2026, 8, 25))
WITHDRAWALS = [("1800.00", date(2026, 7, 28)), ("2200.00", date(2026, 9, 21))]
PURCHASE = ("Grainger", "312.40", date(2026, 8, 12))
CLOSED = [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]


def _fee(gross: str) -> str:
    """La comisión publicada de una factura en EE. UU. (3.49% + $0.49, `FASE 16.A`), en negativo como la manda PayPal."""
    return str(-(Decimal(gross) * Decimal("0.0349") + Decimal("0.49")).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _event(tid: str, code: str, value: str, day: date, *, fee: str = None, name: str = None) -> dict:
    info = {"transaction_id": tid, "transaction_event_code": code, "transaction_status": "S",
            "transaction_initiation_date": f"{day.isoformat()}T17:00:00+0000",
            "transaction_amount": {"currency_code": "USD", "value": value}}
    if fee:
        info["fee_amount"] = {"currency_code": "USD", "value": fee}
    return {"transaction_info": info, "payer_info": {"payer_name": {"alternate_full_name": name}} if name else {}}


def _bank_row(session: Session, company: Company, account: str, day: date, description: str, amount: str, inflow: bool) -> Transaction:
    """El otro lado en el banco, ya emparejado: `Transfer`, como lo deja `paypal.match_transfers`."""
    signed = Decimal(amount) if inflow else -Decimal(amount)
    tx = Transaction(company_id=company.id, date=day, description=description, amount=Decimal(amount), is_inflow=inflow,
                     bank_account=account, fingerprint=make_fingerprint(day, description, signed, account),
                     account_type=AccountType.NON_PL, category="Transfer", source="transfer", confidence=1.0)
    session.add(tx)
    session.flush()
    return tx


def seed(session: Session, firm: Firm, owner: User, *, linked: bool) -> list[Company]:
    name, sector, account, make, rules_seed, plant = demo_data.BOOKS[0]
    company = demo_data._seed_book(session, firm, owner, name, sector, account, make(), rules_seed, plant)
    demo_data.demo_web_evidence.seed_lookups(session, firm)
    paypal.ensure_accounts(session, company)
    rules = load_rules(session, company.id, company.sector or "")

    customers = []
    for cname, email in CUSTOMERS:
        c = InvoiceCustomer(company_id=company.id, name=cname, email=email)
        session.add(c)
        customers.append(c)
    session.flush()

    def post(event: dict) -> Transaction:
        first = None
        for row in paypal.rows_for(event):
            tx = paypal.insert_row(session, company, row, rules)
            first = first or tx
        return first

    for n, (who, concept, amount, day) in enumerate(PAID_INVOICES, start=1):
        pid = f"DEMOPAY{n:04d}"
        tx = post(_event(pid, "T0006", amount, day, fee=_fee(amount), name=customers[who].name))
        paid_at = datetime(day.year, day.month, day.day, 17, tzinfo=timezone.utc)
        session.add(Invoice(company_id=company.id, customer_id=customers[who].id, paypal_invoice_id=f"INV2-DEMO-{n:04d}",
                            number=f"{n:04d}", concept=concept, amount=Decimal(amount), due_date=day + timedelta(days=5),
                            status="PAID", payment_id=pid, transaction_id=tx.id, sent_at=paid_at - timedelta(days=10),
                            paid_at=paid_at, created_by_user_id=owner.id))
    who, value, day = REFUND
    post(_event("DEMOREF0001", "T1107", value, day, fee="0.49", name=customers[who].name))

    for n, (amount, day) in enumerate(WITHDRAWALS, start=1):
        w = post(_event(f"DEMOWD{n:04d}", "T0400", f"-{amount}", day))
        dep = _bank_row(session, company, account, day + timedelta(days=2), f"PAYPAL TRANSFER PPD ID {n}", amount, True)
        session.add(PayPalTransferMatch(company_id=company.id, withdrawal_tx_id=w.id, bank_tx_id=dep.id, status="auto"))
    merchant, amount, day = PURCHASE
    p = post(_event("DEMOBUY0001", "T0001", f"-{amount}", day, name=merchant))
    charge = _bank_row(session, company, account, day + timedelta(days=1), f"PAYPAL *{merchant.upper()}", amount, False)
    session.add(PayPalTransferMatch(company_id=company.id, withdrawal_tx_id=p.id, bank_tx_id=charge.id, status="auto"))
    session.flush()

    for m in CLOSED:
        session.add(MonthClose(company_id=company.id, month=m, closed_by_user_id=owner.id,
                               snapshot=month_close.build_snapshot(session, company.id, m)))

    if linked:
        session.add(PayPalConnection(company_id=company.id, mode="first_party", environment=paypal.environment(),
                                     last_synced_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                     connected_by_user_id=owner.id))
        session.add(Invoice(company_id=company.id, customer_id=customers[0].id, paypal_invoice_id="INV2-DEMO-OPEN",
                            number="0007", concept="Chiller tune-up", amount=Decimal("2150.00"), due_date=date(2026, 10, 15),
                            status="SENT", sent_at=datetime(2026, 9, 30, 17, tzinfo=timezone.utc), created_by_user_id=owner.id))
    session.flush()
    return [company]


if __name__ == "__main__":   # el libro en una base LOCAL: `python demo_paypal.py <uid de desarrollo>`
    import os
    import sys
    from sqlmodel import select
    from db import engine
    from models import FirmMember, FirmRole
    if os.getenv("AUTH_DEV_MODE", "").lower() not in {"1", "true", "yes"} or len(sys.argv) != 2:
        sys.exit("Local only: AUTH_DEV_MODE=true python demo_paypal.py <uid>")
    with Session(engine) as s:
        uid = sys.argv[1]
        if s.exec(select(User).where(User.firebase_uid == uid)).first():
            sys.exit(f"'{uid}' already exists: use a new uid.")
        u = User(firebase_uid=uid, email=f"{uid}@dev.local")
        s.add(u)
        s.flush()
        f = Firm(name="Demo bookkeeping office", owner_user_id=u.id)
        s.add(f)
        s.flush()
        s.add(FirmMember(firm_id=f.id, user_id=u.id, email=u.email, role=FirmRole.OWNER))
        seed(s, f, u, linked=False)
        s.commit()
        print(f"Seeded Rivera HVAC LLC for '{uid}'.")
