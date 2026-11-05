"""
RECORDATORIOS DE COBRO Y QR DE PAGO (FASE 16, área PayPal / portal del dueño · 1-nov-2026).

Tres herramientas para una factura enviada y sin pagar, SOLO del dueño (el bookkeeper no cobra a su nombre):

  1. RECORDATORIO OFICIAL de PayPal (`POST /v2/invoicing/invoices/{id}/remind`). PayPal manda su correo estándar:
     ✅ su especificación oficial dice que el asunto y la nota que uno mande "will not be honored", así que no se manda
     texto propio. Uno cada 24 horas como máximo (el código lo frena; se guarda cuántos y cuándo).
  2. MENSAJE PERSONAL redactado por Nemotron, para que el dueño lo revise y lo mande desde SU correo. La IA NO ve ni
     calcula nada del cobro (regla de oro): recibe solo el estado (por vencer / vence hoy / vencida), el idioma y el
     tono, y escribe con marcadores ({customer}, {amount}, {number}, {due_date}, {days_overdue}, {link}, {business}).
     Los días de atraso los cuenta el código (✅ 1-nov: si se los daba como número, la IA los copiaba en el texto). El CÓDIGO rechaza un texto con cifras propias o sin {amount} y {link}, y pone
     los datos reales. Ni el nombre del cliente, ni el del negocio, ni el monto llegan a la IA.
  3. QR DE PAGO (`POST …/generate-qr-code`, acción "pay"): para cobrar en persona; el cliente lo escanea y paga en
     PayPal. ✅ PayPal lo devuelve como un multipart con la imagen PNG en base64 (probado en sandbox el 1-nov).
  4. RECORDATORIOS AUTOMÁTICOS (3-nov): PayPal los manda solo, según dos reglas DE LA CUENTA (no por factura): antes del
     vencimiento y después. Viven en PayPal; ContaBu solo las lee y las cambia (sin tabla propia). Rutas que usa el Agent
     Toolkit oficial y que NO están en la especificación pública de Invoicing; ✅ probadas en sandbox el 3-nov:
     `GET /v2/invoicing/reminders` · `POST /v2/invoicing/setup-reminders` (crea las dos, INACTIVAS) ·
     `PUT /v2/invoicing/reminders/{id}` (días y repeticiones; IGNORA el estado) · `POST …/{id}/resume` y `…/suspend`
     (activar y desactivar). Límites: de 1 a 7 días; después del vencimiento, de 1 a 7 veces; antes, 1 vez.
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

import agent_calls
import nemotron
import paypal
from auth import current_user, owned_company
from db import get_session
from invoices import OPEN, _connect, _fail, _owner_only, _view
from models import Company, Invoice, InvoiceCustomer, User

router = APIRouter()

REMIND_EVERY = timedelta(hours=24)
PLACEHOLDERS = ("customer", "business", "number", "amount", "due_date", "days_overdue", "link")
_MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
              "noviembre", "diciembre")


def _open_invoice(session: Session, company_id: int, invoice_id: int) -> Invoice:
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.company_id != company_id:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv.status not in OPEN or not inv.paypal_invoice_id:
        raise HTTPException(status_code=409, detail="invoice_not_open")
    return inv


def next_reminder_at(inv: Invoice) -> Optional[datetime]:
    """Desde cuándo se puede mandar otro recordatorio oficial (None = ya se puede)."""
    if inv.last_reminder_at is None:
        return None
    # Se guarda en UTC SIN zona (`_utc_naive`): así no depende de la zona horaria de la base (la local es Los Ángeles).
    nxt = inv.last_reminder_at.replace(tzinfo=None) + REMIND_EVERY
    return nxt.replace(tzinfo=timezone.utc) if nxt > _utc_naive() else None


def _utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 1. Recordatorio oficial de PayPal ───────────────────────────────────────────────────────────

@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/remind")
def send_reminder(company_id: int, invoice_id: int, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    """PayPal manda su recordatorio estándar al cliente. Uno cada 24 h."""
    _owner_only(company_id, user, session)
    inv = _open_invoice(session, company_id, invoice_id)
    if next_reminder_at(inv) is not None:
        raise HTTPException(status_code=409, detail="reminded_recently")
    _connect(session, company_id)        # las llaves de ESTE libro (3-nov: sin esto, una conexión `own_keys` fallaba)
    try:
        paypal.api("POST", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}/remind",
                   json={"send_to_recipient": True, "send_to_invoicer": False})
    except paypal.PayPalError as e:
        _fail(e)
    inv.reminder_count = (inv.reminder_count or 0) + 1
    inv.last_reminder_at = _utc_naive()
    session.add(inv)
    session.commit()
    return _view(session, company_id)


# ── 2. Mensaje personal (Nemotron redacta, el código pone los datos) ─────────────────────────────

class DraftIn(BaseModel):
    lang: str = "en"
    tone: str = "friendly"      # "friendly" | "firm"


class DraftOut(BaseModel):
    subject: str
    body: str


_SYSTEM = """You write a short payment reminder that a small-business owner will review and send from their own email.
You never see the real data. The code fills in these placeholders; write them literally, with the braces:
  {customer}      the customer's first name
  {business}      the owner's business name
  {number}        the invoice number
  {amount}        the amount due, already formatted with the currency
  {due_date}      the due date, already written out
  {days_overdue}  how long it is overdue, already written with its unit (e.g. "a few days" in the right language)
  {link}          the PayPal page where the customer pays by card or PayPal
