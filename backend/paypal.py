"""
PAYPAL, UNA CUENTA MÁS DEL LIBRO (FASE 16 Paso 3, área PayPal / portal del dueño · 31-oct-2026).

API REST de PayPal directa con `httpx` (no el Agent Toolkit: `FASE 16.B.md` §5). Lee el historial con Transaction
Search (`GET /v1/reporting/transactions`, ventanas de 31 días como máximo, hasta 3 horas de demora: §3) y lo asienta
como transacciones del libro, con su propio deduplicado (`PayPalEntry`).

Cómo entra cada movimiento (decisiones de Jose, 30 y 31-oct) — en este orden, el mismo espíritu del motor:
  1. una REGLA del bookkeeper que lo cubra manda (lo escrito por él gana siempre);
  2. si no, el CÓDIGO del evento de PayPal (`T-code`, tabla oficial), como hoy se detectan las transferencias:
       · venta (T00xx que entra)      → ingreso por el BRUTO, "Gross receipts or sales" (IRS: el 1099-K es bruto)
       · su comisión (`fee_amount`)   → fila aparte en "Merchant fees" (gasto; un reembolso de comisión la resta)
       · reembolso (T1107)            → "Returns and allowances" (resta del ingreso: `reports.signed_amount`)
       · retiro al banco (T04xx) / depósito desde el banco (T03xx) → "Transfer", fuera del P&L (el Paso 4 lo empareja)
       · retenciones (T21xx)          → no se asientan: el dinero no salió del negocio
  3. lo demás (un pago enviado, el saldo inicial T1900 de una cuenta de prueba…) → el detector de transferencias y, si
     no, PENDIENTE. Nunca se adivina una categoría. La IA no participa en nada de esto.
Solo lo EXITOSO (`transaction_status == "S"`) y en USD: base caja.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from decimal import Decimal
from typing import Optional

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from auth import client_writable_company, console_company, current_user, owned_company
from db import get_session
from ingestion import make_fingerprint
from corrections import record_change, snapshot
import month_close
from models import (AccountType, BankConnection, Company, CompanyCategory, PayPalConnection, PayPalEntry,
                    PayPalTransferMatch, Transaction, User)
from rules import classify_transfer, load_rules, match_rule

router = APIRouter()
log = logging.getLogger("uvicorn.error")   # sale en la terminal de uvicorn

ACCOUNT_LABEL = "PayPal"
SALE, FEE, REFUND = "Gross receipts or sales", "Merchant fees", "Returns and allowances"
_BASES = {"sandbox": "https://api-m.sandbox.paypal.com", "live": "https://api-m.paypal.com"}


class PayPalError(Exception):
    def __init__(self, code: str, field: Optional[str] = None):
        super().__init__(code)
        self.code = code
        self.field = field     # el campo que PayPal rechazó (de `details[0].field`), para decirlo en pantalla


def environment() -> str:
    return (os.getenv("PAYPAL_ENV") or "sandbox").strip()


def configured() -> bool:
    """¿Hay llaves? El modo `first_party` (una cuenta de prueba con las llaves de la app) es SOLO para el sandbox."""
    return bool((os.getenv("PAYPAL_CLIENT_ID") or "").strip() and (os.getenv("PAYPAL_CLIENT_SECRET") or "").strip()) \
        and environment() == "sandbox"


# ── El cliente ──────────────────────────────────────────────────────────────────────────────────

_token: dict[str, tuple[str, float]] = {}   # entorno → (token, vence); nunca se escribe en logs ni sale al navegador


def _access_token(http: httpx.Client) -> str:
    env = environment()
    tok, exp = _token.get(env, ("", 0.0))
    if tok and time.time() < exp - 60:
        return tok
    r = http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                  auth=(os.getenv("PAYPAL_CLIENT_ID", ""), os.getenv("PAYPAL_CLIENT_SECRET", "")))
    if not r.is_success:
        raise PayPalError("auth_failed")
    body = r.json()
    _token[env] = (body["access_token"], time.time() + int(body.get("expires_in", 300)))
    return body["access_token"]


def api(method: str, path: str, **kw) -> dict:
    """Una llamada a la API de PayPal con el token de la app (Invoicing la usa). Error → `PayPalError(name)`."""
    try:
        with httpx.Client(base_url=_BASES[environment()], timeout=30) as http:
            h = {"Authorization": f"Bearer {_access_token(http)}", "Content-Type": "application/json"}
            r = http.request(method, path, headers={**h, **kw.pop("headers", {})}, **kw)
    except httpx.HTTPError:
        raise PayPalError("network")
    if not r.is_success:
        body = r.json() if r.content else {}
        field = ((body.get("details") or [{}])[0].get("field") or "").rsplit("/", 1)[-1] or None
        log.warning("[paypal] %s %s: HTTP %s %s %s", method, path, r.status_code, body.get("name"),
                    [(d.get("field"), d.get("issue")) for d in (body.get("details") or [])][:3])
        raise PayPalError(body.get("name") or f"http_{r.status_code}", field)
    return r.json() if r.content else {}


def _search(start: datetime, end: datetime) -> list[dict]:
    """Transaction Search entre dos fechas, en ventanas de 31 días (el máximo de PayPal) y todas sus páginas."""
    out: list[dict] = []
    try:
        with httpx.Client(base_url=_BASES[environment()], timeout=30) as http:
            h = {"Authorization": f"Bearer {_access_token(http)}"}
            cur = start
            while cur < end:
                stop = min(cur + timedelta(days=31) - timedelta(seconds=1), end)
                page, pages = 1, 1
                while page <= pages:
                    r = http.get("/v1/reporting/transactions", headers=h, params={
                        "start_date": cur.strftime("%Y-%m-%dT%H:%M:%SZ"), "end_date": stop.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "fields": "transaction_info,payer_info", "page_size": 500, "page": page})
                    if not r.is_success:
                        raise PayPalError((r.json() if r.content else {}).get("name") or f"http_{r.status_code}")
                    body = r.json()
                    out += body.get("transaction_details") or []
                    pages = int(body.get("total_pages") or 1)
                    page += 1
                cur = stop + timedelta(seconds=1)
    except httpx.HTTPError:
        raise PayPalError("network")
    return out


# ── De un movimiento de PayPal a filas del libro (función pura: se prueba sin red) ────────────────

def _name(d: dict) -> str:
    p = d.get("payer_info") or {}
    n = p.get("payer_name") or {}
    full = n.get("alternate_full_name") or " ".join(x for x in (n.get("given_name"), n.get("surname")) if x)
    return (full or p.get("email_address") or "").strip().upper()


def _money(m: Optional[dict]) -> Decimal:
    return Decimal(str((m or {}).get("value") or "0"))


def rows_for(d: dict) -> list[dict]:
    """Las filas del libro que salen de UN movimiento de Transaction Search: 0 (no se asienta), 1 o 2 (venta +
    comisión). Cada fila: paypal_id, date, description, signed (con signo: + entró), event_code y `preset`
    ((sección, categoría) según el T-code, o None = que decidan las reglas o quede Pendiente)."""
    i = d.get("transaction_info") or {}
    code = i.get("transaction_event_code") or ""
    gross = _money(i.get("transaction_amount"))
    fee = _money(i.get("fee_amount"))
    if i.get("transaction_status") != "S" or (i.get("transaction_amount") or {}).get("currency_code", "USD") != "USD":
        return []
    if code.startswith("T21"):                      # retenciones y liberaciones: el dinero no salió del negocio
        return []
    tid = i.get("transaction_id") or ""
    day = date.fromisoformat((i.get("transaction_initiation_date") or "")[:10])
    who = _name(d)
    rows: list[dict] = []

    def add(pid: str, desc: str, amount: Decimal, preset):
        if amount != 0:
            rows.append({"paypal_id": pid, "date": day, "description": desc, "signed": amount,
                         "event_code": code, "preset": preset})

    if code.startswith("T00") and gross > 0:
        add(tid, f"PAYPAL PAYMENT FROM {who}".strip(), gross, (AccountType.REVENUE, SALE))
    elif code == "T1107" and gross < 0:
        add(tid, f"PAYPAL REFUND TO {who}".strip(), gross, (AccountType.REVENUE, REFUND))
    elif code.startswith("T04"):
        add(tid, "PAYPAL TRANSFER TO BANK", gross, (AccountType.NON_PL, "Transfer"))
    elif code.startswith("T03"):
        add(tid, "PAYPAL TRANSFER FROM BANK", gross, (AccountType.NON_PL, "Transfer"))
    elif code.startswith("T00"):
        add(tid, f"PAYPAL PAYMENT TO {who}".strip(), gross, None)            # una compra pagada con PayPal
    else:
        add(tid, f"PAYPAL {code}", gross, None)                             # p. ej. T1900: lo decide el bookkeeper
    # La comisión, aparte (nunca neta en la venta): PayPal la manda en negativo; un reembolso de comisión, en positivo.
    add(f"{tid}:fee", "PAYPAL FEE", fee, (AccountType.OPERATING_EXPENSE, FEE))
    return rows


# ── El plan de cuentas: las dos cuentas que PayPal necesita ─────────────────────────────────────

def ensure_accounts(session: Session, company: Company) -> int:
    """Agrega al plan del libro "Merchant fees" y "Returns and allowances" si no están (Jose, 31-oct). Nunca toca
    una cuenta que ya existe (por nombre, sin mayúsculas). Sin commit. Devuelve cuántas agregó."""
    rows = session.exec(select(CompanyCategory).where(CompanyCategory.company_id == company.id)).all()
    have = {r.name.lower() for r in rows}
    s_corp = (company.tax_form or "schedule_c") == "1120s"
    wanted = [(AccountType.OPERATING_EXPENSE, FEE, "1120S:20" if s_corp else "SchC:PartV"),
              (AccountType.REVENUE, REFUND, "1120S:1b" if s_corp else "SchC:2")]
    added = 0
    for sec, name, line in wanted:
        if name.lower() in have:
            continue
        pos = max([r.position or 0 for r in rows if r.account_type == sec] or [-1]) + 1
        session.add(CompanyCategory(company_id=company.id, account_type=sec, name=name, irs_line=line,
                                    position=pos, default_key=name))
        added += 1
    if added:
        session.flush()
    return added


# ── La sincronización ───────────────────────────────────────────────────────────────────────────

def insert_row(session: Session, company: Company, r: dict, rules: list, counts: Optional[dict] = None) -> Transaction:
    """Asienta UNA fila de PayPal en el libro: regla → código de PayPal (`preset`) → transferencia → Pendiente, y su
    `PayPalEntry` (el dedupe). La usan la sincronización y una factura pagada (`invoices.py`). Sin commit."""
    counts = counts if counts is not None else {}
    tx = Transaction(company_id=company.id, date=r["date"], description=r["description"],
                     amount=abs(r["signed"]), is_inflow=r["signed"] > 0, bank_account=ACCOUNT_LABEL,
                     fingerprint=make_fingerprint(r["date"], r["description"], r["signed"], ACCOUNT_LABEL))
    rule = match_rule(rules, r["description"], r["date"], abs(r["signed"]))
    transfer = None if rule or r["preset"] else classify_transfer(r["description"])
    if rule is not None:
        tx.account_type, tx.category = rule.account_type, rule.category
        tx.subcategory = (rule.subcategory or rule.match_text).upper()
        tx.source, tx.matched_rule_id = "rule", rule.id
        key = "by_rule"
    elif r["preset"] is not None:
        tx.account_type, tx.category = r["preset"]
        tx.subcategory, tx.source = "PAYPAL", "paypal"
        key = "by_paypal"
    elif transfer is not None:
        tx.account_type, tx.category = transfer
        tx.source = "transfer"
        key = "by_transfer"
    else:
        key = "pending"
    tx.confidence = 1.0 if tx.account_type is not None else None
    session.add(tx)
    session.flush()
    session.add(PayPalEntry(company_id=company.id, paypal_id=r["paypal_id"], event_code=r["event_code"],
                            transaction_id=tx.id))
    counts[key] = counts.get(key, 0) + 1
    counts["new"] = counts.get("new", 0) + 1
    return tx


def sync(session: Session, conn: PayPalConnection, company: Company) -> dict:
    """Trae lo nuevo de PayPal y lo asienta. Desde el 1 de enero la primera vez; después, desde la última
    sincronización menos 7 días (PayPal tarda hasta 3 horas y un movimiento puede cambiar de estado). Lo ya
    importado se reconoce por su id (`PayPalEntry`): nunca se duplica."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = (conn.last_synced_at.replace(tzinfo=timezone.utc) - timedelta(days=7)) if conn.last_synced_at \
        else datetime(now.year, 1, 1, tzinfo=timezone.utc)
    try:
        details = _search(start, now)
    except PayPalError as e:
        conn.status, conn.last_error = "error", e.code
        session.add(conn); session.commit()
        raise
    candidates = [r for d in details for r in rows_for(d)]
    known = set(session.exec(select(PayPalEntry.paypal_id).where(PayPalEntry.company_id == company.id)
                             .where(PayPalEntry.paypal_id.in_({r["paypal_id"] for r in candidates} or {""}))).all())
    rules = load_rules(session, company.id, company.sector or "")
    counts = {"new": 0, "by_rule": 0, "by_paypal": 0, "by_transfer": 0, "pending": 0, "known": 0}
    for r in candidates:
        if r["paypal_id"] in known:
            counts["known"] += 1
            continue
        known.add(r["paypal_id"])
        insert_row(session, company, r, rules, counts)
    conn.status, conn.last_error, conn.last_synced_at = "active", None, now
    session.add(conn)
    session.commit()
    counts["transfers"] = match_transfers(session, company)
    return counts


