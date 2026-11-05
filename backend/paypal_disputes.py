"""
DISPUTAS DE PAYPAL (FASE 16, área PayPal / portal del dueño · 3-nov-2026).

Un cliente abre una disputa en PayPal contra un cobro del negocio ("no recibí el servicio", "no lo autoricé"…). Aquí:

  1. SE LEEN de PayPal (`GET /v1/customer/disputes` y su detalle) y se guarda una copia (`PayPalDispute`): el portal y
     la visita la muestran sin llamar a PayPal ni a la IA cada vez. Se refresca con "Actualizar" y con el aviso
     `CUSTOMER.DISPUTE.*` del webhook. La disputa se liga a su factura por el id del cobro (`Invoice.payment_id`) o por
     el número de factura.
  2. LO QUE ESTÁ EN DISPUTA SE DECLARA (regla 4): el total de las abiertas lo suma SQL y sale arriba en Cobros. El dinero
     que de verdad se mueve (contracargo, reversión, devolución) entra al libro por la sincronización de PayPal
     (`paypal.rows_for`), nunca desde aquí.
  3. LA IA RESUME el caso en lenguaje sencillo (Nemotron): qué reclama el cliente, en qué etapa está y qué opciones da
     PayPal. NO decide ni recomienda: explica. No ve el nombre del cliente, ni correos, ni teléfonos, ni montos (el
     código los tapa antes de mandarle los mensajes) y el código rechaza un resumen con cifras. Se guarda por versión
     de la disputa (`update_time` de PayPal): la visita lo muestra sin gastar créditos.
  4. DECIDE EL DUEÑO: aceptar la reclamación (PayPal le devuelve el dinero al cliente; con confirmación en pantalla) o
     escribirle al cliente (solo en la etapa INQUIRY, regla de PayPal). Las pruebas con archivos se suben en PayPal.
  5. SOLO EN SANDBOX: simular lo que haría PayPal (✅ especificación oficial: `require-evidence` y `adjudicate` son
     "for sandbox use only"), para probar el ciclo completo y para el video.

✅ Esquema oficial `customer_disputes_v1.json` (github.com/paypal/paypal-rest-api-specifications), leído el 3-nov.
"""

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

import agent_calls
import nemotron
import paypal
from auth import client_writable_company, current_user, owned_company
from db import get_session
from invoices import PAID_ANY, _connect, _fail, _owner_only
from models import Company, Invoice, InvoiceCustomer, PayPalConnection, PayPalDispute, User

router = APIRouter()

CLOSED = ("RESOLVED",)
SANDBOX_ACTIONS = {                       # lo que PayPal haría por su cuenta; solo en sandbox (ver arriba)
    "escalate": ("escalate", None),
    "seller_evidence": ("require-evidence", {"action": "SELLER_EVIDENCE"}),
    "buyer_favor": ("adjudicate", {"adjudication_outcome": "BUYER_FAVOR"}),
    "seller_favor": ("adjudicate", {"adjudication_outcome": "SELLER_FAVOR"}),
}