Rules:
- Return ONLY JSON: {"subject": "...", "body": "..."}.
- The body MUST contain {amount} AND {link}, exactly like that. Start with a greeting to {customer}; sign as {business}.
- Use ONLY the placeholders above, spelled exactly; never invent another one (no {payment_link}, no {name}).
- NEVER write a digit, amount, date, number of days or currency symbol yourself. To say how late it is, write
  {days_overdue}; to say when it was due, write {due_date}.
- Write in the requested LANGUAGE ("es" = Spanish, "en" = English). Body: 3 to 6 short sentences, plain text, no
  markdown. TONE "friendly" = warm, assumes it slipped their mind; "firm" = polite but clear that payment is overdue.
- STATE is "not due yet", "due today" or "overdue"; match the wording to it (mention {days_overdue} only if overdue)."""

_DIGITS = re.compile(r"[0-9$€£]")


def _state(inv: Invoice, today: date) -> str:
    """El CÓDIGO cuenta los días (la IA no calcula)."""
    if inv.due_date > today:
        return "not due yet"
    if inv.due_date == today:
        return "due today"
    return "overdue"


def _days(n: int, lang: str) -> str:
    """Los días de atraso con su unidad: los cuenta y los escribe el código (la IA solo pone {days_overdue})."""
    if lang == "es":
        return "1 día" if n == 1 else f"{n} días"
    return "1 day" if n == 1 else f"{n} days"


def check_draft(raw: dict) -> Optional[DraftOut]:
    """El código valida el texto: con {amount} y {link}, sin cifras propias, sin marcadores inventados."""
    try:
        d = DraftOut.model_validate(raw if isinstance(raw, dict) else {})
    except Exception:
        return None
    subject, body = d.subject.strip(), d.body.strip()
    if not subject or not body or len(subject) > 200 or len(body) > 2000:
        return None
    if "{amount}" not in body or "{link}" not in body:
        return None
    for text in (subject, body):
        if set(re.findall(r"\{(\w+)\}", text)) - set(PLACEHOLDERS):
            return None
        if _DIGITS.search(re.sub(r"\{\w+\}", "", text)):
            return None
    return DraftOut(subject=subject, body=body)


def _long_date(d: date, lang: str) -> str:
    if lang == "es":
        return f"{d.day} de {_MONTHS_ES[d.month - 1]} de {d.year}"
    return d.strftime("%B ") + f"{d.day}, {d.year}"


def fill(text: str, values: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), text)


@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/reminder-draft")
def draft_reminder(company_id: int, invoice_id: int, payload: DraftIn, user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    """Un borrador de mensaje personal. No se envía nada: el dueño lo revisa, lo edita y lo manda desde su correo."""
    company: Company = _owner_only(company_id, user, session)
    inv = _open_invoice(session, company_id, invoice_id)
    lang = "es" if payload.lang == "es" else "en"
    tone = "firm" if payload.tone == "firm" else "friendly"
    try:
        agent_calls.check_cap(session, company)
    except agent_calls.CapReached:
        raise HTTPException(status_code=429, detail="ai_cap_reached")
    today = date.today()
    user_msg = f"LANGUAGE: {lang}\nTONE: {tone}\nSTATE: {_state(inv, today)}"
    draft, reply = None, None
    for attempt in range(2):            # UN reintento si el texto no pasa la validación o se queda sin tokens
        try:
            reply = nemotron.chat_json(_SYSTEM, user_msg, DraftOut, max_tokens=1200, reasoning=False,
                                       temperature=0.4)
        except nemotron.NemotronUnavailable:
            raise HTTPException(status_code=503, detail="ai_unavailable")
        except nemotron.NemotronError as e:
            agent_calls.record(session, company, "invoice_reminder", e.usage, outcome=e.code, user_id=user.id,
                               model=nemotron.model_name())
            session.commit()
            if e.code != "out_of_tokens" or attempt == 1:
                raise HTTPException(status_code=502, detail="ai_failed")
            continue
        draft = check_draft(reply.data)
        agent_calls.record(session, company, "invoice_reminder", reply.usage, user_id=user.id,
                           items_out=1, items_valid=int(draft is not None))
        session.commit()
        if draft is not None:
            break
    if draft is None:
        raise HTTPException(status_code=502, detail="ai_failed")
    c = session.get(InvoiceCustomer, inv.customer_id) if inv.customer_id else None
    values = {"customer": (c.name.split()[0] if c else ("cliente" if lang == "es" else "there")),
              "business": company.name, "number": inv.number or str(inv.id),
              "amount": f"${inv.amount:,.2f} {inv.currency}", "due_date": _long_date(inv.due_date, lang),
              "days_overdue": _days(max((today - inv.due_date).days, 0), lang),
              "link": inv.recipient_view_url or ""}
    subject, body = fill(draft.subject, values), fill(draft.body, values)
    mailto = (f"mailto:{c.email}?subject={quote(subject)}&body={quote(body)}" if c else None)
    return {"subject": subject, "body": body, "mailto": mailto, "to": c.email if c else None,
            "sent_to_ai": user_msg}


# ── 3. QR de pago ───────────────────────────────────────────────────────────────────────────────

def parse_qr(content_type: str, raw: bytes) -> Optional[str]:
    """La imagen del QR (PNG en base64) dentro del multipart que devuelve PayPal; si llega sola, tal cual."""
    m = re.search(r"boundary=([^;]+)", content_type or "")
    text = raw.decode("ascii", errors="ignore")
    if m:
        for part in text.split("--" + m.group(1).strip('"')):
            if 'name="image"' in part and "\r\n\r\n" in part:
                return re.sub(r"\s+", "", part.split("\r\n\r\n", 1)[1])
        return None
    b64 = re.sub(r"\s+", "", text)
    return b64 if re.fullmatch(r"[A-Za-z0-9+/=]+", b64 or "-") else None


@router.get("/api/owner/companies/{company_id}/invoices/{invoice_id}/qr")
def invoice_qr(company_id: int, invoice_id: int, user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    """El QR para pagar esta factura en PayPal (cobro en persona). No cambia nada."""
    _owner_only(company_id, user, session)
    inv = _open_invoice(session, company_id, invoice_id)
    _connect(session, company_id)        # las llaves de ESTE libro (como el recordatorio)
    try:
        with httpx.Client(base_url=paypal._BASES[paypal.environment()], timeout=30) as http:
            r = http.post(f"/v2/invoicing/invoices/{inv.paypal_invoice_id}/generate-qr-code",
                          headers={"Authorization": f"Bearer {paypal._access_token(http)}",
                                   "Content-Type": "application/json"},
                          json={"width": 300, "height": 300, "action": "pay"})
    except (httpx.HTTPError, paypal.PayPalError):
        raise HTTPException(status_code=502, detail={"code": "paypal_network", "field": None})
    png = parse_qr(r.headers.get("content-type", ""), r.content) if r.is_success else None
    if not png:
        paypal.log.warning("[paypal] QR %s: HTTP %s", inv.paypal_invoice_id, r.status_code)
        raise HTTPException(status_code=502, detail={"code": "paypal_qr_failed", "field": None})
    return {"png_base64": png}


# ── 4. Recordatorios automáticos (reglas de la cuenta de PayPal) ────────────────────────────────

AUTO_MAX = 7          # ✅ sandbox 3-nov: días 1–7; repeticiones después del vencimiento 1–7 (antes: siempre 1)


def _auto_read() -> dict:
    """Las dos reglas como las tiene PayPal (o None si la cuenta nunca las configuró)."""
    cfg = {c.get("type"): c for c in (paypal.api("GET", "/v2/invoicing/reminders").get("configurations") or [])}

    def one(c: Optional[dict]) -> Optional[dict]:
        if not c:
            return None
        return {"id": c.get("id"), "active": c.get("status") == "ACTIVE",
                "days": int((c.get("interval") or {}).get("value") or 1), "times": int(c.get("repetition") or 1)}
    return {"before": one(cfg.get("BEFORE_DUE")), "after": one(cfg.get("AFTER_DUE"))}


@router.get("/api/owner/companies/{company_id}/auto-reminders")
def get_auto_reminders(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Las reglas de recordatorios automáticos de la cuenta de PayPal de este libro. Solo lee."""
    owned_company(company_id, user, session)
    _connect(session, company_id)
    try:
        return _auto_read()
    except paypal.PayPalError as e:
        _fail(e)


