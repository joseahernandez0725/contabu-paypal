"""
FACTURAS RECURRENTES (FASE 16, área PayPal / portal del dueño · 4-nov-2026).

Un contrato de mantenimiento (cada mes, cada trimestre…): el dueño lo programa UNA vez y PayPal genera y ENVÍA sola cada
factura. Rutas que usa el Agent Toolkit oficial y que no están en la especificación pública; ✅ probadas en sandbox el 4-nov:
  · `POST /v2/invoicing/recurring-invoices` (crea la serie en DRAFT) → `POST …/{id}/activate` (ACTIVE)
  · `GET …/{id}`: estado, `completed_cycles`, `next_occurrence_date` · `POST …/{id}/cancel` (CANCELLED)
  · PayPal genera la primera factura ~30 s después de activar (si empieza hoy) y luego en cada fecha; usa la numeración
    normal y la envía sola. Cada factura generada lleva el MISMO `configuration.template_id` de la serie: así se reconoce.
  · Plazos aceptados: DUE_ON_RECEIPT, NET_10, NET_15, NET_30. Sin `total_cycles` = sin fin (PayPal devuelve 0).
ContaBu trae las facturas generadas a Cobros (`Invoice.series_id`) al abrir Cobros; desde ahí son facturas como las demás:
recordatorios, QR, pago fuera de PayPal, y su cobro entra al libro solo. La serie no asienta nada por sí misma.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

import paypal
from auth import current_user, owned_company
from db import get_session
from invoices import (MAX_AMOUNT, _connect, _fail, _invoicer, _name_parts, _now, _owner_only)
from models import Company, Invoice, InvoiceCustomer, InvoiceProfile, InvoiceSeries, User

router = APIRouter()

UNITS = ("WEEK", "MONTH", "YEAR")
TERMS = ("DUE_ON_RECEIPT", "NET_10", "NET_15", "NET_30")
LIVE = ("DRAFT", "ACTIVE")


def _out(x: InvoiceSeries, customers: dict) -> dict:
    c = customers.get(x.customer_id or 0)
    return {"id": x.id, "customer": {"id": c.id, "name": c.name, "email": c.email} if c else None, "concept": x.concept,
            "amount": str(x.amount), "interval_unit": x.interval_unit, "interval_count": x.interval_count,
            "start_date": x.start_date.isoformat(), "total_cycles": x.total_cycles, "payment_term": x.payment_term,
            "status": x.status, "completed_cycles": x.completed_cycles,
            "next_date": x.next_date.isoformat() if x.next_date and x.status == "ACTIVE" else None}


def view(session: Session, company_id: int) -> dict:
    rows = session.exec(select(InvoiceSeries).where(InvoiceSeries.company_id == company_id)
                        .order_by(InvoiceSeries.id.desc())).all()
    customers = {c.id: c for c in session.exec(select(InvoiceCustomer).where(InvoiceCustomer.company_id == company_id)).all()}
    return {"series": [_out(x, customers) for x in sorted(rows, key=lambda r: r.status not in LIVE)]}


def _apply(x: InvoiceSeries, g: dict) -> None:
    pd = g.get("plan_detail") or {}
    x.status = g.get("status") or x.status
    x.completed_cycles = int(pd.get("completed_cycles") or 0)
    nxt = pd.get("next_occurrence_date")
    x.next_date = date.fromisoformat(nxt[:10]) if nxt else None
    x.template_id = ((g.get("recurring_info") or {}).get("configuration") or {}).get("template_id") or x.template_id


# ── Traer a Cobros las facturas que generó cada serie ───────────────────────────────────────────

def _import_generated(session: Session, x: InvoiceSeries, c: Optional[InvoiceCustomer]) -> int:
    """Busca en PayPal las facturas de esta serie que ContaBu todavía no tiene (mismo cliente y monto, desde que empezó, y
    el mismo `template_id`) y las agrega a Cobros. Devuelve cuántas trajo."""
    if not c or not x.template_id:
        return 0
    known = set(session.exec(select(Invoice.paypal_invoice_id).where(Invoice.company_id == x.company_id)).all())
    amt = {"currency_code": x.currency, "value": f"{x.amount:.2f}"}
    res = paypal.api("POST", "/v2/invoicing/search-invoices", params={"page_size": 50}, json={
        "recipient_email": c.email, "total_amount_range": {"lower_amount": amt, "upper_amount": amt},
        "invoice_date_range": {"start": x.start_date.isoformat(), "end": (date.today() + timedelta(days=1)).isoformat()}})
    added = 0
    for it in res.get("items") or []:
        if it.get("id") in known:
            continue
        full = paypal.api("GET", f"/v2/invoicing/invoices/{it['id']}")
        if ((full.get("configuration") or {}).get("template_id")) != x.template_id:
            continue                                   # mismo cliente y monto, pero no es de esta serie
        d = full.get("detail") or {}
        meta = d.get("metadata") or {}
        issued = date.fromisoformat((d.get("invoice_date") or date.today().isoformat())[:10])
        due = (d.get("payment_term") or {}).get("due_date")
        session.add(Invoice(company_id=x.company_id, customer_id=c.id, paypal_invoice_id=full.get("id"),
                            number=d.get("invoice_number"), concept=x.concept, amount=x.amount, currency=x.currency,
                            due_date=date.fromisoformat(due[:10]) if due else issued, status=full.get("status") or "SENT",
                            recipient_view_url=meta.get("recipient_view_url"), sent_at=_now(), series_id=x.id,
                            created_by_user_id=x.created_by_user_id))
        added += 1
    return added


def sync(session: Session, company_id: int) -> int:
    """Al abrir Cobros (la conexión ya elegida): estado de cada serie viva y sus facturas nuevas. Sin commit."""
    added = 0
    for x in session.exec(select(InvoiceSeries).where(InvoiceSeries.company_id == company_id)
                          .where(InvoiceSeries.status.in_(LIVE))).all():
        _apply(x, paypal.api("GET", f"/v2/invoicing/recurring-invoices/{x.paypal_series_id}"))
        session.add(x)
        have = len(session.exec(select(Invoice.id).where(Invoice.series_id == x.id)).all())
        if x.completed_cycles > have:
            added += _import_generated(session, x, session.get(InvoiceCustomer, x.customer_id) if x.customer_id else None)
    session.flush()
    return added


# ── Lo que hace el dueño ────────────────────────────────────────────────────────────────────────

class SeriesIn(BaseModel):
    customer_id: int
    concept: str
    amount: str
    interval_unit: str = "MONTH"
    interval_count: int = 1
    start_date: date
    total_cycles: int = 0          # 0 = sin fin
    payment_term: str = "NET_15"


def _validated(p: SeriesIn) -> tuple[str, Decimal]:
    concept = p.concept.strip()
    try:
        amount = Decimal(p.amount.strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise HTTPException(status_code=422, detail="bad_amount")
    if not concept or len(concept) > 200:
        raise HTTPException(status_code=422, detail="bad_concept")
    if amount <= 0 or amount > MAX_AMOUNT or amount != amount.quantize(Decimal("0.01")):
        raise HTTPException(status_code=422, detail="bad_amount")
    if p.interval_unit not in UNITS or not 1 <= p.interval_count <= 12:
        raise HTTPException(status_code=422, detail="bad_frequency")
    if not 0 <= p.total_cycles <= 99:                               # ✅ el Toolkit: 0–99
        raise HTTPException(status_code=422, detail="bad_cycles")
    if p.payment_term not in TERMS:
        raise HTTPException(status_code=422, detail="bad_term")
    if p.start_date < date.today() or p.start_date > date.today() + timedelta(days=365):
        raise HTTPException(status_code=422, detail="bad_start")    # PayPal: no puede empezar en el pasado
    return concept, amount


@router.get("/api/owner/companies/{company_id}/series")
def list_series(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    owned_company(company_id, user, session)
    return view(session, company_id)


@router.post("/api/owner/companies/{company_id}/series")
def create_series(company_id: int, payload: SeriesIn, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    """Programa la serie y la ACTIVA (el dueño ya la revisó y confirmó en pantalla). PayPal envía cada factura sola."""
    company: Company = _owner_only(company_id, user, session)
    c = session.get(InvoiceCustomer, payload.customer_id)
    if c is None or c.company_id != company_id:
        raise HTTPException(status_code=404, detail="customer not found")
    concept, amount = _validated(payload)
    prof = session.exec(select(InvoiceProfile).where(InvoiceProfile.company_id == company_id)).first()
    detail = {"currency_code": "USD", "payment_term": {"term_type": payload.payment_term},
              **({"note": prof.note} if prof and prof.note else {}),
              **({"terms_and_conditions": prof.terms} if prof and prof.terms else {})}
    plan = {"frequency": {"interval_unit": payload.interval_unit, "interval_count": payload.interval_count},
            "start_series_date": payload.start_date.isoformat(),
            **({"total_cycles": payload.total_cycles} if payload.total_cycles else {})}
    body = {"plan_detail": plan, "recurring_info": {
        "detail": detail, "invoicer": _invoicer(company, prof),
        "primary_recipients": [{"billing_info": {"name": _name_parts(c.name), "email_address": c.email}}],
        "items": [{"name": concept[:200], "quantity": "1", "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}]}}
    _connect(session, company_id)
    try:
        r = paypal.api("POST", "/v2/invoicing/recurring-invoices", json=body, headers={"Prefer": "return=representation"})
        sid = r.get("id")
        paypal.api("POST", f"/v2/invoicing/recurring-invoices/{sid}/activate", json={})
        g = paypal.api("GET", f"/v2/invoicing/recurring-invoices/{sid}")
    except paypal.PayPalError as e:
        _fail(e)
    x = InvoiceSeries(company_id=company_id, customer_id=c.id, paypal_series_id=sid, concept=concept, amount=amount,
                      interval_unit=payload.interval_unit, interval_count=payload.interval_count,
                      start_date=payload.start_date, total_cycles=payload.total_cycles, payment_term=payload.payment_term,
                      created_by_user_id=user.id)
    _apply(x, g)
    session.add(x)
    session.commit()
    return view(session, company_id)


class SeriesEdit(BaseModel):
    concept: str
    amount: str
    payment_term: str = "NET_15"


@router.put("/api/owner/companies/{company_id}/series/{series_id}")
def edit_series(company_id: int, series_id: int, payload: SeriesEdit, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Cambia el concepto, el monto o el plazo de las PRÓXIMAS facturas (5-nov; p. ej. sube el precio del plan). ✅ Sandbox:
    PayPal acepta el PUT completo de una serie ACTIVA y la deja activa; las facturas ya enviadas no cambian; el calendario se
    conserva (ver abajo). PayPal no tiene "pausar" (una serie activa solo trae `update` y `cancel`)."""
    _owner_only(company_id, user, session)
    x = session.get(InvoiceSeries, series_id)
    if x is None or x.company_id != company_id:
        raise HTTPException(status_code=404, detail="series not found")
    if x.status not in LIVE:
        raise HTTPException(status_code=409, detail="series_not_active")
    concept, amount = _validated(SeriesIn(customer_id=0, concept=payload.concept, amount=payload.amount,
                                          interval_unit=x.interval_unit, interval_count=x.interval_count,
                                          start_date=max(x.start_date, date.today()), total_cycles=x.total_cycles,
                                          payment_term=payload.payment_term))
    _connect(session, company_id)
    try:
        g = paypal.api("GET", f"/v2/invoicing/recurring-invoices/{x.paypal_series_id}")
        ri = g.get("recurring_info") or {}
        item = {**((ri.get("items") or [{}])[0]), "name": concept[:200],
                "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}
        item.pop("id", None)
        pd = g.get("plan_detail") or {}
        # ✅ Sandbox 5-nov: el PUT REINICIA el calendario desde `start_series_date` (con la de origen, la próxima volvió a
        # hoy: riesgo de cobrar dos veces), y en el GET `total_cycles` es lo que FALTA (3 → 2 tras la primera). Se manda
        # como inicio la PRÓXIMA fecha y lo que falta: el calendario sigue donde iba y no se acorta ni se alarga.
        plan = {"frequency": pd.get("frequency"), "start_series_date": pd.get("next_occurrence_date") or pd.get("start_series_date"),
                **({"total_cycles": pd["total_cycles"]} if pd.get("total_cycles") else {})}
        body = {"plan_detail": plan,
                "recurring_info": {"detail": {**(ri.get("detail") or {}), "payment_term": {"term_type": payload.payment_term}},
                                   "invoicer": ri.get("invoicer"), "primary_recipients": ri.get("primary_recipients"),
                                   "items": [item]}}
        paypal.api("PUT", f"/v2/invoicing/recurring-invoices/{x.paypal_series_id}", json=body,
                   headers={"Prefer": "return=representation"})
        _apply(x, paypal.api("GET", f"/v2/invoicing/recurring-invoices/{x.paypal_series_id}"))
    except paypal.PayPalError as e:
        _fail(e)
    x.concept, x.amount, x.payment_term = concept, amount, payload.payment_term
    session.add(x)
    session.commit()
    return view(session, company_id)


@router.post("/api/owner/companies/{company_id}/series/{series_id}/cancel")
def cancel_series(company_id: int, series_id: int, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    """Detiene la serie: PayPal no genera más facturas. Las ya enviadas siguen como están (se cancelan una por una)."""
    _owner_only(company_id, user, session)
    x = session.get(InvoiceSeries, series_id)
    if x is None or x.company_id != company_id:
        raise HTTPException(status_code=404, detail="series not found")
    if x.status not in LIVE:
        raise HTTPException(status_code=409, detail="series_not_active")
    _connect(session, company_id)
    try:
        paypal.api("POST", f"/v2/invoicing/recurring-invoices/{x.paypal_series_id}/cancel", json={})
        _apply(x, paypal.api("GET", f"/v2/invoicing/recurring-invoices/{x.paypal_series_id}"))
    except paypal.PayPalError as e:
        _fail(e)
    session.add(x)
    session.commit()
    return view(session, company_id)
