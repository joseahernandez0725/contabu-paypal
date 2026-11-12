"""
PAGOS Y DEVOLUCIONES FUERA DE PAYPAL (FASE 16, área PayPal / portal del dueño · 3-nov-2026).

Un contratista cobra muchas veces en efectivo, con cheque o por transferencia. Sin esto, esa factura quedaba "por cobrar"
para siempre en PayPal y en ContaBu. Ahora:

  1. EL DUEÑO lo registra en la factura (`POST /v2/invoicing/invoices/{id}/payments` · `/refunds`, ✅ especificación
     oficial `invoicing_v2.json`: método CASH, CHECK, BANK_TRANSFER, WIRE_TRANSFER u OTHER). PayPal la deja en
     MARKED_AS_PAID (o MARKED_AS_REFUNDED): no es `PAID`, que es lo cobrado POR PayPal. Con confirmación en pantalla.
  2. CONTABU NO CREA VENTA (decisión de Jose, 3-nov): el dinero entra (o sale) por el BANCO, y esa fila ya llega al libro
     por Plaid o por el extracto. Asentar la venta aquí la contaría dos veces.
  3. EL CÓDIGO EMPAREJA la fila del banco (mismo monto, de 3 días antes a 10 después, en la dirección que toca): si hay
     UNA sola y está Pendiente (o ya en la categoría correcta), queda como venta (*Gross receipts or sales*) o como
     devolución (*Returns and allowances*), ligada a la factura. Si hay varias, la elige el bookkeeper en la consola.
     Nunca toca una fila escrita a mano ni de un mes cerrado. La IA no participa.
  4. MIENTRAS NO HAY FILA DEL BANCO (el efectivo que no se depositó) se DECLARA en Cobros: "cobrado fuera de PayPal,
     todavía no está en el banco" (regla 4).
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

import month_close
import paypal
from auth import console_company, current_user, owned_company
from corrections import record_change, snapshot
from db import get_session
from invoices import OPEN, PAID_ANY, _apply_state, _connect, _fail, _now, _owner_only, _view, no_demo
from models import (AccountType, Company, Invoice, InvoiceCustomer, InvoiceOffline, PayPalTransferMatch, Transaction,
                    User)

router = APIRouter()

METHODS = ("CASH", "CHECK", "BANK_TRANSFER", "WIRE_TRANSFER", "OTHER")
BEFORE, AFTER = 3, 10                      # días alrededor de la fecha que dijo el dueño
VIA = "invoice_offline"                    # el historial de correcciones dice de dónde vino el cambio
_TARGET = {"payment": (AccountType.REVENUE, paypal.SALE), "refund": (AccountType.REVENUE, paypal.REFUND)}


# ── El emparejamiento con el banco (sin red: se prueba solo) ─────────────────────────────────────

def _taken(session: Session, company_id: int) -> set[int]:
    """Filas del banco que ya tienen dueño: otra factura o un retiro/compra de PayPal."""
    a = {o.bank_tx_id for o in session.exec(select(InvoiceOffline).where(InvoiceOffline.company_id == company_id)).all()
         if o.bank_tx_id}
    b = {m.bank_tx_id for m in session.exec(select(PayPalTransferMatch)
                                            .where(PayPalTransferMatch.company_id == company_id)).all() if m.bank_tx_id}
    return a | b


def candidates(session: Session, o: InvoiceOffline, taken: set[int]) -> list[Transaction]:
    """Filas del BANCO (no de PayPal) con el mismo monto, en la ventana y la dirección que tocan, sin dueño, de un mes
    abierto, no escritas a mano, y Pendientes o ya en la categoría correcta."""
    target = _TARGET[o.kind]
    rows = session.exec(
        select(Transaction).where(Transaction.company_id == o.company_id)
        .where((Transaction.bank_account.is_(None)) | (Transaction.bank_account != paypal.ACCOUNT_LABEL))
        .where(Transaction.amount == o.amount).where(Transaction.is_inflow.is_(o.kind == "payment"))
        .where(Transaction.date >= o.day - timedelta(days=BEFORE)).where(Transaction.date <= o.day + timedelta(days=AFTER))
        .order_by(Transaction.date)
    ).all()
    return [t for t in rows if t.id not in taken and t.source != "manual" and not month_close.is_locked(session, t)
            and (t.account_type is None or (t.account_type, t.category) == target)]


def _book(session: Session, o: InvoiceOffline, t: Transaction, *, source: str, user_id: Optional[int] = None) -> None:
    """La fila del banco queda como venta (o devolución) de esta factura."""
    account_type, category = _TARGET[o.kind]
    if (t.account_type, t.category) != (account_type, category):
        before = snapshot(t)
        t.account_type, t.category, t.source, t.matched_rule_id = account_type, category, source, None
        t.confidence, t.needs_review = 1.0, False
        session.add(t)
        record_change(session, t, before, via=VIA, user_id=user_id)
    o.bank_tx_id = t.id
    session.add(o)
    inv = session.get(Invoice, o.invoice_id)
    if o.kind == "payment" and inv is not None and inv.transaction_id is None:
        inv.transaction_id = t.id                    # "en contabilidad": la venta es esta fila del banco
        session.add(inv)


def match(session: Session, company_id: int) -> dict:
    """Empareja lo que no tiene duda; cuenta lo que queda para el bookkeeper. Sin commit."""
    taken = _taken(session, company_id)
    out = {"auto": 0, "proposals": 0, "waiting": 0}
    for o in session.exec(select(InvoiceOffline).where(InvoiceOffline.company_id == company_id)
                          .where(InvoiceOffline.status == "pending").order_by(InvoiceOffline.id)).all():
        cands = candidates(session, o, taken)
        if len(cands) == 1:
            _book(session, o, cands[0], source="invoice")
            o.status = "auto"
            taken.add(cands[0].id)
            out["auto"] += 1
        elif cands:
            out["proposals"] += 1
        else:
            out["waiting"] += 1
    return out


# ── Lo que hace el dueño ────────────────────────────────────────────────────────────────────────

class OfflineIn(BaseModel):
    method: str
    day: date
    note: str = ""
    amount: str = ""     # vacío = todo lo que falta (pago) o todo lo cobrado sin devolver (devolución); 5-nov: parciales


def _clean(payload: OfflineIn) -> tuple[str, date, str]:
    if payload.method not in METHODS:
        raise HTTPException(status_code=422, detail="bad_method")
    if payload.day > date.today() or payload.day < date.today() - timedelta(days=365):
        raise HTTPException(status_code=422, detail="bad_day")
    return payload.method, payload.day, payload.note.strip()[:500]


def _invoice(session: Session, company_id: int, invoice_id: int) -> Invoice:
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.company_id != company_id or not inv.paypal_invoice_id:
        raise HTTPException(status_code=404, detail="invoice not found")
    no_demo(inv)
    return inv


def _money(d: Optional[dict]) -> Decimal:
    return Decimal(str((d or {}).get("value") or "0"))


def _limit(kind: str, inv: Invoice, current: dict) -> Decimal:
    """Lo máximo que se puede registrar, según PayPal en este momento (5-nov): un pago, lo que falta (`due_amount`); una
    devolución, lo cobrado menos lo ya devuelto. Un borrador nunca se envió: falta todo."""
    if kind == "payment":
        return _money(current.get("due_amount")) if current.get("due_amount") else inv.amount
    return _money((current.get("payments") or {}).get("paid_amount")) - _money((current.get("refunds") or {}).get("refund_amount"))


def _record(session: Session, company: Company, inv: Invoice, kind: str, method: str, day: date, note: str,
            user: User, raw_amount: str = "") -> dict:
    _connect(session, company.id)
    try:
        current = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
    except paypal.PayPalError as e:
        _fail(e)
    top = _limit(kind, inv, current)
    try:
        amount = Decimal(raw_amount.strip().replace(",", "")) if raw_amount.strip() else top
    except InvalidOperation:
        raise HTTPException(status_code=422, detail="bad_amount")
    if amount <= 0 or amount > top or amount != amount.quantize(Decimal("0.01")):
        raise HTTPException(status_code=422, detail="bad_amount")
    path = "payments" if kind == "payment" else "refunds"
    body = {"method": method, ("payment_date" if kind == "payment" else "refund_date"): day.isoformat(),
            "amount": {"currency_code": inv.currency, "value": f"{amount:.2f}"},
            **({"note": note} if note and kind == "payment" else {})}
    try:
        res = paypal.api("POST", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}/{path}", json=body)
        state = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
    except paypal.PayPalError as e:
        _fail(e)
    session.add(InvoiceOffline(company_id=company.id, invoice_id=inv.id, kind=kind, method=method, amount=amount,
                               day=day, paypal_ref=res.get("payment_id") or res.get("refund_id"),
                               decided_by_user_id=user.id))
    _apply_state(session, company, inv, state)       # PAID / REFUNDED como dice PayPal (un pago EXTERNO no se asienta)
    if kind == "payment":
        inv.paid_at = inv.paid_at or _now()
    session.flush()
    match(session, company.id)
    session.commit()
    return _view(session, company.id)


@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/record-payment")
def record_payment(company_id: int, invoice_id: int, payload: OfflineIn, user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    """El dueño cobró esta factura fuera de PayPal (el monto completo): una enviada y sin pagar, o un BORRADOR recién
    creado — el cliente le pagó ahí mismo (✅ sandbox 3-nov: PayPal acepta el pago sobre el borrador y lo deja
    MARKED_AS_PAID sin mandarle nada al cliente)."""
    company = _owner_only(company_id, user, session)
    inv = _invoice(session, company_id, invoice_id)
    if inv.status not in OPEN and inv.status != "DRAFT":
        raise HTTPException(status_code=409, detail="invoice_not_open")
    method, day, note = _clean(payload)
    return _record(session, company, inv, "payment", method, day, note, user, payload.amount)


@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/record-refund")
def record_refund(company_id: int, invoice_id: int, payload: OfflineIn, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    """El dueño le devolvió el dinero fuera de PayPal: el total o una parte (5-nov), hasta lo cobrado sin devolver."""
    company = _owner_only(company_id, user, session)
    inv = _invoice(session, company_id, invoice_id)
    if inv.status not in PAID_ANY + ("PARTIALLY_PAID", "PARTIALLY_REFUNDED"):
        raise HTTPException(status_code=409, detail="invoice_not_paid")
    method, day, note = _clean(payload)
    return _record(session, company, inv, "refund", method, day, note, user, payload.amount)


# ── Lo que confirma el bookkeeper (consola) ─────────────────────────────────────────────────────

def _brief(t: Transaction) -> dict:
    return {"id": t.id, "date": t.date.isoformat(), "description": t.description, "amount": str(t.amount),
            "bank_account": t.bank_account, "category": t.category}


def offline_view(session: Session, company_id: int) -> dict:
    taken = _taken(session, company_id)
    invs = {i.id: i for i in session.exec(select(Invoice).where(Invoice.company_id == company_id)).all()}
    custs = {c.id: c.name for c in session.exec(select(InvoiceCustomer).where(InvoiceCustomer.company_id == company_id)).all()}
    out = {"proposals": [], "matched": [], "waiting": []}
    for o in session.exec(select(InvoiceOffline).where(InvoiceOffline.company_id == company_id)
                          .order_by(InvoiceOffline.day)).all():
        inv = invs.get(o.invoice_id)
        head = {"id": o.id, "kind": o.kind, "method": o.method, "day": o.day.isoformat(), "amount": str(o.amount),
                "invoice": {"number": inv.number if inv else None,
                            "customer": custs.get(inv.customer_id) if inv and inv.customer_id else None}}
        if o.bank_tx_id:
            t = session.get(Transaction, o.bank_tx_id)
            out["matched"].append({**head, "status": o.status, "bank": _brief(t) if t else None})
        elif o.status == "pending":
            cands = candidates(session, o, taken)
            (out["proposals"] if cands else out["waiting"]).append({**head, "candidates": [_brief(c) for c in cands]})
    return {**out, "window": {"before": BEFORE, "after": AFTER}}


@router.get("/api/companies/{company_id}/invoices/offline")
def get_offline(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    console_company(company_id, user, session)
    return offline_view(session, company_id)


@router.post("/api/companies/{company_id}/invoices/offline/match")
def rematch_offline(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Vuelve a buscar en el banco (p. ej. después de importar el extracto)."""
    owned_company(company_id, user, session, write=True)
    out = match(session, company_id)
    session.commit()
    return {**out, **offline_view(session, company_id)}


