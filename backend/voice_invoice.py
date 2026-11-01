"""
COBRO POR VOZ (FASE 16 Paso 6, área PayPal / portal del dueño · 1-nov-2026).

El dueño dicta ("Factúrale a Acme 500 dólares por la remodelación de la cocina, vence en 15 días"), el navegador lo
transcribe (Web Speech API; si no, el dictado del teclado en el mismo campo) y este módulo llena EL MISMO formulario de
Cobros. Nada se envía: el dueño revisa y confirma en la pantalla de revisión de siempre (`invoices.py`).

La IA NO decide ni calcula — solo extrae (regla de oro):
  1. El CÓDIGO busca al cliente en la lista guardada (sin acentos ni mayúsculas, palabra por palabra) y CAMBIA su nombre
     por [CUSTOMER] antes de mandar el texto. Si no reconoce a ningún cliente, NO llama a la IA: pide elegirlo.
     Decisión de Jose (1-nov): Nemotron ve el dictado MENOS el cliente — una excepción acotada a la regla del 8-oct
     ("nunca montos ni nombres"): solo el texto que el dueño dicta para su factura, nunca datos del libro.
  2. Nemotron (Nebius) devuelve JSON estricto: monto, fecha o "en N días", concepto. Con topes y costo registrados
     (`agent_calls`, agente "invoice_voice").
  3. El CÓDIGO valida todo: monto (Decimal, > 0, 2 decimales, tope), fecha (no pasada; sin fecha → 15 días, avisado),
     concepto (no vacío, ≤ 200). Lo dudoso vuelve como aviso, nunca se inventa. El correo nunca se dicta: sale del
     cliente guardado.
"""

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

import agent_calls
import nemotron
from db import get_session
from invoices import MAX_AMOUNT, _customer_out, _owner_only
from models import Company, InvoiceCustomer, User
from auth import current_user

router = APIRouter()

MAX_TEXT = 500
DEFAULT_DUE_DAYS = 15
PLACEHOLDER = "[CUSTOMER]"
# Palabras de un nombre que no identifican a nadie (no se usan para reconocer al cliente).
_GENERIC = {"llc", "inc", "corp", "co", "company", "ltd", "the", "and", "de", "la", "el", "los", "las", "y", "sa",
            "group", "services", "service", "construction", "cleaning", "studio", "shop"}


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _norm(name)) if len(t) >= 3 and t not in _GENERIC]


def match_customers(text: str, customers: list[InvoiceCustomer]) -> list[InvoiceCustomer]:
    """Los clientes guardados que el dictado nombra (alguna palabra propia de su nombre, completa)."""
    words = set(re.findall(r"[a-z0-9]+", _norm(text)))
    return [c for c in customers if any(t in words for t in _tokens(c.name))]


def mask(text: str, customers: list[InvoiceCustomer]) -> str:
    """Cambia por [CUSTOMER] cada palabra propia del nombre de esos clientes (con o sin acentos/mayúsculas)."""
    toks = {t for c in customers for t in _tokens(c.name)}
    out = []
    for w in re.split(r"(\W+)", text):
        out.append(PLACEHOLDER if w and _norm(w) in toks else w)
    p = re.escape(PLACEHOLDER)
    return re.sub(rf"(?:{p}\W*)+{p}", PLACEHOLDER, "".join(out))   # "[C] [C]" → "[C]"


class VoiceIn(BaseModel):
    text: str
    customer_id: Optional[int] = None     # el que eligió el dueño si el dictado no lo dejaba claro
    lang: str = "en"


class VoiceOut(BaseModel):
    """Lo que Nemotron debe devolver (JSON estricto). Todo opcional: lo que no se dijo, no se inventa."""
    amount: Optional[str] = None          # "500.00" — solo cifras y punto decimal
    due_date: Optional[str] = None        # "YYYY-MM-DD" si se dijo una fecha
    due_in_days: Optional[int] = None     # si se dijo "en 15 días", "next week" (7)…
    concept: Optional[str] = None         # el concepto, corto, en el idioma del dictado


