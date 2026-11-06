"""
PAGOS A CONTRATISTAS CON PAYPAL PAYOUTS (FASE 16, área PayPal / portal del dueño · 4-nov-2026).

Detrás del interruptor `PAYPAL_PAYOUTS` (Jose, 3-nov: "en main, detrás de un interruptor"): apagado, la lista dice
`enabled: false` (la pestaña no aparece) y las escrituras responden 404.

El dueño le paga a un contratista (ayudante, subcontratista) desde su saldo de PayPal:
  1. Lo agrega una vez (nombre y el correo de su PayPal) y le paga: monto y concepto, con confirmación en pantalla.
     ✅ Sandbox 4-nov: `POST /v1/payments/payouts` → PENDING → SUCCESS en ~15 s; comisión de $0.25 que paga quien envía.
     El `sender_batch_id` es único por pago (`CB-<libro>-<pago>`): PayPal rechaza uno repetido, así un doble clic no
     paga dos veces.
  2. Al confirmarse (SUCCESS), entra al LIBRO solo: COGS · *Subcontractors* (decisión de Jose, 4-nov: para un oficio, el
     contratista trabaja en los trabajos del cliente) y la comisión aparte en *Merchant fees*. La clave es el
     `transaction_id` de PayPal (la sincronización no lo duplica; 🟡 deuda #43: confirmar que es el mismo id del historial).
     Una regla del bookkeeper puede cambiar la categoría, como cualquier fila de PayPal. La IA no participa.
  3. 1099 (✅ instrucciones del IRS para 1099-MISC/NEC, leídas el 4-nov): lo pagado por una red de terceros (PayPal) NO va
     en el 1099-NEC de quien paga; lo reporta PayPal en el 1099-K. La pantalla lo dice.
"""

import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

import paypal
from auth import current_user, owned_company
from db import get_session
from invoices import EMAIL_RE, _connect, _fail, _owner_only
from models import AccountType, Company, CompanyCategory, Contractor, ContractorPayment, PayPalEntry, User
from rules import load_rules

router = APIRouter()

CATEGORY = "Subcontractors"
MAX_PAYOUT = Decimal("10000")          # tope por pago (un error de dedo no vacía la cuenta)
DONE = ("SUCCESS",)
FINAL = ("SUCCESS", "FAILED", "RETURNED", "BLOCKED", "REFUNDED", "DENIED", "REVERSED")


def enabled() -> bool:
    return (os.getenv("PAYPAL_PAYOUTS") or "").strip().lower() in {"1", "true", "yes"}


def _require_enabled() -> None:
    if not enabled():
        raise HTTPException(status_code=404, detail="not available")


def ensure_category(session: Session, company: Company) -> str:
    """La cuenta del pago a un contratista. 🔄 6-nov: si el plan ya tiene una de COSTO DE VENTAS para subcontratistas (el de
    construcción trae "Contract labor – subcontractors"), se usa esa, para no partir el mismo gasto en dos líneas; si no,
    se agrega "Subcontractors" (COGS). Nunca toca una cuenta que ya existe. Sin commit. Devuelve el nombre."""
    rows = session.exec(select(CompanyCategory).where(CompanyCategory.company_id == company.id)).all()
    for r in rows:
        if r.account_type == AccountType.COGS and r.archived_at is None and "subcontractor" in r.name.lower():
            return r.name
    line = "1125-A" if (company.tax_form or "schedule_c") == "1120s" else "SchC:PartIII"
    pos = max([r.position or 0 for r in rows if r.account_type == AccountType.COGS] or [-1]) + 1
    session.add(CompanyCategory(company_id=company.id, account_type=AccountType.COGS, name=CATEGORY, irs_line=line,
                                position=pos, default_key=CATEGORY))
    session.flush()
    return CATEGORY