# ── Paso 4: el retiro de PayPal ↔ su depósito en el banco ───────────────────────────────────────
# Sin esto, la venta se cuenta DOS veces: una al entrar a PayPal (bruto) y otra si el depósito del banco termina
# categorizado como ingreso. El CÓDIGO empareja (no la IA): mismo monto exacto y el depósito de 0 a 7 días después del
# retiro (🟡 PayPal tarda de minutos a pocos días hábiles). Automático SOLO si hay UN candidato y está Pendiente (o ya es
# Transfer); si hay varios, o el único ya tiene una categoría de una regla, lo confirma el bookkeeper. Nunca toca una
# fila escrita a mano ni un mes cerrado.

WINDOW_DAYS = 7
VIA_PAYPAL_TRANSFER = "paypal_transfer"


def _withdrawals(session: Session, company_id: int) -> list[Transaction]:
    return session.exec(
        select(Transaction).join(PayPalEntry, PayPalEntry.transaction_id == Transaction.id)
        .where(PayPalEntry.company_id == company_id).where(PayPalEntry.event_code.startswith("T04"))
        .where(~PayPalEntry.paypal_id.endswith(":fee")).order_by(Transaction.date)
    ).all()


def _candidates(session: Session, w: Transaction, taken: set[int]) -> list[Transaction]:
    rows = session.exec(
        select(Transaction).where(Transaction.company_id == w.company_id)
        .where((Transaction.bank_account.is_(None)) | (Transaction.bank_account != ACCOUNT_LABEL))
        .where(Transaction.is_inflow.is_(True)).where(Transaction.amount == w.amount)
        .where(Transaction.date >= w.date).where(Transaction.date <= w.date + timedelta(days=WINDOW_DAYS))
        .order_by(Transaction.date)
    ).all()
    return [t for t in rows if t.id not in taken and not month_close.is_locked(session, t)]


