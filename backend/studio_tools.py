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
from invoices import OPEN, PAID_ANY, paid_so_far_subquery
from models import Company, Contractor, ContractorPayment, Invoice, InvoiceCustomer, Transaction
from reports import signed_amount

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December"]


def _usd(v) -> str:
    return f"{Decimal(v or 0):.2f}"


def _month_label(ym: str) -> str:
    return f"{_MONTHS[int(ym[5:7]) - 1]} {ym[:4]}"


def customer_totals(session: Session, company: Company) -> dict:
    today = date.today()
    # 9-nov (deuda #45): una factura abierta con pagos parciales debe solo lo que falta; lo cobrado cuenta como cobrado.
    sofar = paid_so_far_subquery(company.id)
    part = func.coalesce(sofar.c.paid, 0)
    collected = func.coalesce(func.sum(case((Invoice.status.in_(PAID_ANY), Invoice.amount),
                                            (Invoice.status.in_(OPEN), part), else_=0)), 0)
    outstanding = func.coalesce(func.sum(case((Invoice.status.in_(OPEN) & (Invoice.due_date >= today), Invoice.amount - part), else_=0)), 0)
    overdue = func.coalesce(func.sum(case((Invoice.status.in_(OPEN) & (Invoice.due_date < today), Invoice.amount - part), else_=0)), 0)
    rows = session.exec(
        select(func.coalesce(InvoiceCustomer.name, "—"), func.count(Invoice.id), collected, outstanding, overdue)
        .select_from(Invoice).join(InvoiceCustomer, InvoiceCustomer.id == Invoice.customer_id, isouter=True)
        .join(sofar, sofar.c.invoice_id == Invoice.id, isouter=True)
        .where(Invoice.company_id == company.id).where(Invoice.status != "DRAFT")
        .group_by(InvoiceCustomer.name).order_by(collected.desc())
    ).all()
    tot = session.exec(
        select(collected, outstanding, overdue).select_from(Invoice)
        .join(sofar, sofar.c.invoice_id == Invoice.id, isouter=True)
        .where(Invoice.company_id == company.id).where(Invoice.status != "DRAFT")
    ).one()
    import invoices as inv_mod   # aquí: el historial de pago (SQL) que ya decide el tono de los recordatorios
    names = {c.id: c.name for c in session.exec(select(InvoiceCustomer).where(InvoiceCustomer.company_id == company.id)).all()}
    habits = sorted(({"customer": names.get(cid, "—"), "paid_invoices": h["paid"], "paid_after_due_date": h["late"],
                      "habit": h["kind"]} for cid, h in inv_mod.payer_history(session, company.id).items()),
                    key=lambda h: (-h["paid_after_due_date"], h["customer"]))
    return {
        # on_time = nunca tarde · sometimes_late = hasta la mitad tarde · often_late = más de la mitad tarde
        "payment_habits_latest_payers_first": habits,
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


def _pending(m: dict) -> dict:
    """Lo Pendiente de clasificar de un mes cerrado (la foto lo trae; no entra en ingresos ni en costos)."""
    p = m.get("pending") or {}
    return {"p_count": int(p.get("count") or 0), "p_in": Decimal(str(p.get("money_in") or 0)),
            "p_out": Decimal(str(p.get("money_out") or 0))}


def _pending_total(snaps: list[dict]) -> dict:
    """8-nov (regla 4): lo Pendiente se declara, nunca se omite. Si es cero, lo dice."""
    ps = [_pending(m) for m in snaps]
    n = sum(p["p_count"] for p in ps)
    if not n:
        return {"transactions": 0, "note": "Nothing pending: every transaction of the closed months is classified."}
    return {"transactions": n, "money_in_usd": _usd(sum(p["p_in"] for p in ps)),
            "money_out_usd": _usd(sum(p["p_out"] for p in ps)),
            "note": "Not included in income, costs or categories until the bookkeeper classifies them. Mention it."}


def month_totals(session: Session, company: Company) -> dict:
    snaps = sorted(month_close.owner_months(session, company.id), key=lambda m: m["month"])
    if not snaps:
        return {"none": "No closed months yet."}
    ms = [{"month": _month_label(m["month"]), "income": Decimal(str(m.get("total_in") or 0)),
           "costs": Decimal(str(m.get("total_out") or 0)), **_pending(m)} for m in snaps]
    for m in ms:
        m["net"] = m["income"] - m["costs"]
    # 10-nov (Jose: "¿gasté más en septiembre que en agosto?"): la diferencia contra el mes cerrado anterior la calcula el
    # código (la IA no resta ni compara cifras, regla 1). Positivo = subió; negativo = bajó.
    for prev, m in zip(ms, ms[1:]):
        m["vs_prev"] = {"previous_month": prev["month"], "income_change_usd": _usd(m["income"] - prev["income"]),
                        "costs_change_usd": _usd(m["costs"] - prev["costs"]),
                        "income_minus_costs_change_usd": _usd(m["net"] - prev["net"])}

    def fmt(m):
        out = {"month": m["month"], "income_usd": _usd(m["income"]), "costs_usd": _usd(m["costs"]),
               "income_minus_costs_usd": _usd(m["net"])}
        if m.get("vs_prev"):
            out["vs_previous_month"] = m["vs_prev"]
        if m["p_count"]:   # Lo Pendiente se declara (regla 4): no está en ingresos ni en costos
            out["pending_to_classify"] = {"transactions": m["p_count"], "money_in_usd": _usd(m["p_in"]),
                                          "money_out_usd": _usd(m["p_out"])}
        return out

    def top(k, hi):
        return fmt(sorted(ms, key=lambda m: m[k], reverse=hi)[0])

    return {
        "months_in_order": [fmt(m) for m in ms],
        "highest_income_month": top("income", True), "lowest_income_month": top("income", False),
        "highest_costs_month": top("costs", True), "lowest_costs_month": top("costs", False),
        "best_month_income_minus_costs": top("net", True),
        "all_closed_months_total": {"income_usd": _usd(sum(m["income"] for m in ms)), "costs_usd": _usd(sum(m["costs"] for m in ms)),
                                    "income_minus_costs_usd": _usd(sum(m["net"] for m in ms))},
        "pending_to_classify": _pending_total(snaps),
        "source": "the snapshots of the months the bookkeeper closed (computed by SQL at close)",
    }


def category_totals(session: Session, company: Company) -> dict:
    """Costos E INGRESOS por categoría, de todos los meses cerrados y de cada uno (10-nov, Jose: el asistente tiene que
    responder "¿qué fue lo que más gané en septiembre?" o "¿en qué gasté más en agosto?"). Todo sale de las fotos de cierre."""
    snaps = sorted(month_close.owner_months(session, company.id), key=lambda m: m["month"])
    out_by: dict[str, Decimal] = defaultdict(Decimal)
    in_by: dict[str, Decimal] = defaultdict(Decimal)

    def ranked(rows: dict[str, Decimal], key: str) -> list[dict]:
        return [{"rank": i + 1, "category": k, key: _usd(v)}
                for i, (k, v) in enumerate(sorted(rows.items(), key=lambda kv: kv[1], reverse=True))]

    months = []
    for m in snaps:
        mo: dict[str, Decimal] = defaultdict(Decimal)
        mi: dict[str, Decimal] = defaultdict(Decimal)
        for c in m.get("out") or []:
            mo[c["category"]] += Decimal(str(c.get("amount") or 0))
        for c in m.get("in") or []:
            mi[c["category"]] += Decimal(str(c.get("amount") or 0))
        for k, v in mo.items():
            out_by[k] += v
        for k, v in mi.items():
            in_by[k] += v
        months.append({"month": _month_label(m["month"]),
                       "income_by_category_largest_first": ranked(mi, "income_usd"),
                       "costs_by_category_largest_first": ranked(mo, "costs_usd")})
    if not out_by and not in_by:
        return {"none": "No closed months yet."}
    return {
        "categories_largest_first": ranked(out_by, "costs_usd"),
        "all_categories_total_usd": _usd(sum(out_by.values())),
        "income_categories_largest_first": ranked(in_by, "income_usd"),
        "all_income_categories_total_usd": _usd(sum(in_by.values())),
        "by_month": months,
        "pending_to_classify": _pending_total(snaps),
        "source": "the snapshots of the months the bookkeeper closed (computed by SQL at close)",
    }


def merchant_totals(session: Session, company: Company) -> dict:
    """Cuánto se gastó en cada COMERCIO (la subcategoría: Home Depot, Shell…) en los meses cerrados, dentro de su categoría,
    en el año y por mes (10-nov, Jose: "¿cuánto he gastado en determinada tienda?"). Solo totales, nunca transacciones
    sueltas (el dueño no ve movimientos). SQL sobre los meses cerrados VIGENTES: un mes cerrado no cambia (409
    `month_closed`), así que estas sumas coinciden con lo que se cerró. Mismo signo que el cierre (`signed_amount`).
    La IA no decide qué comercio es qué (regla 2): la categoría es la que pusieron una regla o el bookkeeper."""
    closed = sorted(m["month"] for m in month_close.owner_months(session, company.id))
    if not closed:
        return {"none": "No closed months yet."}
    ym = func.to_char(Transaction.date, "YYYY-MM")
    rows = session.exec(
        select(ym, Transaction.category, Transaction.subcategory, func.sum(signed_amount()),
               func.count(Transaction.id))
        .where(Transaction.company_id == company.id).where(Transaction.account_type.in_(month_close._OUT))
        .where(ym.in_(closed))
        .group_by(ym, Transaction.category, Transaction.subcategory)
    ).all()
    year: dict[tuple[str, str], list] = {}
    months: dict[str, list] = defaultdict(list)
    for mo, cat, merchant, amt, n in rows:
        amt, merchant = Decimal(str(amt or 0)), merchant or "—"
        k = (cat or "Other", merchant)
        year.setdefault(k, [Decimal(0), 0])
        year[k][0] += amt
        year[k][1] += int(n)
        months[mo].append((cat or "Other", merchant, amt))
    by_cat: dict[str, list] = defaultdict(list)
    for (cat, merchant), (amt, n) in year.items():
        by_cat[cat].append({"merchant": merchant, "costs_usd": amt, "payments": n})
    categories = sorted(({"category": c, "costs_usd": sum(x["costs_usd"] for x in ms),
                          "merchants_largest_first": sorted(ms, key=lambda x: x["costs_usd"], reverse=True)}
                         for c, ms in by_cat.items()), key=lambda c: c["costs_usd"], reverse=True)
    for c in categories:
        c["costs_usd"] = _usd(c["costs_usd"])
        for x in c["merchants_largest_first"]:
            x["costs_usd"] = _usd(x["costs_usd"])
    top = sorted(((m, cat, a) for (cat, m), (a, _) in year.items()), key=lambda r: r[2], reverse=True)[:10]
    return {
        "merchants_by_category": categories,
        "top_merchants": [{"rank": i + 1, "merchant": m, "category": c, "costs_usd": _usd(a)} for i, (m, c, a) in enumerate(top)],
        "by_month": [{"month": _month_label(mo), "merchants_largest_first": [
                         {"merchant": m, "category": c, "costs_usd": _usd(a)}
                         for c, m, a in sorted(months[mo], key=lambda r: r[2], reverse=True)[:12]]}
                     for mo in closed if mo in months],
        "note": "Merchant names are as they appear in the books. A merchant's category was set by a rule or by the bookkeeper.",
        "source": "SQL over the transactions of the months the bookkeeper closed (closed months cannot change)",
    }


SERVER_TOOLS: dict[str, Callable[[Session, Company], dict]] = {
    "customer_totals": customer_totals,
    "contractor_totals": contractor_totals,
    "month_totals": month_totals,
    "category_totals": category_totals,
    "merchant_totals": merchant_totals,
}