_SYSTEM = """You extract invoice fields from a short dictation by a small-business owner. The customer's name was
replaced by [CUSTOMER]: never guess it. Return ONLY JSON with these keys:
- "amount": the invoice total as digits with a dot for decimals (e.g. "500.00", "1250.5"); words like "quinientos" or
  "two thousand" become digits; null if no amount was said. Never add tax or compute anything else.
- "due_date": "YYYY-MM-DD" if a calendar date was said (use TODAY to resolve "the 15th", "next Friday"); else null.
- "due_in_days": an integer if a relative term was said ("in 15 days", "en una semana" = 7); else null.
- "concept": what the invoice is for, short (max 80 characters), in the dictation's language, no amounts or dates;
  null if not said.
Do not invent anything that was not said."""


def _check(raw: dict, today: date) -> tuple[dict, list[str]]:
    """El código valida lo que extrajo la IA. Devuelve (campos limpios, avisos). Nunca inventa un valor."""
    warnings: list[str] = []
    out: dict = {"amount": None, "due_date": None, "concept": None}
    try:
        v = VoiceOut.model_validate(raw if isinstance(raw, dict) else {})
    except Exception:
        return out, ["unreadable"]
    if v.amount:
        try:
            a = Decimal(str(v.amount).replace(",", "").replace("$", "").strip())
            if a <= 0 or a > MAX_AMOUNT or a != a.quantize(Decimal("0.01")):
                warnings.append("amount_invalid")
            else:
                out["amount"] = f"{a:.2f}"
        except InvalidOperation:
            warnings.append("amount_invalid")
    else:
        warnings.append("amount_missing")
    due = None
    if v.due_date:
        try:
            due = date.fromisoformat(v.due_date[:10])
        except ValueError:
            warnings.append("due_invalid")
    elif v.due_in_days is not None and 0 <= v.due_in_days <= 365:
        due = today + timedelta(days=v.due_in_days)
    if due is not None and due < today:
        warnings.append("due_past")
        due = None
    if due is None:
        due = today + timedelta(days=DEFAULT_DUE_DAYS)
        warnings.append("due_default")
    out["due_date"] = due.isoformat()
    concept = (v.concept or "").strip().replace(PLACEHOLDER, "").strip(" ,.-")
    if concept:
        out["concept"] = concept[:200]
    else:
        warnings.append("concept_missing")
    return out, warnings


@router.post("/api/owner/companies/{company_id}/invoices/voice")
def parse_voice(company_id: int, payload: VoiceIn, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Del dictado al formulario (sin enviar nada). Solo el dueño."""
    company: Company = _owner_only(company_id, user, session)
    text = (payload.text or "").strip()
    if not text or len(text) > MAX_TEXT:
        raise HTTPException(status_code=422, detail="text_required")
    customers = session.exec(select(InvoiceCustomer).where(InvoiceCustomer.company_id == company_id)).all()
    chosen = next((c for c in customers if c.id == payload.customer_id), None) if payload.customer_id else None
    named = match_customers(text, customers)
    if chosen is None and len(named) == 1:
        chosen = named[0]
    if chosen is None and not named:
        # No se reconoce a ningún cliente: NO se llama a la IA (podría llevarse un nombre). Que lo elija el dueño.
        return {"needs_customer": True, "candidates": [], "customer": None, "fields": None, "warnings": []}
    try:
        agent_calls.check_cap(session, company)
    except agent_calls.CapReached:
        raise HTTPException(status_code=429, detail="ai_cap_reached")
    masked = mask(text, named + ([chosen] if chosen else []))
    today = date.today()
    user_msg = f"TODAY: {today.isoformat()}\nLANGUAGE: {payload.lang}\nDICTATION: {masked}"
    try:
        reply = nemotron.chat_json(_SYSTEM, user_msg, VoiceOut, max_tokens=400, reasoning=False)
    except nemotron.NemotronUnavailable:
        raise HTTPException(status_code=503, detail="ai_unavailable")
    except nemotron.NemotronError as e:
        agent_calls.record(session, company, "invoice_voice", e.usage, outcome=e.code, user_id=user.id,
                           model=nemotron.model_name())
        session.commit()
        raise HTTPException(status_code=502, detail="ai_failed")
    fields, warnings = _check(reply.data, today)
    agent_calls.record(session, company, "invoice_voice", reply.usage, user_id=user.id,
                       items_out=1, items_valid=int(not warnings or warnings == ["due_default"]))
    session.commit()
    return {"needs_customer": chosen is None, "candidates": [_customer_out(c) for c in named] if chosen is None else [],
            "customer": _customer_out(chosen) if chosen else None, "fields": fields, "warnings": warnings,
            "sent_to_ai": masked}
