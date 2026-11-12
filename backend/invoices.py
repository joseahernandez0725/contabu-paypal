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

import json
import os
import re
import secrets
import struct
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlmodel import Session, select

import demo
import paypal
from auth import client_writable_company, current_user, effective_role, owned_company
from db import get_session
from models import (AccountType, Company, Invoice, InvoiceCustomer, InvoiceOffline, InvoiceProfile, InvoiceSeries,
                    PayPalConnection, PayPalEntry, Transaction, User)
from rules import load_rules

router = APIRouter()

OPEN = ("SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED")   # enviadas y sin cobrar del todo
# Las facturas del libro sembrado (`demo_paypal.py`, ids "INV2-DEMO-…") no existen en PayPal (12-nov): "Actualizar estado"
# las salta y cobrar, recordar, cancelar o registrar un pago sobre ellas responde 409 `demo_invoice`, no un error de PayPal.
DEMO_PREFIX = "INV2-DEMO-"


def is_demo(inv: Invoice) -> bool:
    return (inv.paypal_invoice_id or "").startswith(DEMO_PREFIX)


def no_demo(inv: Invoice) -> None:
    if is_demo(inv):
        raise HTTPException(status_code=409, detail="demo_invoice")
REFUNDED_ANY = ("REFUNDED", "MARKED_AS_REFUNDED", "PARTIALLY_REFUNDED", "REFUNDED_EXTERNAL")
REFUND_CODES = ("T1107", "T1106", "T1201")   # reembolso, reversión, contracargo (`paypal.rows_for`)
PAID_ANY = ("PAID", "MARKED_AS_PAID", "PAID_EXTERNAL")   # ✅ especificación: cobrada por PayPal o marcada como cobrada fuera
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
    number: str = ""     # número propio (4-nov); vacío = el que sigue en PayPal. PayPal rechaza uno repetido
    reference: str = ""  # orden de compra (PO) del cliente, si la tiene


# ── Los datos del negocio en sus facturas (4-nov) ───────────────────────────────────────────────

NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/#-]{0,24}$")      # ✅ especificación: `invoice_number` ≤ 25


class ProfileIn(BaseModel):
    address_line: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    phone: str = ""
    website: str = ""
    tax_id: str = ""
    note: str = ""
    terms: str = ""


def _profile_out(p: Optional[InvoiceProfile]) -> dict:
    keys = ("address_line", "city", "state", "postal_code", "phone", "website", "tax_id", "note", "terms")
    return {**{k: (getattr(p, k) or "") if p else "" for k in keys},
            "logo_path": f"/api/public/invoice-logo/{p.logo_key}" if p and p.logo_key else None,
            "logo_on_paypal": bool(p and p.logo_key and _public_base())}


def _clean_profile(x: ProfileIn) -> dict:
    """Valida con los límites de PayPal; un error dice qué campo (422 `bad_<campo>`)."""
    v = {k: (getattr(x, k) or "").strip() for k in ProfileIn.model_fields}
    v["state"] = v["state"].upper()
    v["phone"] = re.sub(r"\D", "", v["phone"])
    if v["phone"].startswith("1") and len(v["phone"]) == 11:
        v["phone"] = v["phone"][1:]                                      # el +1 va aparte (`country_code`)
    checks = {"address_line": len(v["address_line"]) <= 100, "city": len(v["city"]) <= 60,
              "state": v["state"] == "" or re.fullmatch(r"[A-Z]{2}", v["state"]) is not None,
              "postal_code": v["postal_code"] == "" or re.fullmatch(r"\d{5}(-\d{4})?", v["postal_code"]) is not None,
              "phone": v["phone"] == "" or 7 <= len(v["phone"]) <= 14,
              "website": v["website"] == "" or (re.match(r"^https?://\S+\.\S+$", v["website"]) is not None and len(v["website"]) <= 300),
              "tax_id": len(v["tax_id"]) <= 100, "note": len(v["note"]) <= 1000, "terms": len(v["terms"]) <= 1000}
    bad = next((k for k, ok in checks.items() if not ok), None)
    if bad:
        raise HTTPException(status_code=422, detail=f"bad_{bad}")
    return v