def _is_transfer(t: Transaction) -> bool:
    return t.account_type == AccountType.NON_PL and t.category == "Transfer"


def match_transfers(session: Session, company: Company) -> dict:
    """Empareja lo que se pueda solo; cuenta lo que queda para el bookkeeper. Con commit."""
    decided = session.exec(select(PayPalTransferMatch).where(PayPalTransferMatch.company_id == company.id)).all()
    done = {m.withdrawal_tx_id for m in decided}
    taken = {m.bank_tx_id for m in decided if m.bank_tx_id}
    out = {"auto": 0, "proposals": 0, "unmatched": 0}
    for w in _withdrawals(session, company.id):
        if w.id in done:
            continue
        cands = _candidates(session, w, taken)
        one = cands[0] if len(cands) == 1 else None
        if one is not None and (_is_transfer(one) or (one.account_type is None and one.source != "manual")):
            if not _is_transfer(one):
                before = snapshot(one)
                one.account_type, one.category, one.source = AccountType.NON_PL, "Transfer", "transfer"
                one.confidence, one.needs_review = 1.0, False
                session.add(one)
                record_change(session, one, before, via=VIA_PAYPAL_TRANSFER)
            session.add(PayPalTransferMatch(company_id=company.id, withdrawal_tx_id=w.id, bank_tx_id=one.id, status="auto"))
            taken.add(one.id)
            out["auto"] += 1
        elif cands:
            out["proposals"] += 1
        else:
            out["unmatched"] += 1
    session.commit()
    return out