def _utc_naive(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


# ── 1. Leer de PayPal y guardar la copia ────────────────────────────────────────────────────────

def _invoice_for(session: Session, company_id: int, tx: dict) -> Optional[int]:
    """La factura del cobro disputado: por el id del cobro y, si no, por el número de factura."""
    ref, number = tx.get("seller_transaction_id"), tx.get("invoice_number")
    q = select(Invoice.id).where(Invoice.company_id == company_id)
    if ref:
        hit = session.exec(q.where(Invoice.payment_id == ref)).first()
        if hit:
            return hit
    if number:   # ✅ sandbox 3-nov: PayPal manda aquí el id de SU factura (INV2-…), no nuestro número
        return session.exec(q.where((Invoice.paypal_invoice_id == str(number)) | (Invoice.number == str(number)))).first()
    return None


def _detail(d: dict) -> dict:
    """Lo que la pantalla necesita del detalle de PayPal (no se guarda el resto: ni correos ni datos del comprador)."""
    opts = d.get("allowed_response_options") or {}
    return {
        "messages": [{"by": m.get("posted_by"), "at": m.get("time_posted"), "text": (m.get("content") or "")[:2000]}
                     for m in (d.get("messages") or [])][-20:],
        "accept_types": ((opts.get("accept_claim") or {}).get("accept_claim_types") or []),
        "offer_types": ((opts.get("make_offer") or {}).get("offer_types") or []),
        "actions": sorted({(lk.get("rel") or "") for lk in (d.get("links") or [])} - {"", "self"}),
        "channel": d.get("dispute_channel"),
        "buyer_response_due": d.get("buyer_response_due_date"),
    }


def apply(session: Session, company_id: int, d: dict) -> PayPalDispute:
    """Guarda (o actualiza) la copia de UNA disputa a partir de su detalle de PayPal."""
    did = d.get("dispute_id") or ""
    row = session.exec(select(PayPalDispute).where(PayPalDispute.company_id == company_id)
                       .where(PayPalDispute.dispute_id == did)).first() or PayPalDispute(
        company_id=company_id, dispute_id=did, reason="OTHER", status="OTHER", amount=Decimal("0"))
    tx = (d.get("disputed_transactions") or [{}])[0] or {}
    amt = d.get("dispute_amount") or {}
    row.transaction_ref = tx.get("seller_transaction_id") or row.transaction_ref
    row.invoice_id = _invoice_for(session, company_id, tx) or row.invoice_id
    row.reason = d.get("reason") or row.reason
    row.status = d.get("status") or row.status
    row.stage = d.get("dispute_life_cycle_stage") or row.stage
    row.outcome = (d.get("dispute_outcome") or {}).get("outcome_code") or row.outcome
    row.amount = Decimal(str(amt.get("value") or row.amount or "0"))
    row.currency = amt.get("currency_code") or row.currency or "USD"
    row.opened_at = _utc_naive(d.get("create_time")) or row.opened_at
    row.seller_response_due = _utc_naive(d.get("seller_response_due_date"))
    row.pp_updated_at = d.get("update_time") or row.pp_updated_at
    row.detail = _detail(d)
    row.synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(row)
    return row


def _reread_invoice(session: Session, company_id: int, row: PayPalDispute) -> None:
    """Una disputa resuelta puede devolver el dinero: la factura pasa a REFUNDED en PayPal (✅ sandbox 3-nov). Cobros
    solo consulta las facturas abiertas, así que aquí se relee la factura ligada. El dinero entra al libro por la
    sincronización de PayPal (reversión o reembolso), no desde aquí."""
    if row.status not in CLOSED or not row.invoice_id:
        return
    inv = session.get(Invoice, row.invoice_id)
    if inv is None or not inv.paypal_invoice_id:
        return
    state = paypal.api("GET", f"/v2/invoicing/invoices/{inv.paypal_invoice_id}")
    if state.get("status") and state["status"] != inv.status:
        inv.status = state["status"]
        session.add(inv)


def sync(session: Session, company_id: int) -> int:
    """Lee las disputas de PayPal (la conexión ya elegida con `paypal.use_connection`) y refresca las que cambiaron.
    Devuelve cuántas se actualizaron. Pide el detalle solo si `update_time` cambió."""
    known = {r.dispute_id: r.pp_updated_at for r in session.exec(
        select(PayPalDispute).where(PayPalDispute.company_id == company_id)).all()}
    changed, token = 0, None
    for _ in range(20):                                   # 20 páginas × 50 = 1.000 disputas como máximo
        params = {"page_size": 50, **({"next_page_token": token} if token else {})}
        page = paypal.api("GET", "/v1/customer/disputes", params=params)
        for item in page.get("items") or []:
            did = item.get("dispute_id")
            if not did or known.get(did) == item.get("update_time"):
                continue
            apply(session, company_id, paypal.api("GET", f"/v1/customer/disputes/{did}"))
            changed += 1
        nxt = next((lk.get("href") for lk in page.get("links") or [] if lk.get("rel") == "next"), None)
        m = re.search(r"next_page_token=([^&]+)", nxt or "")
        if not m:
            break
        token = m.group(1)
    # Las resueltas cuya factura todavía figura como pagada: PayPal pudo haberla devuelto (una lectura por factura).
    session.flush()
    for row in session.exec(select(PayPalDispute).join(Invoice, Invoice.id == PayPalDispute.invoice_id)
                            .where(PayPalDispute.company_id == company_id).where(PayPalDispute.status.in_(CLOSED))
                            .where(Invoice.status.in_(PAID_ANY))).all():
        _reread_invoice(session, company_id, row)
    return changed


# ── La vista del portal ─────────────────────────────────────────────────────────────────────────

def view(session: Session, company_id: int, lang: str = "en") -> dict:
    rows = session.exec(select(PayPalDispute).where(PayPalDispute.company_id == company_id)).all()
    invs = {i.id: i for i in session.exec(select(Invoice).where(Invoice.company_id == company_id)
                                          .where(Invoice.id.in_([r.invoice_id for r in rows if r.invoice_id]))).all()}
    custs = {c.id: c for c in session.exec(select(InvoiceCustomer).where(InvoiceCustomer.id.in_(
        [i.customer_id for i in invs.values() if i.customer_id]))).all()}
    # Lo que está en disputa lo suma SQL (regla 1), y se declara aunque el dinero no se haya movido todavía (regla 4).
    held, n_open = session.exec(select(func.coalesce(func.sum(PayPalDispute.amount), 0), func.count())
                                .where(PayPalDispute.company_id == company_id)
                                .where(PayPalDispute.status.notin_(CLOSED))).one()
    out = []
    for r in sorted(rows, key=lambda r: (r.status in CLOSED, r.seller_response_due or datetime.max, -r.id)):
        inv = invs.get(r.invoice_id)
        c = custs.get(inv.customer_id) if inv and inv.customer_id else None
        s = r.summary if (r.summary and r.summary.get("for") == r.pp_updated_at and r.summary.get("lang") == lang) else None
        actions = (r.detail or {}).get("actions") or []
        out.append({
            "id": r.id, "dispute_id": r.dispute_id, "reason": r.reason, "status": r.status, "stage": r.stage,
            "outcome": r.outcome, "amount": str(r.amount), "currency": r.currency,
            "opened_at": r.opened_at.isoformat() + "Z" if r.opened_at else None,
            "seller_response_due": r.seller_response_due.isoformat() + "Z" if r.seller_response_due else None,
            "invoice": {"id": inv.id, "number": inv.number, "concept": inv.concept,
                        "customer": c.name if c else None} if inv else None,
            "messages": (r.detail or {}).get("messages") or [],
            "can_accept": r.status not in CLOSED and "accept_claim" in actions,
            "can_message": r.status not in CLOSED and r.stage == "INQUIRY",
            "can_offer": r.status not in CLOSED and "make_offer" in actions,                 # 5-nov
            "can_evidence": r.status not in CLOSED and "provide_evidence" in actions,
            "sandbox_actions": ([k for k, (rel, _) in SANDBOX_ACTIONS.items() if rel.replace("-", "_") in actions]
                                if paypal.environment() == "sandbox" else []),
            "summary": {k: s[k] for k in ("summary", "customer_says", "options")} if s else None,
            "resolution_center": "https://www.sandbox.paypal.com/resolutioncenter" if paypal.environment() == "sandbox"
                                 else "https://www.paypal.com/resolutioncenter",
        })
    return {"disputes": out, "summary": {"open": int(n_open), "in_dispute": str(held)}}


def _row(session: Session, company_id: int, row_id: int) -> PayPalDispute:
    r = session.get(PayPalDispute, row_id)
    if r is None or r.company_id != company_id:
        raise HTTPException(status_code=404, detail="dispute not found")
    return r


def _lang(lang: Optional[str]) -> str:
    return "es" if (lang or "").startswith("es") else "en"


@router.get("/api/owner/companies/{company_id}/disputes")
def list_disputes(company_id: int, lang: str = "en", user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    owned_company(company_id, user, session)
    return view(session, company_id, _lang(lang))


@router.post("/api/owner/companies/{company_id}/disputes/refresh")
def refresh_disputes(company_id: int, lang: str = "en", user: User = Depends(current_user),
                     session: Session = Depends(get_session)):
    """Lee de PayPal. La puede pedir el dueño o el bookkeeper (solo lee); un VIEWER no."""
    client_writable_company(company_id, user, session)
    _connect(session, company_id)
    try:
        sync(session, company_id)
    except paypal.PayPalError as e:
        _fail(e)
    session.commit()
    return view(session, company_id, _lang(lang))


# ── 3. El resumen de la IA ──────────────────────────────────────────────────────────────────────

class SummaryOut(BaseModel):
    summary: str
    customer_says: str
    options: str


_SYSTEM = """You explain a PayPal dispute to a small-business owner in plain language. You do NOT decide anything and
you do NOT recommend an option: you explain, and the owner decides.
You receive the dispute's REASON code, its STAGE and STATUS, the response OPTIONS PayPal allows, and the MESSAGES
(already redacted: [customer], [email], [phone], [link] and [amount] replace private data).
Return ONLY JSON: {"summary": "...", "customer_says": "...", "options": "..."}
- summary: 1 or 2 sentences — what kind of dispute this is and where it stands (stage and status in plain words).
- customer_says: 1 or 2 sentences — what the customer claims, from the messages; if there are none, say that the
  customer has not written a message and only the reason is known.
- options: 2 or 3 sentences — what each allowed option means for the owner (e.g. accepting the claim means PayPal
  refunds the customer from the business's PayPal balance; replying means writing to the customer; evidence is
  uploaded in PayPal's Resolution Center). Neutral: never say which one to choose.
- NEVER write a digit, amount, date, number of days, name, email or currency symbol. Plain text, no markdown.
- Write in the requested LANGUAGE ("es" = Spanish, "en" = English)."""

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"https?://\S+|www\.\S+")
_PHONE = re.compile(r"\+?\(?\d[\d\s().-]{6,}\d")
_MONEY = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|usd|dollars?|dólares?)")
_DIGITS = re.compile(r"[0-9$€£@]")


