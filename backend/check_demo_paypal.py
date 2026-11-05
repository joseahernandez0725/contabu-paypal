"""
PRUEBA DEL LIBRO SEMBRADO DE `paypal.contabu.com` (FASE 16 Paso 7, 2-nov-2026), sin red.

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_demo_paypal.py

Siembra la oficina de un juez y la de la visita, y comprueba: un solo libro · tres meses cerrados que el dueño ve · la
venta por el bruto, sin contar dos veces el depósito ni la compra · el libro del juez SIN conexión y la visita CON ella ·
que quien opera el libro actúa como dueño solo con `PAYPAL_DEMO`. Borra lo que crea. Código de salida 1 si algo falla.
"""

import os
import sys
from decimal import Decimal

os.environ.setdefault("AUTH_DEV_MODE", "true")

from sqlalchemy import func  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import demo  # noqa: E402
import demo_paypal  # noqa: E402
from db import engine  # noqa: E402
import main  # noqa: E402,F401  (registra el guardián y todas las tablas)
from models import (AccountType, Company, Firm, FirmMember, FirmRole, Invoice, MonthClose, PayPalConnection,  # noqa: E402
                    Role, Transaction, User)
from reports import signed_amount  # noqa: E402

fails: list[str] = []


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(label)


def office(s: Session, uid: str, linked: bool) -> Company:
    u = User(firebase_uid=uid, email=f"{uid}@test.local")
    s.add(u); s.flush()
    f = Firm(name=f"CHECK {uid}", owner_user_id=u.id)
    s.add(f); s.flush()
    s.add(FirmMember(firm_id=f.id, user_id=u.id, email=u.email, role=FirmRole.OWNER))
    books = demo_paypal.seed(s, f, u, linked=linked)
    expect("un solo libro", [b.name for b in books] == ["Rivera HVAC LLC"], len(books))
    return books[0]


def section(s: Session, company_id: int, at: AccountType, account=None) -> Decimal:
    q = select(func.sum(signed_amount())).where(Transaction.company_id == company_id).where(Transaction.account_type == at)
    return s.exec(q.where(Transaction.bank_account == account) if account else q).one() or Decimal(0)


def main_check() -> int:
    with Session(engine) as s:
        judge = office(s, "checkdemo-judge", False)
        tour = office(s, "checkdemo-tour", True)
        s.flush()
        closed = s.exec(select(func.count(MonthClose.id)).where(MonthClose.company_id == judge.id)).one()
        expect("tres meses cerrados", closed == 3, closed)
        paypal_rev = section(s, judge.id, AccountType.REVENUE, "PayPal")
        expect("ventas de PayPal por el bruto, menos el reembolso", paypal_rev == Decimal("5685.00"), paypal_rev)
        doubled = s.exec(select(func.count(Transaction.id)).where(Transaction.company_id == judge.id)
                         .where(Transaction.description.like("PAYPAL TRANSFER PPD%"))
                         .where(Transaction.account_type != AccountType.NON_PL)).one()
        expect("los depósitos del banco no cuentan como venta", doubled == 0, doubled)
        charge = s.exec(select(Transaction).where(Transaction.company_id == judge.id)
                        .where(Transaction.description == "PAYPAL *DELTA VALLEY PARTS")).one()
        expect("el cargo de la compra queda como transferencia", charge.category == "Transfer", charge.category)

        # El libro del banco propio (5-nov): nombres inventados, meses cerrados limpios, 4 Pendientes solo en octubre.
        brands = ("STARBUCKS", "HOME DEPOT", "CHEVRON", "SHELL", "INTUIT", "QUICKBOOKS", "AMAZON", "PG&E", "PGANDE",
                  "GUSTO", "GRAINGER", "FERGUSON", "JOHNSTONE", "STATE FARM", "VERIZON", "NETFLIX", "TARGET", "LOWE'S")
        descs = s.exec(select(Transaction.description).where(Transaction.company_id == judge.id)).all()
        found = sorted({b for d in descs for b in brands if b in (d or "").upper()})
        expect("ninguna marca real en las transacciones del libro", not found, found)
        month = func.date_trunc("month", Transaction.date)
        pend = dict(s.exec(select(func.to_char(month, "YYYY-MM"), func.count(Transaction.id))
                           .where(Transaction.company_id == judge.id).where(Transaction.account_type.is_(None))
                           .group_by(month)).all())
        expect("0 Pendientes en los meses cerrados; 4 en octubre (el mes abierto)", pend == {"2026-10": 4}, pend)
        import month_close
        neg = {m["month"]: m["negative"] for m in month_close.list_months(judge.id, s.get(User, judge.user_id), s)
               if m["negative"]}
        expect("ninguna cuenta negativa en ningún mes (las de contrapartida no cuentan)", not neg, neg)
        conn = lambda c: s.exec(select(PayPalConnection).where(PayPalConnection.company_id == c.id)).first()  # noqa: E731
        expect("el libro del juez NO viene conectado", conn(judge) is None, conn(judge))
        expect("la visita SÍ viene conectada", conn(tour) is not None and conn(tour).mode == "first_party", "first_party")
        open_inv = lambda c: s.exec(select(func.count(Invoice.id)).where(Invoice.company_id == c.id)  # noqa: E731
                                    .where(Invoice.status == "SENT")).one()
        expect("las facturas abiertas (ids inventados) solo en la visita", (open_inv(judge), open_inv(tour)) == (0, 1),
               (open_inv(judge), open_inv(tour)))
        snap = s.exec(select(MonthClose).where(MonthClose.company_id == judge.id).order_by(MonthClose.month)).first().snapshot
        expect("la foto del mes trae las ventas", any(r["category"] == "Gross receipts or sales" for r in snap["in"]), "ok")

        was = demo.PAYPAL_DEMO
        try:
            demo.PAYPAL_DEMO = False
            expect("fuera del demo, el bookkeeper NO actúa como dueño", not demo.acts_as_owner(Role.OWNER), "ok")
            demo.PAYPAL_DEMO = True
            expect("en el demo, sí", demo.acts_as_owner(Role.OWNER) and demo.acts_as_owner(Role.CLIENT), "ok")
            expect("la visita (VIEWER), nunca", not demo.acts_as_owner(Role.VIEWER), "ok")
        finally:
            demo.PAYPAL_DEMO = was
        s.rollback()   # nada de la prueba queda en la base
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main_check())