def _brief(t: Transaction) -> dict:
    return {"id": t.id, "date": t.date.isoformat(), "description": t.description, "amount": str(t.amount),
            "bank_account": t.bank_account, "account_type": t.account_type.value if t.account_type else None,
            "category": t.category}


def transfers_view(session: Session, company_id: int) -> dict:
    """Para la tarjeta de Bancos: lo emparejado, lo que espera al bookkeeper (con sus candidatos) y lo que aún no
    tiene depósito en el banco (puede que el banco no lo haya asentado todavía)."""
    decided = {m.withdrawal_tx_id: m for m in session.exec(
        select(PayPalTransferMatch).where(PayPalTransferMatch.company_id == company_id)).all()}
    taken = {m.bank_tx_id for m in decided.values() if m.bank_tx_id}
    matched, proposals, unmatched = [], [], []
    for w in _withdrawals(session, company_id):
        m = decided.get(w.id)
        if m is not None:
            if m.status != "rejected" and m.bank_tx_id:
                b = session.get(Transaction, m.bank_tx_id)
                matched.append({"withdrawal": _brief(w), "deposit": _brief(b) if b else None, "status": m.status})
            continue
        cands = _candidates(session, w, taken)
        (proposals if cands else unmatched).append(
            {"withdrawal": _brief(w), "candidates": [_brief(c) for c in cands]} if cands else {"withdrawal": _brief(w)})
    return {"matched": matched, "proposals": proposals, "unmatched": unmatched, "window_days": WINDOW_DAYS}