def redact(text: str, names: list[str]) -> str:
    """Tapa lo privado ANTES de que el texto llegue a la IA (la IA no ve nombres, correos, teléfonos ni montos)."""
    t = _EMAIL.sub("[email]", text)
    t = _URL.sub("[link]", t)
    t = _MONEY.sub("[amount]", t)
    t = _PHONE.sub("[phone]", t)
    for n in sorted({w for name in names for w in (name or "").split() if len(w) > 1}, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(n)}\b", "[customer]", t, flags=re.IGNORECASE)
    return t


def check_summary(raw: dict) -> Optional[SummaryOut]:
    try:
        s = SummaryOut.model_validate(raw if isinstance(raw, dict) else {})
    except Exception:
        return None
    parts = [s.summary.strip(), s.customer_says.strip(), s.options.strip()]
    if not all(parts) or any(len(p) > 800 for p in parts) or any(_DIGITS.search(p) for p in parts):
        return None
    return SummaryOut(summary=parts[0], customer_says=parts[1], options=parts[2])


def ai_input(session: Session, company: Company, r: PayPalDispute, lang: str) -> str:
    inv = session.get(Invoice, r.invoice_id) if r.invoice_id else None
    c = session.get(InvoiceCustomer, inv.customer_id) if inv and inv.customer_id else None
    names = [c.name if c else "", company.name]
    d = r.detail or {}
    opts = (["accept_claim (" + ", ".join(d.get("accept_types") or ["REFUND"]) + ")"] if "accept_claim" in d.get("actions", []) else [])
    opts += ["reply_to_customer"] if r.stage == "INQUIRY" and r.status not in CLOSED else []
    opts += ["provide_evidence_in_resolution_center"] if "provide_evidence" in d.get("actions", []) else []
    msgs = [f"{m.get('by') or 'UNKNOWN'}: {redact(m.get('text') or '', names)}" for m in d.get("messages") or []]
    return (f"LANGUAGE: {lang}\nREASON: {r.reason}\nSTAGE: {r.stage or 'UNKNOWN'}\nSTATUS: {r.status}\n"
            f"OUTCOME: {r.outcome or 'NONE'}\nOPTIONS: {', '.join(opts) or 'none (nothing to do now)'}\n"
            f"MESSAGES:\n" + ("\n".join(msgs) if msgs else "(none)"))