class AutoRule(BaseModel):
    active: bool
    days: int
    times: int = 1


class AutoIn(BaseModel):
    before: AutoRule
    after: AutoRule


@router.put("/api/owner/companies/{company_id}/auto-reminders")
def set_auto_reminders(company_id: int, payload: AutoIn, user: User = Depends(current_user),
                       session: Session = Depends(get_session)):
    """El dueño cambia las reglas. Si la cuenta nunca las tuvo, se crean primero (PayPal las crea inactivas)."""
    _owner_only(company_id, user, session)
    want = {"BEFORE_DUE": payload.before, "AFTER_DUE": payload.after}
    for typ, r in want.items():
        if not 1 <= r.days <= AUTO_MAX or not 1 <= r.times <= (1 if typ == "BEFORE_DUE" else AUTO_MAX):
            raise HTTPException(status_code=422, detail="bad_auto_rule")
    _connect(session, company_id)
    try:
        have = _auto_read()
        if not have["before"] or not have["after"]:
            paypal.api("POST", "/v2/invoicing/setup-reminders", json={})
            have = _auto_read()
        for typ, key in (("BEFORE_DUE", "before"), ("AFTER_DUE", "after")):
            r, cur = want[typ], have[key]
            times = 1 if typ == "BEFORE_DUE" else r.times
            if (cur["days"], cur["times"]) != (r.days, times):
                paypal.api("PUT", f"/v2/invoicing/reminders/{cur['id']}",
                           json={"type": typ, "interval": {"unit": "DAY", "value": r.days}, "repetition": times})
            if cur["active"] != r.active:           # el PUT ignora el estado: se activa y se desactiva aparte
                paypal.api("POST", f"/v2/invoicing/reminders/{cur['id']}/{'resume' if r.active else 'suspend'}", json={})
        return _auto_read()
    except paypal.PayPalError as e:
        _fail(e)


