"""
COBROS: LAS FACTURAS DEL DUEÑO (FASE 16 Paso 5, área PayPal / portal del dueño · 1-nov-2026).

El dueño crea una factura (un cliente de su lista guardada, concepto, monto y fecha límite), la revisa y la envía con
la Invoicing API v2 de PayPal (✅ esquema oficial `invoicing_v2.json`). El cliente la paga con tarjeta o PayPal en el
enlace de PayPal (`metadata.recipient_view_url`). Cuando se paga, la venta entra SOLA al libro, ligada a su factura.

Decisiones (Jose, 31-oct / 1-nov):
  · SOLO el dueño crea y envía facturas: el bookkeeper las ve (vista previa) pero no cobra a nombre del dueño.
  · Nada se envía sin la confirmación del dueño: crear deja un BORRADOR; enviar es otro paso.
  · En local, ContaBu se entera del pago CONSULTANDO a PayPal (`refresh`, al abrir Cobros y con "Actualizar");
    publicado, por webhook (deuda #37). El mismo `register_payment` asienta en los dos casos.
  · El código calcula y valida el total (una línea: monto × 1) y "vencida" (PayPal no tiene ese estado).
La venta entra con el `payment_id` de PayPal, que es el `transaction_id` de Transaction Search: la sincronización la
reconoce y no la duplica, y trae su comisión aparte (Merchant fees).
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlmodel import Session, select

import demo
import paypal
from auth import client_writable_company, current_user, effective_role, owned_company
from db import get_session
from models import AccountType, Company, Invoice, InvoiceCustomer, PayPalConnection, PayPalEntry, User
from rules import load_rules

router = APIRouter()

OPEN = ("SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED")   # enviadas y sin cobrar del todo
# Un correo que PayPal acepte: solo caracteres de correo (1-nov: uno copiado con "…" pasó y PayPal lo rechazó al facturar).
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
MAX_AMOUNT = Decimal("100000")


def _owner_only(company_id: int, user: User, session: Session) -> Company:
    """Crear o enviar una factura: SOLO el dueño del negocio (rol CLIENT). El bookkeeper, ni en la vista previa.
    La excepción es `paypal.contabu.com` (`demo.acts_as_owner`)."""
    company = owned_company(company_id, user, session)
    if not demo.acts_as_owner(effective_role(company, user, session)):
        raise HTTPException(status_code=403, detail="owner only")
    return company


def _fail(e: paypal.PayPalError):
    raise HTTPException(status_code=502, detail={"code": "paypal_" + e.code, "field": getattr(e, "field", None)})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect(session: Session, company_id: int) -> PayPalConnection:
    """Las llamadas a PayPal de esta petición usan las llaves de ESTE libro. Sin conexión, ninguna: 409."""
    conn = session.exec(select(PayPalConnection).where(PayPalConnection.company_id == company_id)).first()
    if conn is None:
        raise HTTPException(status_code=409, detail={"code": "paypal_not_connected"})
    try:
        paypal.use_connection(conn)
    except paypal.PayPalError as e:
        _fail(e)
    return conn


# ── Clientes ────────────────────────────────────────────────────────────────────────────────────

class CustomerIn(BaseModel):
    name: str
    email: str


def _customer_out(c: InvoiceCustomer) -> dict:
    return {"id": c.id, "name": c.name, "email": c.email}


@router.get("/api/owner/companies/{company_id}/customers")
def list_customers(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    owned_company(company_id, user, session)
    rows = session.exec(select(InvoiceCustomer).where(InvoiceCustomer.company_id == company_id)
                        .order_by(InvoiceCustomer.name)).all()
    return [_customer_out(c) for c in rows]


@router.post("/api/owner/companies/{company_id}/customers")
def add_customer(company_id: int, payload: CustomerIn, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    _owner_only(company_id, user, session)
    name, email = payload.name.strip(), payload.email.strip().lower()
    if not name or not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="name_and_email_required")
    c = InvoiceCustomer(company_id=company_id, name=name[:120], email=email[:200])
    session.add(c)
    session.commit()
    session.refresh(c)
    return _customer_out(c)


# ── Facturas ────────────────────────────────────────────────────────────────────────────────────

class InvoiceIn(BaseModel):
    customer_id: int
    concept: str
    amount: str          # texto: el código lo valida (Decimal, 2 decimales), nunca un float
    due_date: date


def _out(inv: Invoice, customers: dict[int, InvoiceCustomer], today: date) -> dict:
    c = customers.get(inv.customer_id or 0)
    return {"id": inv.id, "number": inv.number, "status": inv.status,
            "overdue": inv.status in OPEN and inv.due_date < today,
            "customer": _customer_out(c) if c else None, "concept": inv.concept, "amount": str(inv.amount),
            "currency": inv.currency, "due_date": inv.due_date.isoformat(), "recipient_view_url": inv.recipient_view_url,
            "sent_at": inv.sent_at.isoformat() if inv.sent_at else None,
            "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
            "in_books": inv.transaction_id is not None}


def _view(session: Session, company_id: int) -> dict:
    """La lista y sus cifras. Las cifras salen de SQL (regla 1): por cobrar, vencidas y cobrado en el año."""
    today = date.today()
    invs = session.exec(select(Invoice).where(Invoice.company_id == company_id).order_by(Invoice.id.desc())).all()
    customers = {c.id: c for c in session.exec(select(InvoiceCustomer).where(InvoiceCustomer.company_id == company_id)).all()}
    open_, overdue, paid_year = session.exec(
        select(func.coalesce(func.sum(case((Invoice.status.in_(OPEN), Invoice.amount), else_=0)), 0),
               func.count(case(((Invoice.status.in_(OPEN)) & (Invoice.due_date < today), 1))),
               func.coalesce(func.sum(case(((Invoice.status == "PAID") & (func.extract("year", Invoice.paid_at) == today.year),
                                            Invoice.amount), else_=0)), 0))
        .where(Invoice.company_id == company_id)
    ).one()
    conn = session.exec(select(PayPalConnection).where(PayPalConnection.company_id == company_id)).first()
    return {"invoices": [_out(i, customers, today) for i in invs],
            "summary": {"outstanding": str(open_), "overdue": int(overdue), "collected_this_year": str(paid_year)},
            "paypal_connected": conn is not None}


@router.get("/api/owner/companies/{company_id}/invoices")
def list_invoices(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    owned_company(company_id, user, session)
    return _view(session, company_id)


def _validated(payload: InvoiceIn) -> tuple[str, Decimal]:
    concept = payload.concept.strip()
    try:
        amount = Decimal(payload.amount.strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise HTTPException(status_code=422, detail="bad_amount")
    if not concept or len(concept) > 200:
        raise HTTPException(status_code=422, detail="bad_concept")
    if amount <= 0 or amount > MAX_AMOUNT or amount != amount.quantize(Decimal("0.01")):
        raise HTTPException(status_code=422, detail="bad_amount")
    if payload.due_date < date.today():
        raise HTTPException(status_code=422, detail="bad_due_date")
    return concept, amount


def _name_parts(full: str) -> dict:
    parts = full.split(maxsplit=1)
    return {"given_name": parts[0], **({"surname": parts[1]} if len(parts) > 1 else {})}


@router.post("/api/owner/companies/{company_id}/invoices")
def create_invoice(company_id: int, payload: InvoiceIn, user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    """Crea la factura como BORRADOR en PayPal (no se envía nada todavía). Requiere PayPal conectado."""
    company = _owner_only(company_id, user, session)
    _connect(session, company_id)
    c = session.get(InvoiceCustomer, payload.customer_id)
    if c is None or c.company_id != company_id:
        raise HTTPException(status_code=404, detail="customer not found")
    concept, amount = _validated(payload)
    body = {
        "detail": {"currency_code": "USD", "invoice_date": date.today().isoformat(),
                   "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": payload.due_date.isoformat()}},
        "invoicer": {"business_name": company.name[:300]},
        "primary_recipients": [{"billing_info": {"name": _name_parts(c.name), "email_address": c.email}}],
        "items": [{"name": concept[:200], "quantity": "1", "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}],
    }
    try:
        res = paypal.api("POST", "/v2/invoicing/invoices", json=body, headers={"Prefer": "return=representation"})
    except paypal.PayPalError as e:
        _fail(e)
    # El código calculó el total; PayPal debe decir lo mismo (si no, no se guarda una factura que no cuadra).
    total = Decimal(str((res.get("amount") or {}).get("value") or amount))
    if total != amount:
        raise HTTPException(status_code=502, detail={"code": "paypal_total_mismatch"})
    inv = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id=res.get("id"),
                  number=(res.get("detail") or {}).get("invoice_number"), concept=concept, amount=amount,
                  due_date=payload.due_date, status=res.get("status") or "DRAFT", created_by_user_id=user.id)
    session.add(inv)
    session.commit()
    return _view(session, company_id)


@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/send")
def send_invoice(company_id: int, invoice_id: int, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    """Envía el borrador (el dueño ya lo revisó y confirmó en pantalla). Guarda el enlace de pago del cliente."""
    _owner_only(company_id, user, session)
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.company_id != company_id:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv.status != "DRAFT":
        raise HTTPException(status_code=409, detail="not a draft")
    _connect(session, company_id)
    try:
        paypal.api("POST", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}/send",
                   json={"send_to_recipient": True, "send_to_invoicer": False})
        res = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
    except paypal.PayPalError as e:
        _fail(e)
    inv.status = res.get("status") or "SENT"
    inv.recipient_view_url = ((res.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")
    inv.sent_at = _now()
    session.add(inv)
    session.commit()
    return _view(session, company_id)


def register_payment(session: Session, company: Company, inv: Invoice, invoice_json: dict) -> bool:
    """Asienta en el libro lo que se cobró por PayPal en esta factura (lo usan `refresh` y, publicado, el webhook).
    Solo pagos `type == "PAYPAL"` con su `payment_id` (un pago marcado a mano, en efectivo, no movió PayPal). Si
    Transaction Search ya trajo ese pago, solo se liga. Sin commit. Devuelve si asentó algo nuevo."""
    customers = {inv.customer_id: session.get(InvoiceCustomer, inv.customer_id)} if inv.customer_id else {}
    who = (customers.get(inv.customer_id).name if customers.get(inv.customer_id) else "CUSTOMER").upper()
    rules = load_rules(session, company.id, company.sector or "")
    added = False
    for p in ((invoice_json.get("payments") or {}).get("transactions") or []):
        pid = p.get("payment_id")
        if p.get("type") != "PAYPAL" or not pid:
            continue
        entry = session.exec(select(PayPalEntry).where(PayPalEntry.company_id == company.id)
                             .where(PayPalEntry.paypal_id == pid)).first()
        if entry is None:
            amount = Decimal(str((p.get("amount") or {}).get("value") or inv.amount))
            tx = paypal.insert_row(session, company, {
                "paypal_id": pid, "date": date.fromisoformat((p.get("payment_date") or date.today().isoformat())[:10]),
                "description": f"PAYPAL INVOICE {inv.number or inv.id} FROM {who}", "signed": amount,
                "event_code": "INVOICE", "preset": (AccountType.REVENUE, paypal.SALE)}, rules)
            added = True
        else:
            tx = None
        inv.payment_id = pid
        inv.transaction_id = (tx.id if tx else entry.transaction_id) if (tx or entry) else inv.transaction_id
    return added


@router.post("/api/owner/companies/{company_id}/invoices/refresh")
def refresh_invoices(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Pregunta a PayPal el estado de las facturas abiertas y asienta lo cobrado (en local, en lugar del webhook:
    Jose, 1-nov). La puede pedir el dueño o el bookkeeper; un VIEWER no."""
    company = client_writable_company(company_id, user, session)
    invs = session.exec(select(Invoice).where(Invoice.company_id == company_id).where(Invoice.status.in_(OPEN))).all()
    if invs:
        _connect(session, company_id)
    changed = 0
    for inv in invs:
        try:
            res = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
        except paypal.PayPalError as e:
            _fail(e)
        changed += _apply_state(session, company, inv, res)
    session.commit()
    return {**_view(session, company_id), "changed": changed}