class SummaryIn(BaseModel):
    lang: str = "en"


@router.post("/api/owner/companies/{company_id}/disputes/{dispute_id}/summary")
def summarize(company_id: int, dispute_id: int, payload: SummaryIn, user: User = Depends(current_user),
              session: Session = Depends(get_session)):
    """La IA resume la disputa (una vez por versión de la disputa y por idioma; después se lee guardado)."""
    company = _owner_only(company_id, user, session)
    r = _row(session, company_id, dispute_id)
    lang = _lang(payload.lang)
    if r.summary and r.summary.get("for") == r.pp_updated_at and r.summary.get("lang") == lang:
        return view(session, company_id, lang)
    try:
        agent_calls.check_cap(session, company)
    except agent_calls.CapReached:
        raise HTTPException(status_code=429, detail="ai_cap_reached")
    user_msg = ai_input(session, company, r, lang)
    out = None
    for attempt in range(2):                 # UN reintento si no pasa la validación o se queda sin tokens
        try:
            reply = nemotron.chat_json(_SYSTEM, user_msg, SummaryOut, max_tokens=1200, reasoning=False, temperature=0.2)
        except nemotron.NemotronUnavailable:
            raise HTTPException(status_code=503, detail="ai_unavailable")
        except nemotron.NemotronError as e:
            agent_calls.record(session, company, "dispute_summary", e.usage, outcome=e.code, user_id=user.id,
                               model=nemotron.model_name())
            session.commit()
            if e.code != "out_of_tokens" or attempt == 1:
                raise HTTPException(status_code=502, detail="ai_failed")
            continue
        out = check_summary(reply.data)
        agent_calls.record(session, company, "dispute_summary", reply.usage, user_id=user.id,
                           items_out=1, items_valid=int(out is not None))
        session.commit()
        if out is not None:
            break
    if out is None:
        raise HTTPException(status_code=502, detail="ai_failed")
    r.summary = {"for": r.pp_updated_at, "lang": lang, **out.model_dump()}
    session.add(r)
    session.commit()
    return view(session, company_id, lang)