@router.post("/api/owner/companies/{company_id}/invoices/{invoice_id}/stop-auto-reminders")
def stop_auto_reminders(company_id: int, invoice_id: int, user: User = Depends(current_user),
                        session: Session = Depends(get_session)):
    """El dueño detiene los recordatorios AUTOMÁTICOS de UNA factura (p. ej. el cliente prometió pagar el viernes). 5-nov.
    `POST …/cancel-reminders` (✅ sandbox): irreversible; PayPal los programa ~30 s después de enviar la factura (con las
    reglas activas); si no hay ninguno programado responde 422 en `id` (AUTOMATIC_REMINDERS_CANCELLATION_NOT_APPLICABLE)."""
    _owner_only(company_id, user, session)
    inv = _open_invoice(session, company_id, invoice_id)
    if inv.auto_reminders_stopped_at:
        raise HTTPException(status_code=409, detail="auto_reminders_already_stopped")
    _connect(session, company_id)
    try:
        paypal.api("POST", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}/cancel-reminders", json={})
    except paypal.PayPalError as e:
        if e.code == "UNPROCESSABLE_ENTITY" and getattr(e, "field", None) == "id":
            raise HTTPException(status_code=409, detail="no_auto_reminders")
        _fail(e)
    inv.auto_reminders_stopped_at = _utc_naive()
    session.add(inv)
    session.commit()
    return _view(session, company_id)