@router.get("/api/owner/companies/{company_id}/invoice-profile")
def get_profile(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    owned_company(company_id, user, session)
    return _profile_out(session.exec(select(InvoiceProfile).where(InvoiceProfile.company_id == company_id)).first())


@router.put("/api/owner/companies/{company_id}/invoice-profile")
def put_profile(company_id: int, payload: ProfileIn, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Solo el dueño. Sale en las facturas NUEVAS (las enviadas no cambian)."""
    _owner_only(company_id, user, session)
    v = _clean_profile(payload)
    p = session.exec(select(InvoiceProfile).where(InvoiceProfile.company_id == company_id)).first() \
        or InvoiceProfile(company_id=company_id)
    for k, val in v.items():
        setattr(p, k, val or None)
    p.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(p)
    session.commit()
    return _profile_out(p)


# ── El logo (4-nov) ─────────────────────────────────────────────────────────────────────────────
# PayPal no recibe la imagen: pide la URL https de una ya publicada, de 250 × 90 como máximo (✅ especificación,
# `invoicer.logo_url`). El navegador la ajusta a ese tamaño y la manda en PNG; aquí solo se comprueba. La API la sirve
# en una ruta PÚBLICA con una clave al azar (nueva en cada subida). Solo se le pasa a PayPal si la API es pública
# (`PAYPAL_WEBHOOK_BASE_URL`, la dirección pública del backend publicado): en local sería `localhost` y PayPal no llega.

LOGO_MAX_W, LOGO_MAX_H, LOGO_MAX_BYTES = 250, 90, 200_000
_PNG = b"\x89PNG\r\n\x1a\n"


def _public_base() -> str:
    return (os.getenv("PAYPAL_WEBHOOK_BASE_URL") or "").strip().rstrip("/")


def png_size(data: bytes) -> Optional[tuple[int, int]]:
    """(ancho, alto) de un PNG, leídos de su cabecera IHDR; None si no es un PNG."""
    if len(data) < 24 or not data.startswith(_PNG) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


@router.post("/api/owner/companies/{company_id}/invoice-profile/logo")
async def upload_logo(company_id: int, file: Optional[UploadFile] = File(None), user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    """Solo el dueño. Un PNG de hasta 250 × 90 y 200 KB (la pantalla ya lo ajusta)."""
    _owner_only(company_id, user, session)
    data = await file.read(LOGO_MAX_BYTES + 1) if file is not None else b""
    size = png_size(data)
    if size is None or len(data) > LOGO_MAX_BYTES:
        raise HTTPException(status_code=422, detail="bad_logo")
    if size[0] > LOGO_MAX_W or size[1] > LOGO_MAX_H or 0 in size:
        raise HTTPException(status_code=422, detail="logo_too_big")
    p = session.exec(select(InvoiceProfile).where(InvoiceProfile.company_id == company_id)).first() \
        or InvoiceProfile(company_id=company_id)
    p.logo, p.logo_key = data, secrets.token_urlsafe(18)
    p.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(p)
    session.commit()
    return _profile_out(p)


@router.delete("/api/owner/companies/{company_id}/invoice-profile/logo")
def delete_logo(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    _owner_only(company_id, user, session)
    p = session.exec(select(InvoiceProfile).where(InvoiceProfile.company_id == company_id)).first()
    if p is not None:
        p.logo, p.logo_key = None, None
        session.add(p)
        session.commit()
    return _profile_out(p)


@router.get("/api/public/invoice-logo/{key}")
def public_logo(key: str, session: Session = Depends(get_session)):
    """PÚBLICA a propósito: la pide PayPal al mostrar la factura. Solo con la clave al azar; nada más se expone."""
    p = session.exec(select(InvoiceProfile).where(InvoiceProfile.logo_key == key)).first() if len(key) >= 20 else None
    if p is None or not p.logo:
        raise HTTPException(status_code=404, detail="not found")
    return Response(content=p.logo, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/owner/companies/{company_id}/invoices/next-number")
def next_number(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El número que sigue en PayPal (✅ sandbox: respeta el formato del último, p. ej. RH-2026-0101). No reserva nada."""
    _owner_only(company_id, user, session)
    _connect(session, company_id)
    try:
        return {"number": paypal.api("POST", "/v2/invoicing/generate-next-invoice-number", json={}).get("invoice_number")}
    except paypal.PayPalError as e:
        _fail(e)


def _invoicer(company: Company, p: Optional[InvoiceProfile]) -> dict:
    """El bloque `invoicer` de PayPal con los datos que el dueño haya puesto (solo los que tiene)."""
    out: dict = {"business_name": company.name[:300]}
    if p is None:
        return out
    if p.logo_key and _public_base():
        out["logo_url"] = f"{_public_base()}/api/public/invoice-logo/{p.logo_key}"
    addr = {k: val for k, val in (("address_line_1", p.address_line), ("admin_area_2", p.city), ("admin_area_1", p.state),
                                  ("postal_code", p.postal_code)) if val}
    if addr:
        out["address"] = {**addr, "country_code": "US"}
    if p.phone:
        out["phones"] = [{"country_code": "1", "national_number": p.phone, "phone_type": "MOBILE"}]
    if p.website:
        out["website"] = p.website
    if p.tax_id:
        out["tax_id"] = p.tax_id
    return out


def _out(inv: Invoice, customers: dict[int, InvoiceCustomer], today: date, offline: Optional[InvoiceOffline] = None,
         refunds_in_books: frozenset = frozenset(), paid_so_far: Decimal = Decimal(0)) -> dict:
    c = customers.get(inv.customer_id or 0)
    # Devuelta: ¿ya está la devolución en el libro? Por PayPal, si la sincronización trajo un reembolso, reversión o
    # contracargo de ESTE cobro (`reference_id`); fuera de PayPal, si su fila del banco ya se emparejó (3-nov).
    refund_done = (inv.status in REFUNDED_ANY and (bool(inv.payment_id and inv.payment_id in refunds_in_books)
                   or bool(offline and offline.kind == "refund" and offline.bank_tx_id)))
    return {"id": inv.id, "number": inv.number, "status": inv.status,
            "overdue": inv.status in OPEN and inv.due_date < today,
            "customer": _customer_out(c) if c else None, "concept": inv.concept, "amount": str(inv.amount),
            # Abierta con pagos parciales (9-nov): lo ya cobrado; lo que falta es `amount - paid_so_far`.
            "paid_so_far": str(paid_so_far) if inv.status in OPEN else "0",
            "currency": inv.currency, "due_date": inv.due_date.isoformat(), "recipient_view_url": inv.recipient_view_url,
            "sent_at": inv.sent_at.isoformat() if inv.sent_at else None,
            "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
            "in_books": inv.transaction_id is not None,
            "refund_in_books": refund_done if inv.status in REFUNDED_ANY else None,
            "reminder_count": inv.reminder_count or 0,
            "last_reminder_at": inv.last_reminder_at.isoformat() + "Z" if inv.last_reminder_at else None,
            "series_id": inv.series_id,
            "auto_reminders_stopped": inv.auto_reminders_stopped_at is not None,
            # Pagada (o devuelta) FUERA de PayPal (3-nov, `invoice_offline.py`): el último registro y si ya está en el banco.
            "offline": {"kind": offline.kind, "method": offline.method, "day": offline.day.isoformat(),
                        "in_bank": offline.bank_tx_id is not None} if offline else None}


# 9-nov (revisión externa): un pago PARCIAL por PayPal entra al libro cuando llega, por su monto, y queda ligado a su
# factura con una marca `PayPalEntry` "<payment_id>:inv" (sin migración: la rama del concurso no agrega columnas).
INVOICE_PAID_MARK = "INVOICE_PAID"


def paid_so_far_subquery(company_id: int):
    """Por factura, lo ya cobrado en pagos parciales: por PayPal (las marcas) y fuera de PayPal (`InvoiceOffline`). Solo
    se usa con facturas ABIERTAS: de una pagada del todo cuenta el monto completo. Columnas: invoice_id, paid."""
    pp = (select(Invoice.id.label("invoice_id"), Transaction.amount.label("paid"))
          .select_from(PayPalEntry)
          .join(Invoice, (Invoice.paypal_invoice_id == PayPalEntry.reference_id) & (Invoice.company_id == PayPalEntry.company_id))
          .join(Transaction, Transaction.id == PayPalEntry.transaction_id)
          .where(PayPalEntry.company_id == company_id).where(PayPalEntry.event_code == INVOICE_PAID_MARK))
    off = (select(InvoiceOffline.invoice_id.label("invoice_id"), InvoiceOffline.amount.label("paid"))
           .where(InvoiceOffline.company_id == company_id).where(InvoiceOffline.kind == "payment"))
    both = pp.union_all(off).subquery()
    return select(both.c.invoice_id, func.sum(both.c.paid).label("paid")).group_by(both.c.invoice_id).subquery()


def payer_history(session: Session, company_id: int) -> dict[int, dict]:
    """Cómo PAGA cada cliente (5-nov; la idea de `invoice-chase`): de sus facturas cobradas, cuántas después del
    vencimiento, por SQL. El CÓDIGO elige el tono del recordatorio con esto; la IA solo recibe el tono, nunca el historial.
    on_time = nunca tarde · sometimes_late = hasta la mitad tarde · often_late = más de la mitad (→ tono firme) · new."""
    late = (func.date(Invoice.paid_at) > Invoice.due_date)
    rows = session.exec(
        select(Invoice.customer_id, func.count(Invoice.id), func.coalesce(func.sum(case((late, 1), else_=0)), 0))
        .where(Invoice.company_id == company_id).where(Invoice.status.in_(PAID_ANY)).where(Invoice.paid_at.is_not(None))
        .where(Invoice.customer_id.is_not(None)).group_by(Invoice.customer_id)
    ).all()
    out = {}
    for cid, paid, n_late in rows:
        kind = "on_time" if n_late == 0 else "sometimes_late" if n_late * 2 <= paid else "often_late"
        out[cid] = {"paid": int(paid), "late": int(n_late), "kind": kind,
                    "tone": "firm" if kind == "often_late" else "friendly"}
    return out


def _view(session: Session, company_id: int) -> dict:
    """La lista y sus cifras. Las cifras salen de SQL (regla 1): por cobrar, vencidas y cobrado en el año."""
    today = date.today()
    invs = session.exec(select(Invoice).where(Invoice.company_id == company_id).order_by(Invoice.id.desc())).all()
    customers = {c.id: c for c in session.exec(select(InvoiceCustomer).where(InvoiceCustomer.company_id == company_id)).all()}
    counted = PAID_ANY + ("PARTIALLY_REFUNDED",)      # cobradas que siguen contando (menos lo devuelto, abajo)
    open_, overdue, paid_year = session.exec(
        select(func.coalesce(func.sum(case((Invoice.status.in_(OPEN), Invoice.amount), else_=0)), 0),
               func.count(case(((Invoice.status.in_(OPEN)) & (Invoice.due_date < today), 1))),
               func.coalesce(func.sum(case(((Invoice.status.in_(counted)) & (func.extract("year", Invoice.paid_at) == today.year),
                                            Invoice.amount), else_=0)), 0))
        .where(Invoice.company_id == company_id)
    ).one()
    # Parciales FUERA de PayPal (5-nov), también por SQL: lo cobrado de una factura todavía abierta baja "por cobrar" y
    # sube "cobrado"; lo devuelto de una que sigue contando baja "cobrado" (una DEVUELTA del todo ya no cuenta: no se resta 2 veces).
    part_paid, part_paid_year, part_refunded_year = session.exec(
        select(func.coalesce(func.sum(case(((InvoiceOffline.kind == "payment") & Invoice.status.in_(OPEN), InvoiceOffline.amount), else_=0)), 0),
               func.coalesce(func.sum(case(((InvoiceOffline.kind == "payment") & Invoice.status.in_(OPEN)
                                            & (func.extract("year", InvoiceOffline.day) == today.year), InvoiceOffline.amount), else_=0)), 0),
               func.coalesce(func.sum(case(((InvoiceOffline.kind == "refund") & Invoice.status.in_(counted)
                                            & (func.extract("year", InvoiceOffline.day) == today.year), InvoiceOffline.amount), else_=0)), 0))
        .select_from(InvoiceOffline).join(Invoice, Invoice.id == InvoiceOffline.invoice_id)
        .where(InvoiceOffline.company_id == company_id)
    ).one()
    # Parciales POR PAYPAL (9-nov): ya están en el libro; bajan "por cobrar" y suben "cobrado" igual que los de fuera.
    pp_paid, pp_paid_year = session.exec(
        select(func.coalesce(func.sum(Transaction.amount), 0),
               func.coalesce(func.sum(case((func.extract("year", Transaction.date) == today.year, Transaction.amount), else_=0)), 0))
        .select_from(PayPalEntry)
        .join(Invoice, (Invoice.paypal_invoice_id == PayPalEntry.reference_id) & (Invoice.company_id == PayPalEntry.company_id))
        .join(Transaction, Transaction.id == PayPalEntry.transaction_id)
        .where(PayPalEntry.company_id == company_id).where(PayPalEntry.event_code == INVOICE_PAID_MARK)
        .where(Invoice.status.in_(OPEN))
    ).one()
    open_, paid_year = open_ - part_paid - pp_paid, paid_year + part_paid_year - part_refunded_year + pp_paid_year
    sofar = paid_so_far_subquery(company_id)
    paid_by_inv = {iid: Decimal(str(p)) for iid, p in session.exec(select(sofar.c.invoice_id, sofar.c.paid)).all()}
    conn = session.exec(select(PayPalConnection).where(PayPalConnection.company_id == company_id)).first()
    offline: dict[int, InvoiceOffline] = {}
    for o in session.exec(select(InvoiceOffline).where(InvoiceOffline.company_id == company_id).order_by(InvoiceOffline.id)).all():
        offline[o.invoice_id] = o                      # el último registro de cada factura manda
    refs = frozenset(session.exec(select(PayPalEntry.reference_id).where(PayPalEntry.company_id == company_id)
                                  .where(PayPalEntry.event_code.in_(REFUND_CODES))
                                  .where(PayPalEntry.reference_id.is_not(None))).all())
    history = payer_history(session, company_id)
    new = {"paid": 0, "late": 0, "kind": "new", "tone": "friendly"}
    return {"invoices": [{**_out(i, customers, today, offline.get(i.id), refs, paid_by_inv.get(i.id, Decimal(0))),
                          "payer": history.get(i.customer_id, new) if i.customer_id else None} for i in invs],
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
    number, reference = payload.number.strip(), payload.reference.strip()
    if number and not NUMBER_RE.match(number):
        raise HTTPException(status_code=422, detail="bad_number")
    if len(reference) > 120:
        raise HTTPException(status_code=422, detail="bad_reference")
    prof = session.exec(select(InvoiceProfile).where(InvoiceProfile.company_id == company_id)).first()
    detail = {"currency_code": "USD", "invoice_date": date.today().isoformat(),
              "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": payload.due_date.isoformat()},
              **({"invoice_number": number} if number else {}), **({"reference": reference} if reference else {}),
              **({"note": prof.note} if prof and prof.note else {}),
              **({"terms_and_conditions": prof.terms} if prof and prof.terms else {})}
    body = {
        "detail": detail,
        "invoicer": _invoicer(company, prof),
        "primary_recipients": [{"billing_info": {"name": _name_parts(c.name), "email_address": c.email}}],
        "items": [{"name": concept[:200], "quantity": "1", "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}],
    }
    try:
        res = paypal.api("POST", "/v2/invoicing/invoices", json=body, headers={"Prefer": "return=representation"})
    except paypal.PayPalError as e:
        if number and getattr(e, "field", None) == "invoice_number":   # ✅ sandbox: un número repetido → 422 en ese campo
            raise HTTPException(status_code=409, detail="duplicate_number")
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


class SendIn(BaseModel):
    notify: bool = True      # False: cobro en persona con el QR (3-nov): PayPal no le manda el correo al cliente


@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/send")
def send_invoice(company_id: int, invoice_id: int, payload: Optional[SendIn] = None, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    """Envía el borrador (el dueño ya lo revisó y confirmó en pantalla). Guarda el enlace de pago del cliente."""
    notify = payload.notify if payload is not None else True
    _owner_only(company_id, user, session)
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.company_id != company_id:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv.status != "DRAFT":
        raise HTTPException(status_code=409, detail="not a draft")
    _connect(session, company_id)
    try:
        paypal.api("POST", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}/send",
                   json={"send_to_recipient": notify, "send_to_invoicer": False})
        res = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
    except paypal.PayPalError as e:
        _fail(e)
    inv.status = res.get("status") or "SENT"
    inv.recipient_view_url = ((res.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")
    inv.sent_at = _now()
    session.add(inv)
    session.commit()
    return _view(session, company_id)


# Cancelar una enviada y descartar un borrador (3-nov). En el libro no cambia nada: una factura sin cobrar no tiene filas
# (base caja); solo deja de contar en "por cobrar", que suma SQL. ✅ Especificación: `cancel` ignora el asunto y la nota
# que uno mande ("will not be honored"), así que solo se elige si PayPal avisa al cliente; `DELETE` solo borra borradores.

class CancelIn(BaseModel):
    notify: bool = True


@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/cancel")
def cancel_invoice(company_id: int, invoice_id: int, payload: CancelIn, user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    """Cancela una factura enviada y sin cobrar (con confirmación en pantalla). Con un pago parcial, no: 409."""
    company = _owner_only(company_id, user, session)
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.company_id != company_id or not inv.paypal_invoice_id:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv.status not in OPEN or inv.status == "PARTIALLY_PAID":
        raise HTTPException(status_code=409, detail="invoice_not_open")
    no_demo(inv)
    _connect(session, company_id)
    try:
        paypal.api("POST", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}/cancel",
                   json={"send_to_recipient": payload.notify, "send_to_invoicer": False})
        res = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
    except paypal.PayPalError as e:
        _fail(e)
    _apply_state(session, company, inv, res)
    session.commit()
    return _view(session, company_id)


@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/discard")
def discard_draft(company_id: int, invoice_id: int, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    """Descarta un BORRADOR (nunca se envió: nadie lo vio). Se borra en PayPal y aquí."""
    _owner_only(company_id, user, session)
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.company_id != company_id:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv.status != "DRAFT":
        raise HTTPException(status_code=409, detail="not a draft")
    if inv.paypal_invoice_id:
        _connect(session, company_id)
        try:
            paypal.api("DELETE", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
        except paypal.PayPalError as e:
            if e.code not in ("RESOURCE_NOT_FOUND", "http_404"):     # ya no estaba en PayPal: se borra igual aquí
                _fail(e)
    session.delete(inv)
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
        tx_id = tx.id if tx else (entry.transaction_id if entry else None)
        mark = f"{pid}:inv"
        if tx_id and not session.exec(select(PayPalEntry.id).where(PayPalEntry.company_id == company.id)
                                      .where(PayPalEntry.paypal_id == mark)).first():
            session.add(PayPalEntry(company_id=company.id, paypal_id=mark, event_code=INVOICE_PAID_MARK,
                                    transaction_id=tx_id, reference_id=inv.paypal_invoice_id))
        inv.payment_id = pid
        inv.transaction_id = (tx.id if tx else entry.transaction_id) if (tx or entry) else inv.transaction_id
    return added


@router.post("/api/owner/companies/{company_id}/invoices/refresh")
def refresh_invoices(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Pregunta a PayPal el estado de las facturas abiertas y asienta lo cobrado (en local, en lugar del webhook:
    Jose, 1-nov). La puede pedir el dueño o el bookkeeper; un VIEWER no."""
    company = client_writable_company(company_id, user, session)
    import invoice_series                      # aquí: `invoice_series` importa de este módulo
    if session.exec(select(InvoiceSeries.id).where(InvoiceSeries.company_id == company_id)
                    .where(InvoiceSeries.status.in_(invoice_series.LIVE))).first():
        _connect(session, company_id)
        try:
            invoice_series.sync(session, company_id)   # las facturas que generaron las series (4-nov)
        except paypal.PayPalError as e:
            _fail(e)
    invs = [i for i in session.exec(select(Invoice).where(Invoice.company_id == company_id).where(Invoice.status.in_(OPEN))).all()
            if not is_demo(i)]      # las sembradas no están en PayPal: se quedan como están
    if invs:
        _connect(session, company_id)
    changed = 0
    for inv in invs:
        try:
            res = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
        except paypal.PayPalError as e:
            _fail(e)
        changed += _apply_state(session, company, inv, res)
    import invoice_offline                     # aquí: `invoice_offline` importa de este módulo
    invoice_offline.match(session, company_id)   # lo cobrado fuera de PayPal: ¿ya llegó al banco? (3-nov)
    session.commit()
    return {**_view(session, company_id), "changed": changed}


def _apply_state(session: Session, company: Company, inv: Invoice, res: dict) -> bool:
    """Deja la factura como dice PayPal y asienta lo cobrado por PayPal: cada pago, parcial o final, por su monto (9-nov;
    antes un parcial esperaba a que la factura quedara pagada del todo). Sin commit. ¿Cambió el estado?"""
    status = res.get("status") or inv.status
    changed = status != inv.status
    inv.status, inv.last_checked_at = status, _now()
    if status in ("PAID", "PARTIALLY_PAID"):
        register_payment(session, company, inv, res)
    if status == "PAID":
        inv.paid_at = inv.paid_at or _now()
    session.add(inv)
    return changed


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/api/paypal/webhook/{connection_id}")
def paypal_webhook(connection_id: int, request: Request, raw: bytes = Depends(_raw_body),
                   session: Session = Depends(get_session)):
    """El aviso de PayPal (PÚBLICO: lo llama PayPal; se verifica su firma con PayPal, deuda #37). No se le cree al
    aviso: `INVOICING.INVOICE.PAID` también salta con pagos parciales o pendientes, así que el estado y los montos se
    leen de la factura. Repetido no duplica (`register_payment`). Error de PayPal → 502, y PayPal reintenta."""
    conn = session.get(PayPalConnection, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="connection not found")
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="bad body")
    try:
        if not paypal.webhook_is_genuine(conn, request.headers, raw):
            raise HTTPException(status_code=400, detail="bad signature")
        if str(event.get("event_type") or "").startswith("CUSTOMER.DISPUTE."):   # disputas (3-nov): se relee de PayPal
            import paypal_disputes                       # aquí: `paypal_disputes` importa de este módulo
            paypal_disputes.on_webhook(session, conn, event)
            return {"ok": True}
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