# ── Las rutas ───────────────────────────────────────────────────────────────────────────────────

def _status(session: Session, conn: Optional[PayPalConnection]) -> dict:
    if conn is None:
        return {"connected": False, "available": configured(), "environment": environment()}
    return {"connected": True, "available": configured(), "environment": conn.environment, "mode": conn.mode,
            "status": conn.status, "last_error": conn.last_error,
            "last_synced_at": conn.last_synced_at.isoformat() if conn.last_synced_at else None}


def _conn(session: Session, company_id: int) -> Optional[PayPalConnection]:
    return session.exec(select(PayPalConnection).where(PayPalConnection.company_id == company_id)).first()


def _fail(e: PayPalError):
    raise HTTPException(status_code=502, detail={"code": "paypal_" + e.code})


@router.get("/api/companies/{company_id}/paypal")
def paypal_status(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El PayPal del libro, para la página Bancos de la consola."""
    console_company(company_id, user, session)
    return _status(session, _conn(session, company_id))


@router.post("/api/companies/{company_id}/paypal/connect")
def paypal_connect(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Conecta el PayPal del negocio y trae su historial. Lo pueden hacer el DUEÑO (desde Cuentas: es su cuenta) y el
    bookkeeper (Jose, 31-oct); no un VIEWER. Hoy solo existe el modo `first_party` (sandbox, el demo): Partner
    Referrals respondió 403 sin ser partner aprobado (`FASE 16.B.md` §8). Agrega al plan las dos cuentas que PayPal
    necesita. Devuelve el estado, nunca un secreto."""
    company = client_writable_company(company_id, user, session)
    if not configured():
        raise HTTPException(status_code=503, detail={"code": "paypal_not_configured"})
    conn = _conn(session, company_id)
    if conn is None:
        conn = PayPalConnection(company_id=company_id, mode="first_party", environment=environment(),
                                connected_by_user_id=user.id)
        session.add(conn)
    ensure_accounts(session, company)
    session.commit()
    try:
        counts = sync(session, conn, company)
    except PayPalError as e:
        _fail(e)
    return {**_status(session, conn), "imported": counts}


@router.post("/api/companies/{company_id}/paypal/sync")
def paypal_sync(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Trae lo nuevo de PayPal (el bookkeeper, desde Bancos)."""
    company = owned_company(company_id, user, session, write=True)
    conn = _conn(session, company_id)
    if conn is None:
        raise HTTPException(status_code=404, detail={"code": "paypal_not_connected"})
    try:
        counts = sync(session, conn, company)
    except PayPalError as e:
        _fail(e)
    return {**_status(session, conn), "imported": counts}


@router.delete("/api/companies/{company_id}/paypal")
def paypal_disconnect(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Desconecta PayPal. Lo ya importado se queda en el libro (como al quitar un banco)."""
    owned_company(company_id, user, session, write=True)
    conn = _conn(session, company_id)
    if conn is not None:
        session.delete(conn)
        session.commit()
    return {"connected": False}


class TransferConfirm(BaseModel):
    bank_tx_id: int


@router.get("/api/companies/{company_id}/paypal/transfers")
def paypal_transfers(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Los retiros de PayPal al banco y sus depósitos: emparejados, por confirmar y sin depósito todavía."""
    console_company(company_id, user, session)
    return transfers_view(session, company_id)


@router.post("/api/companies/{company_id}/paypal/transfers/match")
def paypal_transfers_match(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Vuelve a buscar depósitos (p. ej. después de sincronizar el banco). Empareja solo lo que no tiene duda."""
    company = owned_company(company_id, user, session, write=True)
    return {**match_transfers(session, company), **transfers_view(session, company_id)}


@router.post("/api/companies/{company_id}/paypal/transfers/{withdrawal_id}/confirm")
def paypal_transfer_confirm(company_id: int, withdrawal_id: int, payload: TransferConfirm,
                            user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El bookkeeper elige el depósito de un retiro. El depósito queda como Transfer y como decisión SUYA
    (`source="manual"`: ninguna regla lo vuelve a tocar). Un mes cerrado responde 409 (el guardián)."""
    owned_company(company_id, user, session, write=True)
    w = session.get(Transaction, withdrawal_id)
    if w is None or w.company_id != company_id or w.id not in {t.id for t in _withdrawals(session, company_id)}:
        raise HTTPException(status_code=404, detail="withdrawal not found")
    if session.exec(select(PayPalTransferMatch).where(PayPalTransferMatch.withdrawal_tx_id == w.id)).first():
        raise HTTPException(status_code=409, detail="already decided")
    taken = {m.bank_tx_id for m in session.exec(select(PayPalTransferMatch)
                                               .where(PayPalTransferMatch.company_id == company_id)).all() if m.bank_tx_id}
    b = next((c for c in _candidates(session, w, taken) if c.id == payload.bank_tx_id), None)
    if b is None:
        raise HTTPException(status_code=422, detail="not a candidate")
    before = snapshot(b)
    b.account_type, b.category, b.source, b.matched_rule_id = AccountType.NON_PL, "Transfer", "manual", None
    b.needs_review = False
    session.add(b)
    record_change(session, b, before, via=VIA_PAYPAL_TRANSFER, user_id=user.id)
    session.add(PayPalTransferMatch(company_id=company_id, withdrawal_tx_id=w.id, bank_tx_id=b.id,
                                    status="confirmed", decided_by_user_id=user.id))
    session.commit()
    return transfers_view(session, company_id)


@router.post("/api/companies/{company_id}/paypal/transfers/{withdrawal_id}/reject")
def paypal_transfer_reject(company_id: int, withdrawal_id: int, user: User = Depends(current_user),
                           session: Session = Depends(get_session)):
    """"Ninguno es este retiro": deja de proponerse. No cambia ninguna transacción."""
    owned_company(company_id, user, session, write=True)
    w = session.get(Transaction, withdrawal_id)
    if w is None or w.company_id != company_id:
        raise HTTPException(status_code=404, detail="withdrawal not found")
    if not session.exec(select(PayPalTransferMatch).where(PayPalTransferMatch.withdrawal_tx_id == w.id)).first():
        session.add(PayPalTransferMatch(company_id=company_id, withdrawal_tx_id=w.id, status="rejected",
                                        decided_by_user_id=user.id))
        session.commit()
    return transfers_view(session, company_id)


# ── "Conectar con PayPal": Log in with PayPal (Jose, 1-nov) ──────────────────────────────────────
# El dueño inicia sesión DE VERDAD en PayPal y autoriza; ContaBu recibe su IDENTIDAD (correo, id de cuenta) — nunca su
# contraseña ni, por esta vía, sus transacciones (✅ "identity data only"). En el demo, el acceso a los datos sigue
# siendo `first_party` (las llaves de la app, sobre UNA cuenta de prueba): por eso se conecta solo si la cuenta con que
# inició sesión ES la del negocio del demo (`PAYPAL_SANDBOX_MERCHANT_EMAIL`). Con otra, se dice tal cual: en producción
# el acceso lo daría Partner Referrals (exige ser partner aprobado: `FASE 16.B.md` §2 y §8).
# El `state` va FIRMADO (HMAC) con el libro, el usuario y la hora: nadie puede canjear un código para el libro de otro.

_AUTH_HOSTS = {"sandbox": "https://www.sandbox.paypal.com", "live": "https://www.paypal.com"}
STATE_TTL = 15 * 60


def return_url() -> str:
    return (os.getenv("PAYPAL_RETURN_URL") or "http://127.0.0.1:5173/paypal-return").strip()   # el sandbox no guarda localhost


def _sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(os.getenv("PAYPAL_CLIENT_SECRET", "").encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{mac}"


def _unsign(state: str) -> Optional[dict]:
    try:
        raw, mac = state.rsplit(".", 1)
        good = hmac.new(os.getenv("PAYPAL_CLIENT_SECRET", "").encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(mac, good):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return payload if time.time() - payload.get("t", 0) <= STATE_TTL else None
    except (ValueError, json.JSONDecodeError):
        return None


@router.post("/api/companies/{company_id}/paypal/authorize")
def paypal_authorize(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El enlace para iniciar sesión en PayPal (el dueño o el bookkeeper; no un VIEWER)."""
    client_writable_company(company_id, user, session)
    if not configured():
        raise HTTPException(status_code=503, detail={"code": "paypal_not_configured"})
    state = _sign({"c": company_id, "u": user.id, "t": int(time.time())})
    q = urlencode({"flowEntry": "static", "client_id": os.getenv("PAYPAL_CLIENT_ID", ""), "response_type": "code",
                   "scope": "openid email",   # solo lo marcado en la app (Email); el id de cuenta, si viene, sobra
                   "redirect_uri": return_url(), "state": state})
    return {"url": f"{_AUTH_HOSTS[environment()]}/connect?{q}"}


class OAuthReturn(BaseModel):
    code: str
    state: str


def _jwt_claims(token: Optional[str]) -> dict:
    """Los datos (payload) de un JWT, sin verificar firma: solo se usa con un `id_token` recibido directamente de PayPal."""
    try:
        part = (token or "").split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def _identity(code: str) -> dict:
    """Canjea el código por un token del USUARIO y lee su identidad. El token no se guarda."""
    try:
        with httpx.Client(base_url=_BASES[environment()], timeout=30) as http:
            r = http.post("/v1/oauth2/token", data={"grant_type": "authorization_code", "code": code},
                          auth=(os.getenv("PAYPAL_CLIENT_ID", ""), os.getenv("PAYPAL_CLIENT_SECRET", "")))
            if not r.is_success:
                log.warning("[paypal connect] canje del código: HTTP %s %s", r.status_code,
                            (r.json() if r.content else {}).get("error"))
                raise PayPalError("code_rejected")
            tokres = r.json()
            tok = tokres["access_token"]
            # El `id_token` (OpenID) es la identidad firmada de QUIEN inició sesión. Llega directo de PayPal por TLS,
            # servidor a servidor: se leen sus datos (sin guardar nada). Para diagnosticar: qué campos trae.
            claims = _jwt_claims(tokres.get("id_token"))
            log.info("[paypal connect] respuesta del canje: campos=%s · id_token=%s", sorted(tokres.keys()),
                     {k: claims[k] for k in ("sub", "email", "email_verified", "payer_id", "user_id") if k in claims} or "—")
            if claims.get("email"):
                return {"email": str(claims["email"]).lower(), "payer_id": claims.get("payer_id") or claims.get("sub"),
                        "account_id": None}
            # La cuenta DUEÑA de la app (la Business del demo) recibe un token de comerciante y PayPal no le entrega
            # identidad (400 "Unable to fetch details", ✅ 1-nov). Su saldo sí, y con él el id de la cuenta: tener acceso
            # al saldo prueba que es esa cuenta. Una cuenta ajena no lo puede leer → se intenta el correo.
            bal = http.get("/v1/reporting/balances", params={"currency_code": "USD"},
                           headers={"Authorization": f"Bearer {tok}"})
            if bal.is_success and bal.json().get("account_id"):
                log.info("[paypal connect] la cuenta que inició sesión es %s (por su saldo)", bal.json()["account_id"])
                return {"email": None, "payer_id": None, "account_id": bal.json()["account_id"]}
            log.info("[paypal connect] saldo con el token del usuario: HTTP %s (no es cuenta de comerciante)", bal.status_code)
            # Dos direcciones de identidad en PayPal: la de "Log in with PayPal" y la de OpenID Connect (respaldo).
            info = None
            hdr = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}   # como en la doc oficial
            for path, schema in (("/v1/identity/oauth2/userinfo", "paypalv1.1"),
                                 ("/v1/identity/oauth2/userinfo", "openid"),
                                 ("/v1/identity/openidconnect/userinfo", "openid")):
                u = http.get(path, params={"schema": schema}, headers=hdr)
                if u.is_success:
                    info = u.json()
                    break
                try:
                    why = {k: v for k, v in u.json().items() if k in ("error", "error_description", "name", "message")}
                except ValueError:
                    why = u.text[:160]
                log.warning("[paypal connect] identidad en %s?schema=%s: HTTP %s %s", path, schema, u.status_code, why)
            log.info("[paypal connect] permisos del token: %s", r.json().get("scope"))
            if info is None:
                raise PayPalError("userinfo_failed")
    except httpx.HTTPError:
        raise PayPalError("network")
    emails = info.get("emails") or []   # paypalv1.1: [{value, primary}] · openid: "email"
    email = next((e.get("value") for e in emails if e.get("primary")), None) or (emails[0].get("value") if emails else None)         or info.get("email")
    return {"email": (email or "").lower(), "payer_id": info.get("payer_id"), "account_id": None}


def _app_account_id() -> Optional[str]:
    """El id de la cuenta que manejan las llaves de la app (la Business del demo), por su saldo."""
    try:
        return api("GET", "/v1/reporting/balances", params={"currency_code": "USD"}).get("account_id")
    except PayPalError:
        return None


@router.post("/api/paypal/oauth")
def paypal_oauth(payload: OAuthReturn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """La vuelta de PayPal: verifica el `state` (este usuario, este libro, menos de 15 min), lee la identidad y, si es
    la cuenta del negocio del demo, conecta e importa su historial."""
    st = _unsign(payload.state)
    if st is None or st.get("u") != user.id:
        log.warning("[paypal connect] state inválido o de otro usuario (user=%s)", user.id)
        raise HTTPException(status_code=400, detail={"code": "paypal_bad_state"})
    company = client_writable_company(int(st["c"]), user, session)
    try:
        who = _identity(payload.code)
    except PayPalError as e:
        log.warning("[paypal connect] libro=%s: PayPal rechazó el canje del código (%s)", company.id, e.code)
        _fail(e)
    demo_email = (os.getenv("PAYPAL_SANDBOX_MERCHANT_EMAIL") or "").strip().lower()
    demo_account = _app_account_id() if who.get("account_id") else None
    same = (who.get("account_id") and who["account_id"] == demo_account) or (demo_email and who.get("email") == demo_email)
    if not same:
        # Para diagnosticar SIN secretos: con qué cuenta entró y contra cuál se compara (ninguno es secreto).
        log.warning("[paypal connect] libro=%s: entró como %r / cuenta %r; la del demo es %r / %r → no se conecta",
                    company.id, who.get("email"), who.get("account_id"), demo_email or "(sin PAYPAL_SANDBOX_MERCHANT_EMAIL)",
                    demo_account)
        raise HTTPException(status_code=409, detail={"code": "paypal_other_account",
                                                     "email": who.get("email") or who.get("account_id"),
                                                     "company_id": company.id})
    who["email"] = who.get("email") or demo_email
    who["payer_id"] = who.get("payer_id") or who.get("account_id")
    log.info("[paypal connect] libro=%s: conectado (%r)", company.id, who["email"])
    conn = _conn(session, company.id)
    if conn is None:
        conn = PayPalConnection(company_id=company.id, mode="first_party", environment=environment(),
                                connected_by_user_id=user.id)
        session.add(conn)
    conn.merchant_id = who["payer_id"]
    ensure_accounts(session, company)
    session.commit()
    try:
        counts = sync(session, conn, company)
    except PayPalError as e:
        _fail(e)
    return {**_status(session, conn), "imported": counts, "email": who["email"], "company_id": company.id}


@router.get("/api/owner/companies/{company_id}/accounts")
def owner_accounts(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """La ventana CUENTAS del dueño: sus bancos (Plaid) y su PayPal, con la última sincronización. Solo nombres,
    últimos 4 dígitos y estado: ningún token, id de Plaid ni transacción."""
    owned_company(company_id, user, session)
    banks = session.exec(select(BankConnection).where(BankConnection.company_id == company_id)
                         .order_by(BankConnection.id)).all()
    return {
        "banks": [{"institution": b.institution_name, "status": b.status,
                   "last_synced_at": b.last_synced_at.isoformat() if b.last_synced_at else None,
                   "accounts": [{"name": a.get("name"), "mask": a.get("mask")} for a in (b.accounts or [])]}
                  for b in banks],
        "paypal": _status(session, _conn(session, company_id)),
    }
