"""
LAS HERRAMIENTAS DEL ASISTENTE QUE CORREN EN EL SERVIDOR (FASE 16, rama `paypal-contest`, 8-nov).

AG Studio las declara como `server`: el navegador las anuncia al modelo pero no las ejecuta. Las ejecuta `studio_ai.py`
dentro de su propio ciclo, con las cifras de SQL — "las cifras las calcula SQL", sin pasar por el navegador:
- `customer_totals` y `contractor_totals`: SUM/COUNT de SQL sobre las facturas y los pagos de Payouts del libro.
- `month_totals` y `category_totals`: las FOTOS de los meses cerrados (`month_close.owner_months`, calculadas por SQL al
  cerrar); el código solo las ordena y dice el mayor y el menor.
Todas devuelven además los totales, el orden y el mayor/menor: el modelo no suma ni compara (8-nov, juego de 10 preguntas:
cuando sumaba o comparaba él, se equivocaba). Solo LEEN; ninguna cambia nada.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Callable

from sqlalchemy import case, func
from sqlmodel import Session, select

import month_close
from invoices import OPEN, PAID_ANY
from models import Company, Contractor, ContractorPayment, Invoice, InvoiceCustomer

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December"]


def _usd(v) -> str:
    return f"{Decimal(v or 0):.2f}"


def _month_label(ym: str) -> str:
    return f"{_MONTHS[int(ym[5:7]) - 1]} {ym[:4]}"


def customer_totals(session: Session, company: Company) -> dict:
    today = date.today()
    collected = func.coalesce(func.sum(case((Invoice.status.in_(PAID_ANY), Invoice.amount), else_=0)), 0)
    outstanding = func.coalesce(func.sum(case((Invoice.status.in_(OPEN) & (Invoice.due_date >= today), Invoice.amount), else_=0)), 0)
    overdue = func.coalesce(func.sum(case((Invoice.status.in_(OPEN) & (Invoice.due_date < today), Invoice.amount), else_=0)), 0)
    rows = session.exec(
        select(func.coalesce(InvoiceCustomer.name, "—"), func.count(Invoice.id), collected, outstanding, overdue)
        .select_from(Invoice).join(InvoiceCustomer, InvoiceCustomer.id == Invoice.customer_id, isouter=True)
        .where(Invoice.company_id == company.id).where(Invoice.status != "DRAFT")
        .group_by(InvoiceCustomer.name).order_by(collected.desc())
    ).all()
    tot = session.exec(
        select(collected, outstanding, overdue).select_from(Invoice)
        .where(Invoice.company_id == company.id).where(Invoice.status != "DRAFT")
    ).one()
    return {
        "customers_ordered_by_collected": [
            {"rank_by_collected": i + 1, "customer": r[0], "invoices": int(r[1]), "collected_usd": _usd(r[2]),
             "outstanding_usd": _usd(r[3]), "overdue_usd": _usd(r[4])} for i, r in enumerate(rows)],
        "all_customers_total": {"collected_usd": _usd(tot[0]), "outstanding_usd": _usd(tot[1]), "overdue_usd": _usd(tot[2])},
        "source": "SQL over the business's invoices (paid, outstanding = sent and not yet due, overdue = sent and past due)",
    }


def contractor_totals(session: Session, company: Company) -> dict:
    paid = func.coalesce(func.sum(ContractorPayment.amount), 0)
    fees = func.coalesce(func.sum(ContractorPayment.fee), 0)
    rows = session.exec(
        select(func.coalesce(Contractor.name, "—"), func.count(ContractorPayment.id), paid, fees)
        .select_from(ContractorPayment).join(Contractor, Contractor.id == ContractorPayment.contractor_id, isouter=True)
        .where(ContractorPayment.company_id == company.id).where(ContractorPayment.status == "SUCCESS")
        .group_by(Contractor.name).order_by(paid.desc())
    ).all()
    if not rows:
        return {"none": "No contractor payments yet."}
    tot = session.exec(
        select(paid, fees).select_from(ContractorPayment)
        .where(ContractorPayment.company_id == company.id).where(ContractorPayment.status == "SUCCESS")
    ).one()
    return {
        "contractors_ordered_by_paid": [{"contractor": r[0], "payments": int(r[1]), "paid_usd": _usd(r[2]),
                                         "paypal_fee_usd": _usd(r[3])} for r in rows],
        "all_contractors_total": {"paid_usd": _usd(tot[0]), "paypal_fee_usd": _usd(tot[1])},
        "source": "SQL over the PayPal Payouts the business sent",
    }


def month_totals(session: Session, company: Company) -> dict:
    snaps = sorted(month_close.owner_months(session, company.id), key=lambda m: m["month"])
    if not snaps:
        return {"none": "No closed months yet."}
    ms = [{"month": _month_label(m["month"]), "income": Decimal(str(m.get("total_in") or 0)),
           "costs": Decimal(str(m.get("total_out") or 0))} for m in snaps]
    for m in ms:
        m["net"] = m["income"] - m["costs"]

    def fmt(m):
        return {"month": m["month"], "income_usd": _usd(m["income"]), "costs_usd": _usd(m["costs"]),
                "income_minus_costs_usd": _usd(m["net"])}

    def top(k, hi):
        return fmt(sorted(ms, key=lambda m: m[k], reverse=hi)[0])

    return {
        "months_in_order": [fmt(m) for m in ms],
        "highest_income_month": top("income", True), "lowest_income_month": top("income", False),
        "highest_costs_month": top("costs", True), "lowest_costs_month": top("costs", False),
        "best_month_income_minus_costs": top("net", True),
        "all_closed_months_total": {"income_usd": _usd(sum(m["income"] for m in ms)), "costs_usd": _usd(sum(m["costs"] for m in ms)),
                                    "income_minus_costs_usd": _usd(sum(m["net"] for m in ms))},
        "source": "the snapshots of the months the bookkeeper closed (computed by SQL at close)",
    }


def category_totals(session: Session, company: Company) -> dict:
    by: dict[str, Decimal] = defaultdict(Decimal)
    for m in month_close.owner_months(session, company.id):
        for c in m.get("out") or []:
            by[c["category"]] += Decimal(str(c.get("amount") or 0))
    if not by:
        return {"none": "No closed months yet."}
    ordered = sorted(by.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "categories_largest_first": [{"rank": i + 1, "category": k, "costs_usd": _usd(v)} for i, (k, v) in enumerate(ordered)],
        "all_categories_total_usd": _usd(sum(by.values())),
        "source": "the snapshots of the months the bookkeeper closed (computed by SQL at close)",
    }


SERVER_TOOLS: dict[str, Callable[[Session, Company], dict]] = {
    "customer_totals": customer_totals,
    "contractor_totals": contractor_totals,
    "month_totals": month_totals,
    "category_totals": category_totals,
}