# ── 4. Lo que decide el dueño ───────────────────────────────────────────────────────────────────

def _post_input(path: str, body: dict) -> dict:
    """Una acción de disputa. ✅ Sandbox 3-nov: `escalate` solo acepta JSON (multipart → 415) y `send-message` acepta
    multipart con una parte `input`; la especificación no lo aclara. Se manda JSON y, si PayPal responde 415, multipart."""
    try:
        return paypal.api("POST", path, json=body)
    except paypal.PayPalError as e:
        if e.code != "UNSUPPORTED_MEDIA_TYPE":
            raise
    return paypal.api("POST", path, files={"input": (None, json.dumps(body), "application/json")})


def _reload(session: Session, company_id: int, r: PayPalDispute) -> None:
    apply(session, company_id, paypal.api("GET", f"/v1/customer/disputes/{r.dispute_id}"))
    session.commit()


class AcceptIn(BaseModel):
    note: str = ""
    lang: str = "en"


@router.post("/api/owner/companies/{company_id}/disputes/{dispute_id}/accept")
def accept_claim(company_id: int, dispute_id: int, payload: AcceptIn, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    """El dueño acepta la reclamación: PayPal le devuelve el dinero al cliente. La devolución entra al libro con la
    siguiente sincronización (como cualquier reembolso), no desde aquí."""
    _owner_only(company_id, user, session)
    r = _row(session, company_id, dispute_id)
    if r.status in CLOSED or "accept_claim" not in ((r.detail or {}).get("actions") or []):
        raise HTTPException(status_code=409, detail="dispute_not_actionable")
    _connect(session, company_id)
    types = (r.detail or {}).get("accept_types") or []
    body = {"note": (payload.note.strip() or "Accepted by the business owner.")[:2000],
            **({"accept_claim_type": "REFUND"} if not types or "REFUND" in types else {})}
    try:
        _post_input(f"/v1/customer/disputes/{r.dispute_id}/accept-claim", body)
        _reload(session, company_id, r)
    except paypal.PayPalError as e:
        _fail(e)
    return view(session, company_id, _lang(payload.lang))


class MessageIn(BaseModel):
    message: str
    lang: str = "en"


@router.post("/api/owner/companies/{company_id}/disputes/{dispute_id}/message")
def send_message(company_id: int, dispute_id: int, payload: MessageIn, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    """El dueño le escribe al cliente (PayPal solo lo permite en la etapa INQUIRY). El texto es suyo, no de la IA."""
    _owner_only(company_id, user, session)
    r = _row(session, company_id, dispute_id)
    text = payload.message.strip()
    if not text or len(text) > 2000:
        raise HTTPException(status_code=422, detail="bad_message")
    if r.status in CLOSED or r.stage != "INQUIRY":
        raise HTTPException(status_code=409, detail="dispute_not_actionable")
    _connect(session, company_id)
    try:
        _post_input(f"/v1/customer/disputes/{r.dispute_id}/send-message", {"message": text})
        _reload(session, company_id, r)
    except paypal.PayPalError as e:
        _fail(e)
    return view(session, company_id, _lang(payload.lang))


# ── Ofrecer un reembolso y subir pruebas (5-nov) ────────────────────────────────────────────────

class OfferIn(BaseModel):
    amount: str
    note: str = ""
    lang: str = "en"


@router.post("/api/owner/companies/{company_id}/disputes/{dispute_id}/offer")
def make_offer(company_id: int, dispute_id: int, payload: OfferIn, user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    """Ofrece un reembolso (p. ej. parcial) para cerrar la disputa (✅ especificación: solo en la etapa INQUIRY; si el
    cliente acepta, PayPal reembolsa solo). El reembolso entra al libro con la sincronización, como cualquier otro."""
    _owner_only(company_id, user, session)
    r = _row(session, company_id, dispute_id)
    if r.status in CLOSED or "make_offer" not in ((r.detail or {}).get("actions") or []):
        raise HTTPException(status_code=409, detail="dispute_not_actionable")
    try:
        amount = Decimal(payload.amount.strip().replace(",", ""))
    except Exception:
        raise HTTPException(status_code=422, detail="bad_amount")
    if amount <= 0 or amount > r.amount or amount != amount.quantize(Decimal("0.01")):
        raise HTTPException(status_code=422, detail="bad_amount")
    types = (r.detail or {}).get("offer_types") or ["REFUND"]
    _connect(session, company_id)
    try:
        _post_input(f"/v1/customer/disputes/{r.dispute_id}/make-offer", {
            "note": (payload.note.strip() or "Offer from the business owner.")[:2000],
            "offer_amount": {"currency_code": r.currency, "value": f"{amount:.2f}"},
            "offer_type": "REFUND" if "REFUND" in types else types[0]})
        _reload(session, company_id, r)
    except paypal.PayPalError as e:
        _fail(e)
    return view(session, company_id, _lang(payload.lang))


# Para un negocio de SERVICIOS, los tipos de prueba que tienen sentido (de los ~80 de la especificación oficial).
EVIDENCE_TYPES = ("PROOF_FOR_SOFTWARE_OR_SERVICE_DELIVERED", "PROOF_OF_RECEIPT_COPY", "COPY_OF_CONTRACT",
                  "PROOF_OF_REFUND_OUTSIDE_PAYPAL", "MERCHANT_RESPONSE", "PHOTOS_OF_SHIPPED_ITEM", "OTHER")
EVIDENCE_MAX = 10_000_000                       # 10 MB por archivo (✅ guía de PayPal: hasta 10 MB, 50 MB en total)
EVIDENCE_MIME = {"application/pdf", "image/png", "image/jpeg", "image/gif"}


@router.post("/api/owner/companies/{company_id}/disputes/{dispute_id}/evidence")
async def provide_evidence(company_id: int, dispute_id: int, evidence_type: Optional[str] = Form(None), notes: str = Form(""),
                           lang: str = Form("en"), file: Optional[UploadFile] = File(None),
                           user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Sube una prueba a PayPal (✅ especificación: multipart con `input` y `evidence-file`; solo si PayPal anuncia
    `provide_evidence`). Un archivo opcional (PDF o imagen, hasta 10 MB) y notas. ContaBu no guarda el archivo."""
    _owner_only(company_id, user, session)
    r = _row(session, company_id, dispute_id)
    if r.status in CLOSED or "provide_evidence" not in ((r.detail or {}).get("actions") or []):
        raise HTTPException(status_code=409, detail="dispute_not_actionable")
    if evidence_type not in EVIDENCE_TYPES:
        raise HTTPException(status_code=422, detail="bad_evidence_type")
    notes = (notes or "").strip()[:2000]
    data = await file.read(EVIDENCE_MAX + 1) if file is not None else b""
    if file is not None and (len(data) > EVIDENCE_MAX or (file.content_type or "") not in EVIDENCE_MIME):
        raise HTTPException(status_code=422, detail="bad_evidence_file")
    if not notes and not data:
        raise HTTPException(status_code=422, detail="evidence_empty")
    name = (file.filename or "evidence")[:100] if data else None
    ev = {"evidence_type": evidence_type, **({"notes": notes} if notes else {}), **({"documents": [{"name": name}]} if name else {})}
    files = {"input": (None, json.dumps({"evidences": [ev]}), "application/json"),
             **({"evidence-file": (name, data, file.content_type)} if data else {})}
    _connect(session, company_id)
    try:
        paypal.api("POST", f"/v1/customer/disputes/{r.dispute_id}/provide-evidence", files=files)
        _reload(session, company_id, r)
    except paypal.PayPalError as e:
        _fail(e)
    return view(session, company_id, _lang(lang))


# ── 5. Solo sandbox: lo que haría PayPal ────────────────────────────────────────────────────────

class SimulateIn(BaseModel):
    action: str
    lang: str = "en"


@router.post("/api/owner/companies/{company_id}/disputes/{dispute_id}/simulate")
def simulate(company_id: int, dispute_id: int, payload: SimulateIn, user: User = Depends(current_user),
             session: Session = Depends(get_session)):
    """SOLO SANDBOX: escalar a reclamación, pedir pruebas al negocio, o decidir a favor de una parte."""
    _owner_only(company_id, user, session)
    if paypal.environment() != "sandbox" or payload.action not in SANDBOX_ACTIONS:
        raise HTTPException(status_code=404, detail="not available")
    r = _row(session, company_id, dispute_id)
    _connect(session, company_id)
    rel, body = SANDBOX_ACTIONS[payload.action]
    try:
        if body is None:
            _post_input(f"/v1/customer/disputes/{r.dispute_id}/{rel}", {"note": "Escalated in the sandbox."})   # JSON ✅
        else:
            paypal.api("POST", f"/v1/customer/disputes/{r.dispute_id}/{rel}", json=body)
        _reload(session, company_id, r)
    except paypal.PayPalError as e:
        _fail(e)
    return view(session, company_id, _lang(payload.lang))


def on_webhook(session: Session, conn: PayPalConnection, event: dict) -> None:
    """Aviso `CUSTOMER.DISPUTE.*`: no se le cree al aviso; se vuelve a leer la disputa de PayPal."""
    did = (event.get("resource") or {}).get("dispute_id")
    paypal.use_connection(conn)
    if did:
        row = apply(session, conn.company_id, paypal.api("GET", f"/v1/customer/disputes/{did}"))
        _reread_invoice(session, conn.company_id, row)
    else:
        sync(session, conn.company_id)
    session.commit()