def _book(session: Session, company: Company, p: ContractorPayment, c: Optional[Contractor]) -> None:
    """El pago confirmado entra al libro (y su comisión aparte). Si la sincronización ya lo trajo, solo se liga."""
    entry = session.exec(select(PayPalEntry).where(PayPalEntry.company_id == company.id)
                         .where(PayPalEntry.paypal_id == p.paypal_tx_id)).first()
    if entry is not None:
        p.transaction_id = entry.transaction_id
        return
    category = ensure_category(session, company)
    rules = load_rules(session, company.id, company.sector or "")
    day = (p.paid_at or datetime.now(timezone.utc)).date()
    who = (c.name if c else "CONTRACTOR").upper()
    tx = paypal.insert_row(session, company, {
        "paypal_id": p.paypal_tx_id, "date": day, "description": f"PAYPAL PAYOUT TO {who}", "signed": -p.amount,
        "event_code": "PAYOUT", "preset": (AccountType.COGS, category)}, rules)
    p.transaction_id = tx.id
    if p.fee:
        paypal.insert_row(session, company, {
            "paypal_id": f"{p.paypal_tx_id}:fee", "date": day, "description": "PAYPAL FEE", "signed": -p.fee,
            "event_code": "PAYOUT", "preset": (AccountType.OPERATING_EXPENSE, paypal.FEE)}, rules)


def _refresh_one(session: Session, company: Company, p: ContractorPayment) -> None:
    """Lee de PayPal el estado del pago y, si se confirmó, lo asienta. Sin commit."""
    if not p.paypal_batch_id or p.status in FINAL:
        return
    g = paypal.api("GET", f"/v1/payments/payouts/{p.paypal_batch_id}")
    it = (g.get("items") or [{}])[0]
    p.status = it.get("transaction_status") or p.status
    p.paypal_item_id = it.get("payout_item_id") or p.paypal_item_id
    p.paypal_tx_id = it.get("transaction_id") or p.paypal_tx_id
    fee = (it.get("payout_item_fee") or {}).get("value")
    p.fee = Decimal(str(fee)) if fee else p.fee
    if p.status in DONE and p.paypal_tx_id and p.transaction_id is None:
        p.paid_at = p.paid_at or datetime.now(timezone.utc).replace(tzinfo=None)
        _book(session, company, p, session.get(Contractor, p.contractor_id) if p.contractor_id else None)
    session.add(p)


def view(session: Session, company_id: int) -> dict:
    cs = session.exec(select(Contractor).where(Contractor.company_id == company_id).order_by(Contractor.name)).all()
    ps = session.exec(select(ContractorPayment).where(ContractorPayment.company_id == company_id)
                      .order_by(ContractorPayment.id.desc())).all()
    names = {c.id: c.name for c in cs}
    year = date.today().year
    # Lo pagado en el año por contratista lo suma el código sobre lo CONFIRMADO (regla 1: la IA no calcula).
    paid_year: dict[int, Decimal] = {}
    for p in ps:
        if p.status in DONE and p.contractor_id and (p.paid_at or p.created_at).year == year:
            paid_year[p.contractor_id] = paid_year.get(p.contractor_id, Decimal("0")) + p.amount
    return {"enabled": True,
            "contractors": [{"id": c.id, "name": c.name, "email": c.email, "paid_this_year": str(paid_year.get(c.id, Decimal("0"))),
                             "has_payments": any(p.contractor_id == c.id for p in ps)} for c in cs],
            "payments": [{"id": p.id, "contractor": names.get(p.contractor_id or 0), "amount": str(p.amount), "memo": p.memo,
                          "status": p.status, "fee": str(p.fee) if p.fee is not None else None, "in_books": p.transaction_id is not None,
                          "date": (p.paid_at or p.created_at).isoformat() + "Z"} for p in ps[:50]]}