def _apply_state(session: Session, company: Company, inv: Invoice, res: dict) -> bool:
    """Deja la factura como dice PayPal y, si está PAGADA del todo, asienta el cobro. Sin commit. ¿Cambió el estado?"""
    status = res.get("status") or inv.status
    changed = status != inv.status
    inv.status, inv.last_checked_at = status, _now()
    if status == "PAID":
        register_payment(session, company, inv, res)
        inv.paid_at = inv.paid_at or _now()
    session.add(inv)
    return changed


@router.post("/api/paypal/webhook/{connection_id}")
def paypal_webhook(connection_id: int, event: dict, request: Request, session: Session = Depends(get_session)):
    """El aviso de PayPal (PÚBLICO: lo llama PayPal; se verifica su firma con PayPal, deuda #37). No se le cree al
    aviso: `INVOICING.INVOICE.PAID` también salta con pagos parciales o pendientes, así que el estado y los montos se
    leen de la factura. Repetido no duplica (`register_payment`). Error de PayPal → 502, y PayPal reintenta."""
    conn = session.get(PayPalConnection, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="connection not found")
    try:
        if not paypal.webhook_is_genuine(conn, request.headers, event):
            raise HTTPException(status_code=400, detail="bad signature")
        resource = event.get("resource") or {}
        invoice_id = (resource.get("invoice") or {}).get("id") or resource.get("id")
        inv = session.exec(select(Invoice).where(Invoice.company_id == conn.company_id)
                           .where(Invoice.paypal_invoice_id == invoice_id)).first() if invoice_id else None
        if inv is None or not str(event.get("event_type") or "").startswith("INVOICING.INVOICE."):
            return {"ok": True, "ignored": True}
        res = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
    except paypal.PayPalError as e:
        _fail(e)
    _apply_state(session, session.get(Company, conn.company_id), inv, res)
    session.commit()
    return {"ok": True}