class ConfirmIn(BaseModel):
    bank_tx_id: int


@router.post("/api/companies/{company_id}/invoices/offline/{offline_id}/confirm")
def confirm_offline(company_id: int, offline_id: int, payload: ConfirmIn, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    """El bookkeeper elige la fila del banco. Queda como decisión SUYA (`source="manual"`: ninguna regla la toca)."""
    owned_company(company_id, user, session, write=True)
    o = session.get(InvoiceOffline, offline_id)
    if o is None or o.company_id != company_id:
        raise HTTPException(status_code=404, detail="not found")
    if o.status != "pending":
        raise HTTPException(status_code=409, detail="already decided")
    t = next((c for c in candidates(session, o, _taken(session, company_id)) if c.id == payload.bank_tx_id), None)
    if t is None:
        raise HTTPException(status_code=422, detail="not a candidate")
    _book(session, o, t, source="manual", user_id=user.id)
    o.status, o.decided_by_user_id = "confirmed", user.id
    session.commit()
    return offline_view(session, company_id)


@router.post("/api/companies/{company_id}/invoices/offline/{offline_id}/reject")
def reject_offline(company_id: int, offline_id: int, user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    """Ninguna fila del banco es esta (p. ej. el efectivo se gastó sin depositarse): deja de proponerse."""
    owned_company(company_id, user, session, write=True)
    o = session.get(InvoiceOffline, offline_id)
    if o is None or o.company_id != company_id:
        raise HTTPException(status_code=404, detail="not found")
    if o.status != "pending":
        raise HTTPException(status_code=409, detail="already decided")
    o.status, o.decided_by_user_id = "rejected", user.id
    session.add(o)
    session.commit()
    return offline_view(session, company_id)