@router.get("/api/owner/companies/{company_id}/contractors")
def list_contractors(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    owned_company(company_id, user, session)
    if not enabled():
        return {"enabled": False}
    return view(session, company_id)


class ContractorIn(BaseModel):
    name: str
    email: str


@router.post("/api/owner/companies/{company_id}/contractors")
def add_contractor(company_id: int, payload: ContractorIn, user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    _require_enabled()
    _owner_only(company_id, user, session)
    name, email = payload.name.strip(), payload.email.strip().lower()
    if not name or len(name) > 120 or not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="name_and_email_required")
    session.add(Contractor(company_id=company_id, name=name, email=email[:200]))
    session.commit()
    return view(session, company_id)


def _contractor(session: Session, company_id: int, contractor_id: int) -> Contractor:
    c = session.get(Contractor, contractor_id)
    if c is None or c.company_id != company_id:
        raise HTTPException(status_code=404, detail="contractor not found")
    return c


@router.put("/api/owner/companies/{company_id}/contractors/{contractor_id}")
def edit_contractor(company_id: int, contractor_id: int, payload: ContractorIn, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    """Corrige el nombre o el correo (5-nov). Los pagos ya hechos no cambian: se enviaron al correo de ese momento."""
    _require_enabled()
    _owner_only(company_id, user, session)
    c = _contractor(session, company_id, contractor_id)
    name, email = payload.name.strip(), payload.email.strip().lower()
    if not name or len(name) > 120 or not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="name_and_email_required")
    c.name, c.email = name, email[:200]
    session.add(c)
    session.commit()
    return view(session, company_id)


@router.delete("/api/owner/companies/{company_id}/contractors/{contractor_id}")
def delete_contractor(company_id: int, contractor_id: int, user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    """Solo uno SIN pagos (5-nov): con pagos, el historial y lo pagado en el año lo necesitan → 409 `has_payments`."""
    _require_enabled()
    _owner_only(company_id, user, session)
    c = _contractor(session, company_id, contractor_id)
    if session.exec(select(ContractorPayment.id).where(ContractorPayment.contractor_id == c.id)).first():
        raise HTTPException(status_code=409, detail="has_payments")
    session.delete(c)
    session.commit()
    return view(session, company_id)


class PayIn(BaseModel):
    amount: str
    memo: str = ""


@router.post("/api/owner/companies/{company_id}/contractors/{contractor_id}/pay")
def pay_contractor(company_id: int, contractor_id: int, payload: PayIn, user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    """Le paga desde el saldo de PayPal (el dueño ya confirmó en pantalla). No paga más que el saldo disponible."""
    _require_enabled()
    company = _owner_only(company_id, user, session)
    c = _contractor(session, company_id, contractor_id)
    try:
        amount = Decimal(payload.amount.strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise HTTPException(status_code=422, detail="bad_amount")
    if amount <= 0 or amount > MAX_PAYOUT or amount != amount.quantize(Decimal("0.01")):
        raise HTTPException(status_code=422, detail="bad_amount")
    memo = payload.memo.strip()[:200]
    _connect(session, company_id)
    try:
        bal = paypal.api("GET", "/v1/reporting/balances", params={"currency_code": "USD"}).get("balances") or []
        available = Decimal(str(((bal[0] if bal else {}).get("available_balance") or {}).get("value") or "0"))
    except paypal.PayPalError as e:
        _fail(e)
    if amount > available:
        raise HTTPException(status_code=409, detail="insufficient_balance")
    p = ContractorPayment(company_id=company_id, contractor_id=c.id, amount=amount, memo=memo, created_by_user_id=user.id,
                          created_at=datetime.now(timezone.utc).replace(tzinfo=None))   # UTC sin zona (deuda #38)
    session.add(p)
    session.flush()
    body = {"sender_batch_header": {"sender_batch_id": f"CB-{company_id}-{p.id}",
                                    "email_subject": f"You have a payment from {company.name}"[:255],
                                    "email_message": (memo or f"Payment from {company.name}")[:1000]},
            "items": [{"recipient_type": "EMAIL", "amount": {"value": f"{amount:.2f}", "currency": "USD"}, "receiver": c.email,
                       "note": (memo or "Payment")[:4000], "sender_item_id": f"CB-{p.id}"}]}
    try:
        r = paypal.api("POST", "/v1/payments/payouts", json=body)
    except paypal.PayPalError as e:
        session.rollback()
        _fail(e)
    p.paypal_batch_id = (r.get("batch_header") or {}).get("payout_batch_id")
    try:
        _refresh_one(session, company, p)        # a veces ya viene confirmado; si no, "Actualizar" lo confirma
    except paypal.PayPalError:
        pass
    session.add(p)
    session.commit()
    return view(session, company_id)


@router.post("/api/owner/companies/{company_id}/contractors/refresh")
def refresh_payments(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Pregunta a PayPal el estado de los pagos sin confirmar y asienta los confirmados."""
    _require_enabled()
    company = _owner_only(company_id, user, session)
    open_ = session.exec(select(ContractorPayment).where(ContractorPayment.company_id == company_id)
                         .where(ContractorPayment.status.notin_(FINAL))).all()
    if open_:
        _connect(session, company_id)
        try:
            for p in open_:
                _refresh_one(session, company, p)
        except paypal.PayPalError as e:
            _fail(e)
    session.commit()
    return view(session, company_id)
