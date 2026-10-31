"""
FASE 12 — la prueba del SIGNO dentro de la sección del P&L (`reports.signed_amount()`, 31-oct).

Siembra un libro de prueba (sin oficina, nada del libro real) en una transacción que se DESHACE al
final, y comprueba:
1. Una devolución a un cliente (salida en REVENUE) RESTA de los ingresos y su categoría sale en
   negativo; un reembolso de un proveedor (entrada en COGS) RESTA del costo.
   Venta 1,000 − devolución 200 = Revenue 800 · gasto 500 − reembolso 50 = COGS 450 · Gross Profit 350.
2. `is_inflow` NULL cuenta en la dirección natural de su sección y el P&L lo DECLARA aparte.
3. NON_PL y lo Pendiente no entran al resultado (lo Pendiente sigue declarado como caja).
4. La misma expresión agrupa por (mes, sección, categoría), como la usará el Resumen de la FASE 16.

Corre así:
    .venv/bin/python check_pnl_sign.py
Salida: un renglón por caso y **código de salida 1** si algo falló.
"""

import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlmodel import Session, select

from db import engine
from models import AccountType, Company, Transaction
from reports import income_statement, signed_amount

D = Decimal
REV, COGS, OPEX, NON_PL = (AccountType.REVENUE, AccountType.COGS,
                           AccountType.OPERATING_EXPENSE, AccountType.NON_PL)

ROWS = [
    # fecha, descripción, monto, entra?, sección, categoría
    (date(2026, 9, 3), "INVOICE 101 CUSTOMER", "1000.00", True, REV, "Gross receipts or sales"),
    (date(2026, 9, 20), "REFUND TO CUSTOMER", "200.00", False, REV, "Returns and allowances"),
    (date(2026, 9, 5), "SUPPLIER PURCHASE", "500.00", False, COGS, "Purchases"),
    (date(2026, 10, 2), "SUPPLIER REFUND", "50.00", True, COGS, "Purchases"),
    (date(2026, 10, 8), "BANK DID NOT SAY", "10.00", None, OPEX, "Office expense"),
    (date(2026, 10, 9), "OWNER TRANSFER", "999.00", False, NON_PL, "Owner Draw"),
    (date(2026, 10, 10), "NOT CLASSIFIED YET", "77.00", True, None, None),
]


def main() -> int:
    fails: list[str] = []

    def check(name: str, got, want) -> None:
        if got == want:
            print(f"  ✅ {name}: {got}")
        else:
            fails.append(name)
            print(f"  ❌ {name}: {got} (esperaba {want})")

    with Session(engine) as s:
        try:
            book = Company(name="CHECK PNL SIGN", sector="other")
            s.add(book); s.flush()
            for d, desc, amount, inflow, at, cat in ROWS:
                s.add(Transaction(company_id=book.id, date=d, description=desc, amount=D(amount),
                                  is_inflow=inflow, account_type=at, category=cat))
            s.flush()

            pnl = income_statement(s, book.id)
            print("  --- el P&L ---")
            check("Revenue", pnl["revenue"], D("800.00"))
            check("COGS", pnl["cogs"], D("450.00"))
            check("Gross Profit", pnl["gross_profit"], D("350.00"))
            check("Operating expenses (la fila sin dirección, natural)", pnl["operating_expenses"], D("10.00"))
            check("Net income (NON_PL y Pendiente fuera)", pnl["net_income"], D("340.00"))

            print("  --- el desglose por categoría ---")
            rev = {r["category"]: r["amount"] for r in pnl["breakdown"]["REVENUE"]}
            check("Gross receipts or sales", rev.get("Gross receipts or sales"), D("1000.00"))
            check("Returns and allowances (en negativo)", rev.get("Returns and allowances"), D("-200.00"))
            cogs = {r["category"]: r["amount"] for r in pnl["breakdown"]["COGS"]}
            check("Purchases (500 − 50)", cogs.get("Purchases"), D("450.00"))
            for sec, rows in pnl["breakdown"].items():
                total = sum((r["amount"] for r in rows), D("0"))
                want = {"REVENUE": pnl["revenue"], "COGS": pnl["cogs"],
                        "OPERATING_EXPENSE": pnl["operating_expenses"],
                        "OTHER_INCOME": pnl["other_income"], "OTHER_EXPENSE": pnl["other_expense"]}[sec]
                check(f"el desglose de {sec} cuadra con su total", total, want)

            print("  --- lo que se declara aparte ---")
            check("filas sin dirección, declaradas", (pnl["direction_unknown_count"],
                  pnl["direction_unknown_amount"]), (1, D("10.00")))
            u = pnl["uncategorized"]
            check("lo Pendiente (caja)", (u["count"], u["money_in"]), (1, D("77.00")))

            print("  --- la misma expresión por (mes, sección, categoría), como la FASE 16 ---")
            month = func.to_char(Transaction.date, "YYYY-MM")
            stmt = (select(month, Transaction.account_type, Transaction.category, func.sum(signed_amount()))
                    .where(Transaction.company_id == book.id)
                    .where(Transaction.account_type.is_not(None), Transaction.account_type != NON_PL)
                    .group_by(month, Transaction.account_type, Transaction.category))
            got = {(m, at.value, c): v for m, at, c, v in s.exec(stmt).all()}
            check("2026-09 · REVENUE · Returns and allowances", got.get(("2026-09", "REVENUE", "Returns and allowances")), D("-200.00"))
            check("2026-09 · COGS · Purchases", got.get(("2026-09", "COGS", "Purchases")), D("500.00"))
            check("2026-10 · COGS · Purchases (el reembolso solo)", got.get(("2026-10", "COGS", "Purchases")), D("-50.00"))
        finally:
            s.rollback()  # nada de esta prueba queda en la base

    print(f"\n{'❌ ' + str(len(fails)) + ' fallaron' if fails else '✅ todo bien'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
