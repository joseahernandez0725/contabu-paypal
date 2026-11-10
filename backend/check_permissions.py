"""
FASE 13 Paso 3 — la matriz de permisos del rol CLIENT, comprobada contra TODAS las rutas.

**Por qué es un script y no una tabla en un documento:** una matriz escrita a mano queda vieja en la
primera ruta nueva. Esto recorre `app.routes` de verdad: el día que alguien agregue un endpoint sin
decidir el permiso del cliente, ESTO FALLA en vez de abrir un hueco en silencio.

Corre así (necesita `AUTH_DEV_MODE=true`, que activa la cabecera `X-Dev-Uid`):

    AUTH_DEV_MODE=true .venv/bin/python check_permissions.py

Crea sus propios datos de prueba (una firma, un libro, un bookkeeper y un cliente), prueba, y los
BORRA al final — no toca los libros reales.

Salida: un renglón por ruta, un resumen y **código de salida 1** si algo falló.
"""

import os
import sys

os.environ.setdefault("AUTH_DEV_MODE", "true")
os.environ.setdefault("PAYPAL_PAYOUTS", "true")   # los pagos a contratistas (4-nov): con el interruptor encendido se prueba el permiso real

# ── La prueba de permisos NUNCA sale a internet (3-oct). ──────────────────────────────────────────
# La pasada del bookkeeper SÍ atraviesa el permiso, y algunas rutas llaman servicios externos (Teller
# al vincular un banco, la IA al importar o proponer reglas). Con las claves del `.env` cargadas, eso
# era gastar crédito y mandar datos de prueba afuera en cada corrida. Se bloquea TODA conexión salvo
# la base de datos; las rutas que intenten salir fallan en local (y eso no es un 403: la prueba sigue
# midiendo solo permisos). Cubre también los servicios que se agreguen después, sin tocar esta lista.
import socket  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

def _allowed_hosts() -> set:
    hosts = {"127.0.0.1", "::1", "localhost"}
    from dotenv import load_dotenv  # el mismo .env que lee db.py
    load_dotenv()
    db_host = urlparse(os.getenv("DATABASE_URL", "")).hostname
    if db_host:
        hosts.add(db_host)
        try:
            hosts |= {ai[4][0] for ai in socket.getaddrinfo(db_host, None)}
        except OSError:
            pass
    return hosts

_ALLOWED = _allowed_hosts()
BLOCKED: set = set()
_real_connect = socket.socket.connect

def _local_only(self, address):
    host = address[0] if isinstance(address, tuple) else None   # None = socket UNIX (local)
    if host is not None and host not in _ALLOWED:
        BLOCKED.add(host)
        raise ConnectionRefusedError(f"check_permissions no sale a internet ({host})")
    return _real_connect(self, address)

socket.socket.connect = _local_only

from datetime import date, datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402
from typing import Optional  # noqa: E402

from sqlalchemy import func, text  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, delete, select  # noqa: E402

import auth  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import (AccountType, CategorizationChange, CategoryRule, CellFormat, Company,  # noqa: E402
                    CompanyCategory, CompanyMember,
                    Firm, FirmMember, FirmRole, LearnedMerchant, MonthlyInsight, PnlReport,
                    Role, SheetLabel, Transaction, TransactionLabel, User)

if not auth.AUTH_DEV_MODE:
    sys.exit("AUTH_DEV_MODE no está activo: corre con AUTH_DEV_MODE=true")

CLIENT_UID = "__permcheck_client__"
BOOK_UID = "__permcheck_bookkeeper__"

# ──────────────────────────────────────────────────────────────────────────────────────────────────
# LA LISTA BLANCA: lo único que un CLIENT puede alcanzar. Todo lo demás debe rechazarse.
# Claves = (método, plantilla de ruta) tal como FastAPI las registra.
# ──────────────────────────────────────────────────────────────────────────────────────────────────
CLIENT_ALLOWED: set[tuple[str, str]] = {
    # Decisión de Jose (30-sep-2026): el dueño NO ve el libro en construcción — ni el P&L, ni el
    # pivote, ni los movimientos categorizados, ni los reportes. Todo eso cambia el año entero; lo
    # ve cuando el bookkeeper se lo ENVÍA (Solicitud). Lo que queda es lo que el dueño APORTA.
    # 🔄 31-oct (Jose, FASE 16): el dueño tiene SU PORTAL (Resumen · Cuentas · Cobros) bajo /api/owner/.
    # Sigue sin ver el P&L ni transacciones ni el criterio del bookkeeper: su Resumen son totales por
    # categoría de los meses que el bookkeeper CERRÓ (un mes cerrado ya no cambia). Cada ruta /api/owner/
    # entra aquí con su razón, y `check_owner_portal()` prueba que no alcanza otro libro.
    ("GET", "/api/me"),                                            # quién soy y cuál es mi libro
    ("GET", "/api/companies/{company_id}"),                        # nombre de mi negocio
    ("POST", "/api/companies/{company_id}/transactions/upload"),   # "subir un extracto"
    ("GET", "/api/hello"),                                         # público, sin identidad
    ("GET", "/api/owner/companies/{company_id}"),                  # portada del portal: negocio, quién lo lleva, meses cerrados
    ("GET", "/api/owner/companies/{company_id}/accounts"),         # Cuentas: sus bancos y su PayPal (nombre, últimos 4, estado)
    ("POST", "/api/companies/{company_id}/paypal/authorize"),      # el enlace para iniciar sesión en PayPal (1-nov)
    ("POST", "/api/companies/{company_id}/paypal/credentials"),    # conectar con las llaves de SU app de sandbox (2-nov)
    # Vincular SU banco desde el portal (Jose, 1-nov): sus credenciales en Plaid, como subir su extracto. Reparar,
    # sincronizar y quitar un banco siguen siendo del bookkeeper.
    ("POST", "/api/companies/{company_id}/bank-connections/link-token"),
    ("POST", "/api/companies/{company_id}/bank-connections/check"),
    ("POST", "/api/companies/{company_id}/bank-connections"),
    # Tras subir SU estado de cuenta: ponerle la cuenta a lo que subió. Solo filas SIN cuenta (check_owner_portal).
    ("POST", "/api/companies/{company_id}/transactions/set-account"),
    ("POST", "/api/paypal/oauth"),                                 # la vuelta de PayPal: el `state` firmado dice el libro
    # Cobros (Paso 5): el dueño factura a SUS clientes. Crear y enviar son SOLO suyas (OWNER_ONLY, abajo).
    ("GET", "/api/owner/companies/{company_id}/customers"),        # su lista de clientes (para facturar y dictar)
    ("POST", "/api/owner/companies/{company_id}/customers"),       # agregar un cliente
    ("GET", "/api/owner/companies/{company_id}/invoices"),         # sus facturas y sus cifras
    ("POST", "/api/owner/companies/{company_id}/invoices"),        # crear un BORRADOR (no envía nada)
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/send"),  # enviarla, tras confirmar en pantalla
    ("POST", "/api/owner/companies/{company_id}/invoices/refresh"),  # preguntar a PayPal si ya se pagó
    ("POST", "/api/owner/companies/{company_id}/invoices/voice"),  # dictado → el formulario (no envía nada)
    ("POST", "/api/owner/companies/{company_id}/studio-ai"),       # un turno del asistente del Tablero (solo lee)
    # Recordatorios y QR (1-nov): el recordatorio oficial de PayPal, el borrador de un mensaje personal y el QR de pago.
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/remind"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/reminder-draft"),
    ("GET", "/api/owner/companies/{company_id}/invoices/{invoice_id}/qr"),
    # Disputas (3-nov): leer y refrescar; resumir, aceptar, escribir y simular (sandbox) son SOLO del dueño.
    ("GET", "/api/owner/companies/{company_id}/disputes"),            # sus disputas y lo que está en disputa
    ("POST", "/api/owner/companies/{company_id}/disputes/refresh"),   # leer de PayPal (solo lee)
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/summary"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/accept"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/message"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/simulate"),
    # Pagado o devuelto fuera de PayPal (3-nov): registrarlo es SOLO del dueño.
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/record-payment"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/record-refund"),
    # Cancelar una enviada y descartar un borrador (3-nov): SOLO el dueño.
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/cancel"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/discard"),
    # Recordatorios automáticos (3-nov): leer las reglas de PayPal; cambiarlas es SOLO del dueño.
    ("GET", "/api/owner/companies/{company_id}/auto-reminders"),
    ("PUT", "/api/owner/companies/{company_id}/auto-reminders"),
    # Los datos del negocio en sus facturas (4-nov): leer; guardarlos y pedir el número que sigue son SOLO del dueño.
    ("GET", "/api/owner/companies/{company_id}/invoice-profile"),
    ("PUT", "/api/owner/companies/{company_id}/invoice-profile"),
    ("GET", "/api/owner/companies/{company_id}/invoices/next-number"),
    # Facturas recurrentes (4-nov): verlas; programarlas y detenerlas son SOLO del dueño.
    ("GET", "/api/owner/companies/{company_id}/series"),
    ("POST", "/api/owner/companies/{company_id}/series"),
    ("POST", "/api/owner/companies/{company_id}/series/{series_id}/cancel"),
    # El logo de la factura (4-nov): subirlo y quitarlo son SOLO del dueño.
    ("POST", "/api/owner/companies/{company_id}/invoice-profile/logo"),
    ("DELETE", "/api/owner/companies/{company_id}/invoice-profile/logo"),
    # Pagos a contratistas (4-nov, detrás de PAYPAL_PAYOUTS): ver la lista; agregar, pagar y actualizar son SOLO del dueño.
    ("GET", "/api/owner/companies/{company_id}/contractors"),
    ("POST", "/api/owner/companies/{company_id}/contractors"),
    ("POST", "/api/owner/companies/{company_id}/contractors/{contractor_id}/pay"),
    ("POST", "/api/owner/companies/{company_id}/contractors/refresh"),
    ("PUT", "/api/owner/companies/{company_id}/contractors/{contractor_id}"),
    ("DELETE", "/api/owner/companies/{company_id}/contractors/{contractor_id}"),
    # Detener los recordatorios automáticos de una factura (5-nov): SOLO el dueño.
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/stop-auto-reminders"),
    ("PUT", "/api/owner/companies/{company_id}/series/{series_id}"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/offer"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/evidence"),
}

# Rutas SOLO DEL DUEÑO: el bookkeeper (y su vista previa) NO puede cobrar a nombre del dueño (FASE 16, 31-oct). La
# segunda pasada exige que al bookkeeper le respondan 403 — al revés que en el resto de las rutas de su libro.
OWNER_ONLY: set[tuple[str, str]] = {
    ("POST", "/api/owner/companies/{company_id}/customers"),
    ("POST", "/api/owner/companies/{company_id}/invoices"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/send"),
    ("POST", "/api/owner/companies/{company_id}/invoices/voice"),
    ("POST", "/api/owner/companies/{company_id}/studio-ai"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/remind"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/reminder-draft"),
    ("GET", "/api/owner/companies/{company_id}/invoices/{invoice_id}/qr"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/summary"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/accept"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/message"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/simulate"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/record-payment"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/record-refund"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/cancel"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/discard"),
    ("PUT", "/api/owner/companies/{company_id}/auto-reminders"),
    ("PUT", "/api/owner/companies/{company_id}/invoice-profile"),
    ("GET", "/api/owner/companies/{company_id}/invoices/next-number"),
    ("POST", "/api/owner/companies/{company_id}/series"),
    ("POST", "/api/owner/companies/{company_id}/series/{series_id}/cancel"),
    ("POST", "/api/owner/companies/{company_id}/invoice-profile/logo"),
    ("DELETE", "/api/owner/companies/{company_id}/invoice-profile/logo"),
    ("POST", "/api/owner/companies/{company_id}/contractors"),
    ("POST", "/api/owner/companies/{company_id}/contractors/{contractor_id}/pay"),
    ("POST", "/api/owner/companies/{company_id}/contractors/refresh"),
    ("PUT", "/api/owner/companies/{company_id}/contractors/{contractor_id}"),
    ("DELETE", "/api/owner/companies/{company_id}/contractors/{contractor_id}"),
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/stop-auto-reminders"),
    ("PUT", "/api/owner/companies/{company_id}/series/{series_id}"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/offer"),
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/evidence"),
}

# Rutas que NO se prueban porque no pertenecen al modelo de acceso por libro.
SKIP: set[tuple[str, str]] = {
    ("POST", "/api/paypal/webhook/{connection_id}"),   # lo llama PayPal; check_paypal prueba que sin firma válida → 400
    ("POST", "/api/companies"),        # crea un libro NUEVO: no hay `company_id` que validar (Paso 4)
    ("GET", "/api/public/invoice-logo/{key}"),   # PÚBLICA: la pide PayPal; solo con la clave al azar (check_profile la prueba)
}

# Cuerpos mínimos VÁLIDOS para las rutas que mutan el libro. Sin esto FastAPI responde 422
# (valida el cuerpo antes de correr la función) y la prueba no llegaría a comprobar el permiso.
# Son justo las rutas peligrosas: las que reescriben la contabilidad.
BODIES: dict[tuple[str, str], dict] = {
    ("PATCH", "/api/transactions/{transaction_id}"): {"category": "Materials"},
    ("PATCH", "/api/companies/{company_id}"): {"name": "hackeada"},
    ("POST", "/api/companies/{company_id}/rules"): {
        "match_text": "X", "account_type": "OPERATING_EXPENSE", "category": "Materials"},
    ("PATCH", "/api/companies/{company_id}/rules/{rule_id}"): {"category": "Materials"},
    ("POST", "/api/companies/{company_id}/custom-categories"): {
        "account_type": "OPERATING_EXPENSE", "name": "Inventada"},
    # El plan de cuentas del libro (Movimientos, 18-oct). El PATCH apunta a la categoría 999999: un 404
    # también prueba el rechazo.
    ("POST", "/api/companies/{company_id}/chart"): {"account_type": "OPERATING_EXPENSE", "name": "Permcheck"},
    ("PATCH", "/api/companies/{company_id}/chart/{category_id}"): {"name": "Permcheck"},
    ("POST", "/api/companies/{company_id}/labels"): {"name": "mía", "color": "#ff0000"},
    ("PUT", "/api/transactions/{transaction_id}/labels"): {"label_ids": []},
    ("POST", "/api/companies/{company_id}/cell-formats"): {
        "transaction_ids": [], "col": None, "bold": True},
    ("POST", "/api/companies/{company_id}/members"): {"email": "x@y.com", "role": "MEMBER"},
    ("PATCH", "/api/companies/{company_id}/members/{member_id}"): {"role": "MEMBER"},
    ("POST", "/api/companies/{company_id}/pnl-reports"): {"name": "mío"},
    ("POST", "/api/companies/{company_id}/transactions/set-account"): {
        "transaction_ids": [], "bank_account": "X"},
    ("POST", "/api/transactions/clarify-group"): {"transaction_ids": [], "answer": "x"},
    ("POST", "/api/companies/{company_id}/checkout"): {"quota": 100},
    ("PATCH", "/api/firm"): {"brand_name": "PERMCHECK"},
    ("PATCH", "/api/companies/{company_id}/settlement"): {"wait_days": 5},
    ("POST", "/api/companies/{company_id}/bank-connections"): {"public_token": "public-permcheck"},
    ("POST", "/api/companies/{company_id}/bank-connections/check"): {"institution_name": "First Platypus Bank", "account_masks": ["0000"]},
    ("POST", "/api/companies/{company_id}/rules/preview"): {"keyword": "HOME DEPOT"},
    ("POST", "/api/companies/{company_id}/rules/{rule_id}/edit-preview"): {"match_text": "HOME DEPOT"},
    ("POST", "/api/companies/{company_id}/transactions/{transaction_id}/bank-removed"): {"action": "keep"},
    ("POST", "/api/companies/{company_id}/bank-connections/link-token"): {"language": "es"},
    ("POST", "/api/companies/{company_id}/checkout/confirm"): {"session_id": "cs_x"},
    ("POST", "/api/companies/{company_id}/transactions/set-account"): {
        "transaction_ids": ["__TX__"], "account": "Chase Checking"},
    ("POST", "/api/companies/{company_id}/rule-proposals/apply"): {"rules": []},
    # Notas del libro (F11). El PATCH apunta a la nota 999999: un 404 también prueba el rechazo.
    ("POST", "/api/companies/{company_id}/notes"): {"text": "permcheck note"},
    ("PATCH", "/api/companies/{company_id}/notes/{note_id}"): {"text": "permcheck note"},
    # Probar una nota (18-oct): 🚫, que nunca llama a Nebius aunque el permiso fallara.
    ("POST", "/api/companies/{company_id}/notes/preview"): {"text": "permcheck note", "kind": "dont_report", "merchant": "PERMCHECK"},
    # Agentes (F11). La prueba NO sale a internet: si el permiso dejara pasar, la llamada a Nebius
    # fallaría en local; lo que se mide aquí es solo el 403.
    ("POST", "/api/companies/{company_id}/agents/draft"): {"lang": "es"},
    ("POST", "/api/companies/{company_id}/agents/review"): {"lang": "es"},
    ("POST", "/api/companies/{company_id}/agents/verify"): {},
    ("POST", "/api/companies/{company_id}/agents/audit"): {"lang": "es"},
    ("POST", "/api/companies/{company_id}/agents/verify-merchant"): {"transaction_id": 999999},
    ("POST", "/api/companies/{company_id}/audit/findings/{finding_id}/resolve"): {"status": "ok"},
    ("POST", "/api/companies/{company_id}/agents/dismissals"): {"keyword": "PERMCHECK"},
    # Cobros (FASE 16 Paso 5). Cliente 999999: para el dueño, un 404 (no es un 403); para el bookkeeper, 403.
    ("POST", "/api/owner/companies/{company_id}/customers"): {"name": "Permcheck Customer", "email": "pc@test.local"},
    ("POST", "/api/owner/companies/{company_id}/invoices"): {"customer_id": 999999, "concept": "Permcheck", "amount": "10.00",
                                                            "due_date": "2099-01-01"},
    # Cobro por voz (1-nov): sin cliente reconocido no llama a la IA (responde que elija el cliente).
    ("POST", "/api/owner/companies/{company_id}/invoices/voice"): {"text": "factura de prueba por 10 dólares"},
    # El asistente del Tablero (6-nov): sin conversación responde 422 ANTES de llamar a la IA.
    ("POST", "/api/owner/companies/{company_id}/studio-ai"): {"input": []},
    # Recordatorios (1-nov): factura 999999 → para el dueño un 404, para el bookkeeper 403 (OWNER_ONLY).
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/reminder-draft"): {"lang": "en"},
    # Disputas (3-nov): disputa 999999 → para el dueño un 404, para el bookkeeper 403 (OWNER_ONLY).
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/summary"): {"lang": "en"},
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/accept"): {"note": "permcheck"},
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/message"): {"message": "permcheck"},
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/simulate"): {"action": "seller_favor"},
    # Fuera de PayPal (3-nov): factura 999999 → para el dueño un 404, para el bookkeeper 403. Confirmar es del bookkeeper.
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/record-payment"): {"method": "CASH", "day": "2026-11-01"},
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/record-refund"): {"method": "CASH", "day": "2026-11-01"},
    ("POST", "/api/companies/{company_id}/invoices/offline/{offline_id}/confirm"): {"bank_tx_id": 999999},
    ("POST", "/api/owner/companies/{company_id}/invoices/{invoice_id}/cancel"): {"notify": False},
    ("PUT", "/api/owner/companies/{company_id}/auto-reminders"): {"before": {"active": False, "days": 99}, "after": {"active": False, "days": 2, "times": 2}},
    ("PUT", "/api/owner/companies/{company_id}/invoice-profile"): {"state": "Florida"},
    ("POST", "/api/owner/companies/{company_id}/series"): {"customer_id": 999999, "concept": "Permcheck", "amount": "10.00", "start_date": "2099-01-01"},
    # Contratistas (4-nov): con PAYPAL_PAYOUTS encendido en la prueba; contratista 999999 → 404 para el dueño, 403 para el bookkeeper.
    ("POST", "/api/owner/companies/{company_id}/contractors"): {"name": "Permcheck", "email": "pc@test.local"},
    ("POST", "/api/owner/companies/{company_id}/contractors/{contractor_id}/pay"): {"amount": "1.00"},
    ("PUT", "/api/owner/companies/{company_id}/contractors/{contractor_id}"): {"name": "Permcheck", "email": "pc@test.local"},
    ("PUT", "/api/owner/companies/{company_id}/series/{series_id}"): {"concept": "Permcheck", "amount": "10.00"},
    ("POST", "/api/owner/companies/{company_id}/disputes/{dispute_id}/offer"): {"amount": "1.00"},
    # Log in with PayPal (1-nov): un `state` inventado → 400 (la firma no cuadra), nunca un 2xx.
    ("POST", "/api/paypal/oauth"): {"code": "permcheck", "state": "permcheck.forged"},
    ("POST", "/api/companies/{company_id}/paypal/credentials"): {"client_id": "", "client_secret": ""},
    # PayPal (FASE 16 Paso 4): confirmar el depósito de un retiro es del bookkeeper. Retiro 999999: un 404 también rechaza.
    ("POST", "/api/companies/{company_id}/paypal/transfers/{withdrawal_id}/confirm"): {"bank_tx_id": 999999},
    # El dueño en la consola (FASE 16, 31-oct): invitarlo es del bookkeeper, nunca del propio cliente.
    ("POST", "/api/companies/{company_id}/owner-access"): {"email": "permcheck-owner2@test.local"},
    # Las dos puertas del demo (Firm, 23-oct): fuera del demo, 404.
    ("POST", "/api/demo/access"): {"code": "permcheck-wrong"},
    # "__TX__" se reemplaza por el id de la transacción REAL de prueba: con una lista vacía el
    # endpoint corta con 422 ("no transactions") ANTES de mirar el permiso, y no probaría nada.
    ("POST", "/api/transactions/clarify-group"): {"transaction_ids": ["__TX__"], "answer": "x"},
}

# Parámetros OBLIGATORIOS en la URL (query). Sin ellos FastAPI responde 422 antes del permiso.
QUERY: dict[tuple[str, str], dict] = {
    ("GET", "/api/companies/{company_id}/agents/estimate"): {"action": "audit"},
    ("GET", "/api/companies/{company_id}/pivot/leaves"): {
        "section": "COGS", "category": "Materials", "subcategory": "Home Depot", "month": "2026-06"},
}

# Rutas que reciben un ARCHIVO (multipart), no JSON. Se manda un archivo mínimo para que la
# validación del cuerpo pase y se llegue de verdad a comprobar el permiso.
FILES: dict[tuple[str, str], dict] = {
    ("POST", "/api/companies/{company_id}/rules/import"):
        {"file": ("reglas.csv", b"match,category\nX,Materials\n", "text/csv")},
    ("POST", "/api/companies/{company_id}/transactions/upload"):
        {"file": ("extracto.csv", b"date,description,amount\n2026-06-01,X,10\n", "text/csv")},
}


def _fill(body: dict, tx_id: int) -> dict:
    """Reemplaza el centinela "__TX__" por el id real de la transacción de prueba."""
    return {k: ([tx_id if x == "__TX__" else x for x in v] if isinstance(v, list) else v)
            for k, v in body.items()}


def build_fixtures(s: Session) -> tuple[int, int]:
    """Crea firma + libro + bookkeeper + cliente + 1 transacción. Devuelve (company_id, tx_id)."""
    bk = User(firebase_uid=BOOK_UID, email="permcheck-bk@test.local")
    cl = User(firebase_uid=CLIENT_UID, email="permcheck-client@test.local")
    s.add(bk); s.add(cl); s.commit(); s.refresh(bk); s.refresh(cl)

    firm = Firm(name="PERMCHECK FIRM", owner_user_id=bk.id, brand_name="PERMCHECK")
    s.add(firm); s.commit(); s.refresh(firm)
    s.add(FirmMember(firm_id=firm.id, user_id=bk.id, email=bk.email, role=FirmRole.OWNER))

    co = Company(name="PERMCHECK BOOK", sector="construction", firm_id=firm.id, user_id=bk.id)
    s.add(co); s.commit(); s.refresh(co)

    # El cliente entra por INVITACIÓN al libro: es el único camino al rol CLIENT.
    s.add(CompanyMember(company_id=co.id, user_id=cl.id, email=cl.email, role=Role.CLIENT))
    tx = Transaction(company_id=co.id, date=date(2026, 6, 1), description="HOME DEPOT",
                     amount=Decimal("100.00"), is_inflow=False, category="Materials")
    s.add(tx); s.commit(); s.refresh(tx)
    return co.id, tx.id


def drop_fixtures(s: Session) -> None:
    """Borra TODO lo creado por la prueba. Usa el borrado en cascada del demo (`demo._delete`), que
    DESCUBRE en la base qué cuelga de cada fila — por clave foránea y por referencia suave. Antes había
    una lista de tablas escrita a mano y se rompió dos veces con tablas nuevas de otras áreas
    (`sheetlabel` el 27-sep, `bankconnection` de Teller el 3-oct)."""
    import demo  # noqa: E402 (aquí, para no cargarlo si nadie limpia)
    users = s.exec(select(User).where(User.firebase_uid.in_([BOOK_UID, CLIENT_UID]))).all()
    uids = [u.id for u in users]
    fids = [f.id for f in s.exec(select(Firm).where(Firm.name == "PERMCHECK FIRM")).all()]
    # Por FIRMA y por DUEÑO, nunca por nombre: la prueba renombra el libro (`PATCH /companies`).
    cids: set[int] = set()
    if fids:
        cids |= set(s.exec(select(Company.id).where(Company.firm_id.in_(fids))).all())
    if uids:
        cids |= set(s.exec(select(Company.id).where(Company.user_id.in_(uids))).all())
    counts: dict[str, int] = {}
    conn = s.connection()
    demo._delete(conn, "company", sorted(cids), counts)
    demo._delete(conn, "firm", fids, counts)
    demo._delete(conn, "user", uids, counts)
    s.commit()


ANON_UID = "__permcheck_anonymous__"


def check_anonymous_tokens(client: TestClient) -> list[str]:
    """DEUDA #17 (4-oct): un token ANÓNIMO de Firebase solo entra en el demo. Demo y producción comparten
    el proyecto de Firebase, y cualquiera saca un token anónimo con la API key pública del frontend.

    Se apaga `AUTH_DEV_MODE` y se reemplaza la verificación de Firebase por una de mentira (sin red) que
    decodifica tokens inventados — así se prueba el camino REAL de producción (`Authorization: Bearer`)."""
    import auth
    import demo
    print("\n  --- tokens anónimos de Firebase (DEUDA #17) ---")
    fake_claims = {
        "anon": {"uid": ANON_UID, "firebase": {"sign_in_provider": "anonymous"}},
        "google": {"uid": "__permcheck_google__", "email": "g@test.local", "firebase": {"sign_in_provider": "google.com"}},
    }
    def fake_verify(token: str) -> dict:
        if token not in fake_claims:
            raise ValueError("firma inválida")
        return fake_claims[token]
    saved = (auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE)
    fails: list[str] = []
    def expect(label: str, ok: bool, got) -> None:
        print(f"  {'✅' if ok else '❌'} {label} → {got}")
        if not ok:
            fails.append(f"DEUDA #17: {label} → {got}")
    try:
        auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE = False, fake_verify, False
        # 1) Producción, por HTTP: 401 y NINGÚN usuario creado en la base.
        r = client.get("/api/me", headers={"Authorization": "Bearer anon"})
        with Session(engine) as s:
            created = s.exec(select(User).where(User.firebase_uid == ANON_UID)).first() is not None
        expect("fuera del demo, token anónimo en /api/me", r.status_code == 401 and not created,
               f"{r.status_code} · usuario creado: {created}")
        # 2) Producción: un token de Google sí pasa (no se rompió el login normal).
        ident = auth.current_identity(authorization="Bearer google", x_dev_uid=None, x_dev_email=None)
        expect("fuera del demo, token de Google", ident["uid"] == "__permcheck_google__", ident["uid"])
        # 3) Un token inválido sigue siendo 401.
        try:
            auth.current_identity(authorization="Bearer basura", x_dev_uid=None, x_dev_email=None)
            expect("token inválido", False, "entró")
        except Exception as e:
            expect("token inválido", getattr(e, "status_code", None) == 401, getattr(e, "detail", e))
        # 4) En el demo, el anónimo SÍ entra (sin pasar por la base: no se siembra nada aquí).
        demo.DEMO_MODE = True
        ident = auth.current_identity(authorization="Bearer anon", x_dev_uid=None, x_dev_email=None)
        expect("en el demo, token anónimo", ident["uid"] == ANON_UID, ident["uid"])
    finally:
        auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE = saved
        with Session(engine) as s:   # por si una regresión llegó a crearlo
            u = s.exec(select(User).where(User.firebase_uid == ANON_UID)).first()
            if u:
                import demo as _d
                _d._delete(s.connection(), "user", [u.id], {}); s.commit()
    return fails


DEMO_UIDS = {"anon": "__permcheck_demo_anon__", "google": "__permcheck_demo_google__",
             "broken": "__permcheck_demo_broken__"}


def check_demo_cleanup_firebase() -> list[str]:
    """DEUDA #18 (4-oct): la limpieza de 7 días borra también el usuario ANÓNIMO de Firebase, nunca uno
    de Google (demo y producción comparten el proyecto), y un fallo de Firebase no deja la base a medias.

    Firebase se reemplaza por uno de mentira (sin red). Las oficinas de prueba vencen en el año 2000 y la
    limpieza corre con `now` = 2-ene-2000: así SOLO toca estas, nunca una oficina de demo real de la base."""
    from datetime import datetime
    from types import SimpleNamespace
    import demo
    print("\n  --- limpieza del demo y usuarios de Firebase (DEUDA #18) ---")
    fails: list[str] = []
    def expect(label: str, ok: bool, got) -> None:
        print(f"  {'✅' if ok else '❌'} {label} → {got}")
        if not ok:
            fails.append(f"DEUDA #18: {label} → {got}")

    class FakeFirebase:
        def __init__(self): self.deleted: list[str] = []
        def get_user(self, uid):
            if uid == DEMO_UIDS["broken"]:
                raise ConnectionError("Firebase caído")
            if uid == DEMO_UIDS["google"]:
                return SimpleNamespace(provider_data=[object()], email="g@test.local", phone_number=None)
            return SimpleNamespace(provider_data=[], email=None, phone_number=None)
        def delete_user(self, uid): self.deleted.append(uid)

    def plant(s: Session) -> None:
        for uid in DEMO_UIDS.values():
            u = User(firebase_uid=uid); s.add(u); s.commit(); s.refresh(u)
            f = Firm(name="PERMCHECK DEMO", owner_user_id=u.id, demo_expires_at=datetime(2000, 1, 1))
            s.add(f); s.commit(); s.refresh(f)
            s.add(FirmMember(firm_id=f.id, user_id=u.id, email="", role=FirmRole.OWNER))
            s.add(Company(name="PERMCHECK DEMO BOOK", sector="construction", firm_id=f.id, user_id=u.id))
            s.commit()

    def leftovers(s: Session) -> int:
        return len(s.exec(select(User).where(User.firebase_uid.in_(list(DEMO_UIDS.values())))).all())

    def drop(s: Session) -> None:
        fids = list(s.exec(select(Firm.id).where(Firm.name == "PERMCHECK DEMO")).all())
        uids = list(s.exec(select(User.id).where(User.firebase_uid.in_(list(DEMO_UIDS.values())))).all())
        conn = s.connection()
        demo._delete(conn, "firm", fids, {}); demo._delete(conn, "user", uids, {}); s.commit()

    saved = (demo.DEMO_MODE, demo._firebase)
    try:
        # 1) Firebase responde: borra SOLO el anónimo; el de Google se queda; el que falla se cuenta.
        fake = FakeFirebase()
        demo.DEMO_MODE, demo._firebase = True, (lambda: fake)
        with Session(engine) as s:
            drop(s); plant(s)
            counts = demo.cleanup_expired_offices(s, now=datetime(2000, 1, 2))
            left = leftovers(s)
        expect("borra en Firebase solo el anónimo", fake.deleted == [DEMO_UIDS["anon"]], fake.deleted)
        expect("no borra la cuenta de Google ni oculta el fallo",
               counts.get("firebase_kept_not_anonymous") == 1 and counts.get("firebase_errors") == 1,
               {k: v for k, v in counts.items() if k.startswith("firebase")})
        expect("la base quedó limpia (3 oficinas y sus dueños)", left == 0, f"{left} usuarios quedan")
        # 2) firebase-admin ni arranca: la limpieza de la base igual termina.
        def broken():
            raise RuntimeError("sin credenciales")
        demo._firebase = broken
        with Session(engine) as s:
            plant(s)
            try:
                counts = demo.cleanup_expired_offices(s, now=datetime(2000, 1, 2)); err = None
            except Exception as e:
                err = e
            left = leftovers(s)
        expect("Firebase caído no rompe la limpieza de la base", err is None and left == 0,
               f"error: {err} · usuarios quedan: {left}")
        # 3) Fuera del demo nunca se llama a Firebase (las pruebas y la producción no lo tocan).
        fake = FakeFirebase()
        demo.DEMO_MODE, demo._firebase = False, (lambda: fake)
        with Session(engine) as s:
            plant(s)
            demo.cleanup_expired_offices(s, now=datetime(2000, 1, 2))
        expect("fuera del demo no se toca Firebase", fake.deleted == [], fake.deleted)
    finally:
        demo.DEMO_MODE, demo._firebase = saved
        with Session(engine) as s:
            drop(s)
    return fails


# ── LAS DOS PUERTAS DEL DEMO (23-oct) ─────────────────────────────────────────────────────────────
# Se prueban como corren en el servicio de demo: `DEMO_MODE` encendido y `AUTH_DEV_MODE` APAGADO (el candado 1
# no deja tener los dos), con la verificación de Firebase reemplazada por una de mentira, sin red, que
# reconoce tokens inventados — el camino real (`Authorization: Bearer`) de punta a punta.

DOOR_UIDS = {"anon": "__permcheck_door_anon__", "nocode": "__permcheck_door_nocode__",
             "live": "__permcheck_door_live__", "other": "__permcheck_door_other__"}
DOOR_CODES = {"DEMO_ACCESS_CODE": "permcheck-devpost-7Qx2", "DEMO_ACCESS_CODE_GUEST": "permcheck-guest-K9w4"}


def _sweep(client: TestClient, token: str, subs: dict, *, methods_only: Optional[set] = None,
           allowed: set = frozenset()) -> list[str]:
    """Recorre TODAS las rutas con `token` sobre el libro/movimiento de `subs`. Ninguna puede dar 2xx salvo las
    de `allowed`. `methods_only={"POST",…}` limita a esos métodos (las escrituras de un VIEWER)."""
    leaks: list[str] = []
    for route in app.routes:
        tmpl = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not tmpl or not tmpl.startswith("/api"):
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            key = (method, tmpl)
            if (methods_only and method not in methods_only) or key in SKIP - {("POST", "/api/companies")}:
                continue
            url = tmpl
            for name in ("company_id", "transaction_id"):
                url = url.replace("{" + name + "}", subs[name])
            while "{" in url:
                a = url.index("{"); b = url.index("}") + 1
                url = url[:a] + "999999" + url[b:]
            kwargs: dict = {"headers": {"Authorization": f"Bearer {token}"}}
            if key in BODIES:
                kwargs["json"] = _fill(BODIES[key], int(subs["transaction_id"]))
            elif key in FILES:
                kwargs["files"] = FILES[key]
            elif key == ("POST", "/api/companies"):
                kwargs["json"] = {"name": "PERMCHECK NEW"}
            if key in QUERY:
                kwargs["params"] = QUERY[key]
            r = client.request(method, url, **kwargs)
            if 200 <= r.status_code < 300 and key not in allowed:
                leaks.append(f"{method} {tmpl} → {r.status_code}")
                print(f"  🔴 [{token}] {method:6} {tmpl}  → {r.status_code}  ¡FUGA!")
    return leaks


def check_demo_doors(client: TestClient) -> list[str]:
    import logging
    import agent_calls
    import auth
    import demo
    from models import AgentCall, DemoAccess, DemoCodeAttempt, DemoOffice
    print("\n  --- las dos puertas del demo (23-oct) ---")
    claims = {
        "anon": {"uid": DOOR_UIDS["anon"], "firebase": {"sign_in_provider": "anonymous"}},
        **{k: {"uid": DOOR_UIDS[k], "email": f"{k}@permcheck.local", "firebase": {"sign_in_provider": "google.com"}}
           for k in ("nocode", "live", "other")},
    }
    def fake_verify(token: str) -> dict:
        if token not in claims:
            raise ValueError("firma inválida")
        return claims[token]
    H = lambda k: {"Authorization": f"Bearer {k}"}   # noqa: E731
    fails: list[str] = []
    def expect(label: str, ok: bool, got) -> None:
        print(f"  {'✅' if ok else '❌'} {label} → {got}")
        if not ok:
            fails.append(f"puertas: {label} → {got}")

    saved = (auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE, demo.cleanup_quietly,
             demo.DEMO_POOL_SIZE, demo.GLOBAL_DAILY_CALLS, demo.GLOBAL_DAILY_SEARCHES,
             {k: os.environ.get(k) for k in DOOR_CODES})
    pool_user_existed = False
    made_tour = False
    try:
        auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE = False, fake_verify, True
        demo.cleanup_quietly = lambda s: None      # la limpieza real NO corre sobre la base de desarrollo
        os.environ.update(DOOR_CODES)

        # Fixtures: la visita (si la base no tiene una) y la oficina en vivo de OTRO usuario con código.
        with Session(engine) as s:
            pool_user_existed = s.exec(select(User).where(User.firebase_uid == demo.POOL_UID)).first() is not None
            tour_id = demo.tour_firm_id(s)
            if tour_id is None:
                made_tour = True
                tu = User(firebase_uid="__permcheck_door_tourowner__"); s.add(tu); s.flush()
                tf = Firm(name="PERMCHECK TOUR", owner_user_id=tu.id); s.add(tf); s.flush()
                s.add(DemoOffice(firm_id=tf.id, kind="tour"))
                s.add(FirmMember(firm_id=tf.id, user_id=tu.id, email="", role=FirmRole.OWNER))
                tco = Company(name="PERMCHECK TOUR BOOK", sector="construction", firm_id=tf.id, user_id=tu.id)
                s.add(tco); s.flush()
                s.add(Transaction(company_id=tco.id, date=date(2026, 6, 1), description="HOME DEPOT",
                                  amount=Decimal("100.00"), is_inflow=False, category="Materials"))
                s.commit()
                tour_id = tf.id
            tour_co = s.exec(select(Company).where(Company.firm_id == tour_id).order_by(Company.id)).first()
            tour_tx = s.exec(select(Transaction).where(Transaction.company_id == tour_co.id)).first()
            ou = User(firebase_uid=DOOR_UIDS["other"], email="other@permcheck.local"); s.add(ou); s.flush()
            of = Firm(name="PERMCHECK OTHER", owner_user_id=ou.id, demo_expires_at=datetime(2099, 1, 1))
            s.add(of); s.flush()
            s.add(FirmMember(firm_id=of.id, user_id=ou.id, email=ou.email, role=FirmRole.OWNER))
            s.add(DemoAccess(user_id=ou.id, code_name="devpost"))
            oco = Company(name="PERMCHECK OTHER BOOK", sector="construction", firm_id=of.id, user_id=ou.id)
            s.add(oco); s.flush()
            otx = Transaction(company_id=oco.id, date=date(2026, 6, 1), description="HOME DEPOT",
                              amount=Decimal("100.00"), is_inflow=False, category="Materials")
            s.add(otx); s.commit()
            tour_subs = {"company_id": str(tour_co.id), "transaction_id": str(tour_tx.id)}
            other_subs = {"company_id": str(oco.id), "transaction_id": str(otx.id)}
            other_co_id = oco.id

        def owned_by(uid_key: str) -> int:
            with Session(engine) as s:
                u = s.exec(select(User).where(User.firebase_uid == DOOR_UIDS[uid_key])).first()
                if u is None:
                    return 0
                return len(s.exec(select(Firm).where(Firm.owner_user_id == u.id)).all()) + \
                    len(s.exec(select(FirmMember).where(FirmMember.user_id == u.id)).all())

        demo.DEMO_POOL_SIZE = 0     # en los barridos, rellenar la piscina no siembra nada
        refill_ok = {("POST", "/api/demo/pool/refill")}
        # Un POST que solo LEE (qué cazaría una regla; ni escribe ni llama a la IA): un VIEWER puede.
        viewer_ok = refill_ok | {("POST", "/api/companies/{company_id}/rules/preview")}
        # Sin código, `/api/firm` es la VISITA (no la oficina del otro): se comprueba aparte, abajo.
        # `/api/demo/intro`: los conteos de la VISITA (pública), no de la oficina del otro.
        reads_ok = {("GET", "/api/me"), ("GET", "/api/hello"), ("GET", "/api/firm"), ("GET", "/api/demo/intro")} | refill_ok

        # 1) La visita guiada: el anónimo la ve como VIEWER y no nace nada suyo.
        r = client.get("/api/me", headers=H("anon"))
        j = r.json() if r.status_code == 200 else {}
        expect("anónimo: /api/me abre la visita", r.status_code == 200 and j.get("demo", {}).get("door") == "tour"
               and j.get("companies") and all(v == "VIEWER" for v in j.get("roles", {}).values()),
               f"{r.status_code} · {j.get('demo')} · {j.get('roles')}")
        expect("anónimo: ninguna oficina ni membresía propia", owned_by("anon") == 0, owned_by("anon"))
        leaks = _sweep(client, "anon", tour_subs, methods_only={"POST", "PATCH", "PUT", "DELETE"}, allowed=viewer_ok)
        expect("anónimo (VIEWER de la visita): ninguna escritura pasa", not leaks, leaks or "0 fugas")
        r = client.post("/api/demo/access", json={"code": DOOR_CODES["DEMO_ACCESS_CODE"]}, headers=H("anon"))
        expect("anónimo: no canjea códigos", r.status_code == 403, f"{r.status_code} {r.json().get('detail')}")

        # 2) Google SIN código: ni oficina, ni agentes, ni la oficina de otro.
        r = client.get("/api/me", headers=H("nocode"))
        expect("Google sin código: /api/me pide el código", r.status_code == 200 and r.json().get("demo", {}).get("door") == "needs_code",
               f"{r.status_code} · {r.json().get('demo')}")
        r = client.post("/api/companies", json={"name": "PERMCHECK NEW"}, headers=H("nocode"))
        expect("Google sin código: no crea oficina (POST /api/companies)", r.status_code == 403 and owned_by("nocode") == 0,
               f"{r.status_code} · oficinas: {owned_by('nocode')}")
        leaks = _sweep(client, "nocode", other_subs, allowed=reads_ok)
        expect("Google sin código: nada de la oficina de otro (todas las rutas)", not leaks, leaks or "0 fugas")
        r = client.get("/api/firm", headers=H("nocode"))
        expect("Google sin código: su /api/firm es la visita, como VIEWER", r.status_code == 200
               and r.json().get("id") == tour_id and r.json().get("role") == "VIEWER", r.json())
        leaks = _sweep(client, "nocode", tour_subs, methods_only={"POST", "PATCH", "PUT", "DELETE"}, allowed=viewer_ok)
        expect("Google sin código: ninguna escritura ni agente en la visita", not leaks, leaks or "0 fugas")
        expect("Google sin código: sigue sin oficina tras los barridos", owned_by("nocode") == 0, owned_by("nocode"))

        # 3) El código: tiempo constante, intentos limitados, y nunca en el log.
        expect("match_code: vacío, errado, apagado → nada",
               demo.match_code("") is None and demo.match_code("x") is None, "ok")
        expect("match_code: el de invitados con espacios", demo.match_code(f"  {DOOR_CODES['DEMO_ACCESS_CODE_GUEST']} ") == "guest", "ok")
        records: list[str] = []
        class _Grab(logging.Handler):
            def emit(self, rec): records.append(self.format(rec))
        grab = _Grab(); logging.getLogger().addHandler(grab); logging.getLogger().setLevel(logging.INFO)
        codes = []
        for i in range(demo.MAX_FAILS_USER):
            codes.append(client.post("/api/demo/access", json={"code": f"wrong-{i}"}, headers=H("live")).status_code)
        r = client.post("/api/demo/access", json={"code": DOOR_CODES["DEMO_ACCESS_CODE_GUEST"]}, headers=H("live"))
        expect(f"{demo.MAX_FAILS_USER} errados → 403; el siguiente, aunque acierte, 429",
               codes == [403] * demo.MAX_FAILS_USER and r.status_code == 429, f"{codes} → {r.status_code}")
        expect("bloqueado: no se le dio acceso ni oficina", owned_by("live") == 0, owned_by("live"))
        with Session(engine) as s:
            lu = s.exec(select(User).where(User.firebase_uid == DOOR_UIDS["live"])).first()
            s.exec(delete(DemoCodeAttempt).where(DemoCodeAttempt.user_id == lu.id)); s.commit()

        # 4) Con código: una oficina de la piscina, a su nombre, que vence desde HOY.
        demo.DEMO_POOL_SIZE = 1
        r = client.post("/api/demo/pool/refill", headers=H("nocode"))
        expect("la piscina se rellena dentro de la petición", r.status_code == 200 and r.json().get("ready", 0) >= 1, r.json())
        r = client.post("/api/demo/access", json={"code": DOOR_CODES["DEMO_ACCESS_CODE_GUEST"]}, headers=H("live"))
        expect("código de invitados → acceso", r.status_code == 200 and r.json().get("code_name") == "guest",
               f"{r.status_code} {r.json()}")
        logging.getLogger().removeHandler(grab)
        leaked = [x for x in records if any(c in x for c in list(DOOR_CODES.values()) + ["wrong-0"])]
        expect("el código nunca va al log", not leaked, leaked or f"{len(records)} líneas, ninguna con el código")
        with Session(engine) as s:
            lu = s.exec(select(User).where(User.firebase_uid == DOOR_UIDS["live"])).first()
            acc = s.exec(select(DemoAccess).where(DemoAccess.user_id == lu.id)).first()
            expect("guarda QUÉ código usó (el nombre)", acc is not None and acc.code_name == "guest", acc and acc.code_name)
            f = s.exec(select(Firm).where(Firm.owner_user_id == lu.id)).first()
            office = s.exec(select(DemoOffice).where(DemoOffice.firm_id == f.id)).first() if f else None
            left = (f.demo_expires_at - datetime.now(timezone.utc).replace(tzinfo=None)).days if f else None
            expect("la oficina salió de la piscina y vence a los DEMO_TTL_DAYS desde hoy",
                   office is not None and office.kind == "live" and left in (demo.DEMO_TTL_DAYS - 1, demo.DEMO_TTL_DAYS),
                   f"{office and office.kind} · vence en {left} días")
            pool = s.exec(select(User).where(User.firebase_uid == demo.POOL_UID)).first()
            cids = list(s.exec(select(Company.id).where(Company.firm_id == f.id)).all()) if f else []
            stale = s.connection().execute(text("""
                SELECT count(*) FROM categorizationchange WHERE company_id = ANY(:ids) AND changed_by_user_id = :p
            """), {"ids": cids, "p": pool.id if pool else -1}).scalar()
            books = s.exec(select(Company).where(Company.firm_id == f.id).order_by(Company.id)).all() if f else []
            expect("los libros y su historial pasan al usuario (nada queda a nombre de la piscina)",
                   len(books) == 3 and all(b.user_id == lu.id for b in books) and stale == 0,
                   f"{len(books)} libros · {stale} filas de historial a nombre de la piscina")
        r = client.get("/api/me", headers=H("live"))
        j = r.json()
        expect("con código: /api/me abre su oficina en Rivera HVAC", j.get("demo", {}).get("door") == "live"
               and j["companies"] and j["companies"][0]["name"].startswith("Rivera HVAC")
               and set(j["roles"].values()) == {"OWNER"}, f"{j.get('demo')} · {[c['name'] for c in j['companies']]}")
        r = client.get(f"/api/companies/{other_co_id}", headers=H("live"))
        expect("con código: la oficina de OTRO sigue cerrada", r.status_code == 403, r.status_code)
        r = client.patch(f"/api/companies/{tour_subs['company_id']}", json={"name": "hackeada"}, headers=H("live"))
        expect("con código: la visita sigue de solo lectura", r.status_code == 403, r.status_code)

        # 5) El tope GLOBAL: con el demo entero en su techo, ni la oficina con cupo llama al modelo.
        with Session(engine) as s:
            since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
            used = s.exec(select(func.count(AgentCall.id)).where(AgentCall.created_at >= since,
                                                                AgentCall.agent.in_(agent_calls._MODEL_AGENTS))).one()
            searches = s.exec(select(func.count(AgentCall.id)).where(AgentCall.created_at >= since,
                                                                    AgentCall.agent == "verifier")).one()
            book = s.get(Company, j["companies"][0]["id"])
            book_id = book.id
            # Una llamada y una búsqueda sembradas en el libro de prueba: el techo = lo gastado (0 = apagado).
            for agent in ("reviewer", "verifier"):
                s.add(AgentCall(company_id=book.id, firm_id=book.firm_id, agent=agent, model="permcheck"))
            s.commit()
            s.refresh(book)
            demo.GLOBAL_DAILY_CALLS, demo.GLOBAL_DAILY_SEARCHES = used + 1, searches + 1
            got = []
            for agent in (None, "verifier"):
                try:
                    agent_calls.check_cap(s, book, agent); got.append("pasó")
                except agent_calls.GlobalCapReached as e:
                    got.append(e.code)
        expect("tope global: llamadas y búsquedas", got == ["demo_global_cap_reached"] * 2, got)
        r = client.post(f"/api/companies/{book_id}/agents/review", json={"lang": "en"}, headers=H("live"))
        expect("tope global: la ruta responde 429 con su código", r.status_code == 429
               and r.json().get("detail", {}).get("code") == "demo_global_cap_reached", f"{r.status_code} {r.json()}")

        # 6) Si se VACÍA el código de invitados, quienes entraron con él pierden el acceso.
        os.environ["DEMO_ACCESS_CODE_GUEST"] = ""
        r = client.get("/api/me", headers=H("live"))
        r2 = client.get(f"/api/companies/{book_id}", headers=H("live"))
        expect("código de invitados vaciado → sin acceso", r.json().get("demo", {}).get("door") == "needs_code"
               and r2.status_code == 403, f"{r.json().get('demo')} · su libro → {r2.status_code}")
        os.environ["DEMO_ACCESS_CODE_GUEST"] = DOOR_CODES["DEMO_ACCESS_CODE_GUEST"]

        # 7) La limpieza nunca borra la visita, ni aunque alguien le pusiera fecha por error.
        with Session(engine) as s:
            tf = s.get(Firm, tour_id)
            before = tf.demo_expires_at
            tf.demo_expires_at = datetime(1999, 1, 1); s.add(tf); s.commit()
            saved_fb = demo._firebase
            demo._firebase = lambda: type("F", (), {"get_user": lambda *_: None, "delete_user": lambda *_: None})()
            try:
                demo.cleanup_expired_offices(s, now=datetime(2000, 1, 2))
            finally:
                demo._firebase = saved_fb
            alive = s.get(Firm, tour_id) is not None
            if alive:
                tf = s.get(Firm, tour_id); tf.demo_expires_at = before; s.add(tf); s.commit()
        expect("la limpieza no borra la visita guiada", alive, alive)
    finally:
        (auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE, demo.cleanup_quietly,
         demo.DEMO_POOL_SIZE, demo.GLOBAL_DAILY_CALLS, demo.GLOBAL_DAILY_SEARCHES, env) = saved
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        with Session(engine) as s:
            uids = list(DOOR_UIDS.values()) + (["__permcheck_door_tourowner__"] if made_tour else [])
            if not pool_user_existed:
                uids.append(demo.POOL_UID)
            users = s.exec(select(User).where(User.firebase_uid.in_(uids))).all()
            ids = [u.id for u in users]
            fids = list(s.exec(select(Firm.id).where(Firm.owner_user_id.in_(ids))).all()) if ids else []
            conn = s.connection()
            cids = [r[0] for r in conn.execute(text("SELECT id FROM company WHERE firm_id = ANY(:f)"), {"f": fids})]
            demo._delete(conn, "company", cids, {})
            demo._delete(conn, "firm", fids, {})
            conn.execute(text("DELETE FROM democodeattempt WHERE user_id = ANY(:u)"), {"u": ids})
            demo._delete(conn, "user", ids, {})
            s.commit()
    return fails


OWNER_KEYS = {"company", "managed_by", "brand_color", "brand_logo_url", "preview", "closed_months", "years", "paypal"}


def check_owner_portal(client: TestClient) -> list[str]:
    """FASE 16 (31-oct): el portal del dueño. Además del barrido de rutas (que ya prueba que el CLIENT no lee
    transacciones, categorías, notas ni reportes), esto prueba lo que el barrido no ve:
      1. que el dueño NO alcanza el portal de OTRO libro de la misma firma (403);
      2. que la portada trae SOLO sus campos — si alguien le agrega transacciones o cifras, esto falla;
      3. que el bookkeeper ve la MISMA portada en vista previa (`preview: true`) y el dueño no.
    """
    print("\n  --- el portal del dueño (FASE 16) ---")
    fails: list[str] = []
    def expect(label: str, ok: bool, got) -> None:
        print(f"  {'✅' if ok else '❌'} {label} → {got}")
        if not ok:
            fails.append(f"portal del dueño: {label} → {got}")
    with Session(engine) as s:
        company_id, _ = build_fixtures(s)
        firm_id = s.get(Company, company_id).firm_id
        bk_id = s.exec(select(User.id).where(User.firebase_uid == BOOK_UID)).one()
        other = Company(name="PERMCHECK OTHER BOOK", sector="construction", firm_id=firm_id, user_id=bk_id)
        s.add(other)
        # Un bookkeeper de la oficina (invitación pendiente): si lo invitan como dueño, se AVISA.
        s.add(FirmMember(firm_id=firm_id, email="permcheck-mate@test.local", role=FirmRole.BOOKKEEPER))
        s.commit(); s.refresh(other)
        other_id = other.id
    cl = {"X-Dev-Uid": CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    try:
        r = client.get(f"/api/owner/companies/{other_id}", headers=cl)
        expect("el dueño en el portal de OTRO libro de su firma", r.status_code == 403, r.status_code)
        r = client.get(f"/api/owner/companies/{company_id}", headers=cl)
        body = r.json() if r.status_code == 200 else {}
        expect("el dueño en SU portal", r.status_code == 200 and body.get("preview") is False,
               f"{r.status_code} · preview={body.get('preview')}")
        expect("la portada trae solo sus campos", set(body) == OWNER_KEYS, sorted(set(body) ^ OWNER_KEYS) or "ok")
        r = client.get(f"/api/owner/companies/{company_id}", headers=bk)
        expect("el bookkeeper ve la portada en vista previa",
               r.status_code == 200 and r.json().get("preview") is True, r.status_code)
        # El dueño pone la cuenta SOLO a filas sin cuenta: nunca cambia la de una que ya la tiene.
        with Session(engine) as s:
            had = Transaction(company_id=company_id, date=date(2026, 6, 2), description="HAS ACCOUNT",
                              amount=Decimal("5.00"), is_inflow=False, bank_account="••1111")
            s.add(had); s.commit(); s.refresh(had); had_id = had.id
        client.post(f"/api/companies/{company_id}/transactions/set-account", headers=cl,
                    json={"transaction_ids": [had_id], "account": "9999"})
        with Session(engine) as s:
            kept = s.get(Transaction, had_id).bank_account
        expect("el dueño no cambia la cuenta de una fila que ya la tiene", kept == "••1111", kept)
        r = client.post(f"/api/companies/{company_id}/owner-access", headers=bk,
                        json={"email": "permcheck-bk@test.local"})
        expect("invitar como dueño a quien creó el libro → 409", r.status_code == 409, r.status_code)
        r = client.post(f"/api/companies/{company_id}/owner-access", headers=bk,
                        json={"email": "permcheck-mate@test.local"})
        expect("invitar como dueño a alguien del equipo de la oficina → aviso, no bloqueo",
               r.status_code == 200 and r.json().get("warning") == "team_elsewhere",
               f"{r.status_code} · {r.json().get('warning') if r.status_code == 200 else r.json()}")
    finally:
        with Session(engine) as s:
            drop_fixtures(s)
    return fails


def check_month_close(client: TestClient) -> list[str]:
    """FASE 16 Paso 2 (31-oct): cerrar el mes. Lo que el barrido no ve: que un mes cerrado de verdad NO cambia
    (ni a mano, ni por una regla), que el dueño ve la FOTO (y nada al reabrir), y lo que llega tarde."""
    print("\n  --- cerrar el mes (FASE 16) ---")
    fails: list[str] = []
    def expect(label: str, ok: bool, got) -> None:
        print(f"  {'✅' if ok else '❌'} {label} → {got}")
        if not ok:
            fails.append(f"cerrar el mes: {label} → {got}")
    with Session(engine) as s:
        company_id, tx_id = build_fixtures(s)
        tx = s.get(Transaction, tx_id)
        tx.account_type = AccountType.COGS                      # HOME DEPOT 100 → Materials (COGS), junio
        s.add(tx)
        for desc, amt, inflow, at, cat in (("CLIENT PAYMENT", "1000.00", True, AccountType.REVENUE, "Gross receipts or sales"),
                                           ("REFUND TO CLIENT", "200.00", False, AccountType.REVENUE, "Returns and allowances"),
                                           ("MYSTERY DEPOSIT", "50.00", True, None, None)):
            s.add(Transaction(company_id=company_id, date=date(2026, 6, 10), description=desc,
                              amount=Decimal(amt), is_inflow=inflow, account_type=at, category=cat))
        s.commit()
    cl = {"X-Dev-Uid": CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    base = f"/api/companies/{company_id}/months"
    try:
        r = client.post(f"{base}/2026-06/close", headers=cl)
        expect("el dueño NO puede cerrar un mes", r.status_code == 403, r.status_code)
        r = client.post(f"{base}/{date.today():%Y-%m}/close", headers=bk)
        expect("el mes en curso no se cierra", r.status_code == 422, r.status_code)
        with Session(engine) as s:                          # días de espera enormes: junio todavía "espera"
            co = s.get(Company, company_id); co.settlement_buffer_days = 9999; s.add(co); s.commit()
        r = client.post(f"{base}/2026-06/close", headers=bk)
        waiting = {x["month"]: x["status"] for x in client.get(base, headers=bk).json()}.get("2026-06")
        expect("antes de los días de espera no se cierra (422 waiting_days · status waiting)",
               r.status_code == 422 and (r.json().get("detail") or {}).get("code") == "waiting_days" and waiting == "waiting",
               f"{r.status_code} · {waiting}")
        with Session(engine) as s:
            co = s.get(Company, company_id); co.settlement_buffer_days = None; s.add(co); s.commit()
        r = client.post(f"{base}/2026-06/close", headers=bk)
        expect("el bookkeeper cierra junio", r.status_code == 200, r.status_code)
        r = client.post(f"{base}/2026-06/close", headers=bk)
        expect("cerrarlo dos veces → 409", r.status_code == 409, r.status_code)

        home = client.get(f"/api/owner/companies/{company_id}", headers=cl).json()
        m = (home.get("closed_months") or [{}])[0]
        expect("el dueño ve la foto de junio (entró 1000 − 200, salió 100, 1 Pendiente)",
               (m.get("month"), m.get("total_in"), m.get("total_out"), (m.get("pending") or {}).get("count"))
               == ("2026-06", "800.00", "100.00", 1),
               (m.get("month"), m.get("total_in"), m.get("total_out"), (m.get("pending") or {}).get("count")))

        y = (home.get("years") or [{}])[0]
        expect("el año acumulado sale de las fotos (2026: entró 800, salió 100; Materials 100)",
               (y.get("year"), y.get("total_in"), y.get("total_out"), (y.get("out_by_category") or [{}])[0].get("category"),
                (y.get("months") or [{}])[-1].get("cum_out")) == (2026, "800.00", "100.00", "Materials", "100.00"),
               (y.get("year"), y.get("total_in"), y.get("total_out")))

        r = client.patch(f"/api/transactions/{tx_id}", headers=bk, json={"category": "Supplies"})
        expect("editar a mano un movimiento de junio → 409 month_closed",
               r.status_code == 409 and r.json().get("detail") == "month_closed", f"{r.status_code} {r.json()}")
        r = client.post(f"/api/companies/{company_id}/rules", headers=bk,
                        json={"match_text": "HOME DEPOT", "account_type": "OPERATING_EXPENSE", "category": "Supplies"})
        with Session(engine) as s:
            after = s.get(Transaction, tx_id)
            cat = (after.account_type.value if after.account_type else None, after.category)
        expect("una regla nueva SALTA el mes cerrado (la regla se crea, junio no cambia)",
               r.status_code == 200 and cat == ("COGS", "Materials"), f"{r.status_code} · {cat}")

        with Session(engine) as s:                          # el banco asienta tarde un movimiento de junio
            s.add(Transaction(company_id=company_id, date=date(2026, 6, 28), description="LATE BANK ROW",
                              amount=Decimal("30.00"), is_inflow=False))
            s.commit()
        months = {x["month"]: x for x in client.get(base, headers=bk).json()}
        expect("lo que llega tarde entra y se avisa (late=1); la foto no cambia",
               months.get("2026-06", {}).get("late") == 1 and
               client.get(f"/api/owner/companies/{company_id}", headers=cl).json()["closed_months"][0]["total_out"] == "100.00",
               months.get("2026-06"))

        r = client.post(f"{base}/2026-06/reopen", headers=bk)
        expect("reabrir junio", r.status_code == 200, r.status_code)
        home = client.get(f"/api/owner/companies/{company_id}", headers=cl).json()
        expect("reabierto: el dueño deja de verlo", home.get("closed_months") == [], home.get("closed_months"))
        r = client.patch(f"/api/transactions/{tx_id}", headers=bk, json={"category": "Supplies"})
        expect("reabierto: la edición a mano vuelve a pasar", r.status_code == 200, r.status_code)
    finally:
        with Session(engine) as s:
            drop_fixtures(s)
    return fails


def main() -> int:
    # Un router INCLUIDO (FastAPI ≥ 0.137) queda en `app.routes` como una sola entrada sin `path`, y todos los
    # barridos de abajo lo saltarían en silencio. Se exige registrar las rutas planas (ver el final de main.py).
    hidden = [type(r).__name__ for r in app.routes if not getattr(r, "path", None)]
    if hidden:
        print(f"  🔴 Rutas que este barrido NO ve (¿un `include_router`?): {hidden}")
        return 1
    with Session(engine) as s:
        drop_fixtures(s)              # por si una corrida anterior murió a medias
        company_id, tx_id = build_fixtures(s)

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-Dev-Uid": CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}

    # `999999` en cualquier otro id: el recurso NO es del cliente, así que un 404 también es un
    # rechazo legítimo. Lo que NUNCA puede pasar es un 2xx.
    subs = {"company_id": str(company_id), "transaction_id": str(tx_id)}

    leaks: list[str] = []
    unproven: list[str] = []
    checked = 0

    try:
        for route in app.routes:
            tmpl = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or set()
            if not tmpl or not tmpl.startswith("/api"):
                continue
            for method in sorted(methods - {"HEAD", "OPTIONS"}):
                key = (method, tmpl)
                if key in SKIP:
                    print(f"  ⏭️  {method:6} {tmpl}  (no aplica)")
                    continue

                url = tmpl
                for name in ("company_id", "transaction_id"):
                    url = url.replace("{" + name + "}", subs[name])
                # Cualquier otro id: uno que no existe.
                while "{" in url:
                    start = url.index("{"); end = url.index("}") + 1
                    url = url[:start] + "999999" + url[end:]

                kwargs = {"headers": headers}
                if key in BODIES:
                    kwargs["json"] = _fill(BODIES[key], tx_id)
                elif key in FILES:
                    kwargs["files"] = FILES[key]
                if key in QUERY:
                    kwargs["params"] = QUERY[key]
                r = client.request(method, url, **kwargs)
                checked += 1
                allowed = key in CLIENT_ALLOWED
                ok2xx = 200 <= r.status_code < 300

                if allowed:
                    if r.status_code == 403:
                        leaks.append(f"BLOQUEADA pero debería pasar: {method} {tmpl} → 403")
                        print(f"  ❌ {method:6} {tmpl}  → 403 (¡debería pasar!)")
                    else:
                        print(f"  ✅ {method:6} {tmpl}  → {r.status_code} (permitida)")
                elif ok2xx:
                    leaks.append(f"🔴 FUGA: {method} {tmpl} → {r.status_code} (el cliente pudo)")
                    print(f"  🔴 {method:6} {tmpl}  → {r.status_code}  ¡FUGA!")
                elif r.status_code == 403:
                    print(f"  ✅ {method:6} {tmpl}  → 403 (rechazada)")
                elif r.status_code == 422:
                    unproven.append(f"{method} {tmpl}")
                    print(f"  ⚠️  {method:6} {tmpl}  → 422 (el cuerpo se validó ANTES del permiso: "
                          f"agrega su cuerpo a BODIES para probarlo de verdad)")
                else:
                    print(f"  ✅ {method:6} {tmpl}  → {r.status_code} (rechazada)")
    finally:
        with Session(engine) as s:
            drop_fixtures(s)

    # ──────────────────────────────────────────────────────────────────────────────────────────
    # SEGUNDA DIRECCIÓN: que el BOOKKEEPER no haya quedado bloqueado.
    # Una prueba que solo comprueba rechazos pasaría igual si se bloqueara a TODO el mundo. Aquí se
    # exige lo contrario: el dueño de la oficina no puede recibir 403 en ninguna ruta de su libro.
    # ──────────────────────────────────────────────────────────────────────────────────────────
    print("\n  --- el bookkeeper de la oficina NO debe recibir 403 ---")
    bk_headers = {"X-Dev-Uid": BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    with Session(engine) as s:
        company_id, tx_id = build_fixtures(s)
    subs["company_id"] = str(company_id)
    subs["transaction_id"] = str(tx_id)
    blocked: list[str] = []
    try:
        for route in app.routes:
            tmpl = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or set()
            if not tmpl or not tmpl.startswith("/api"):
                continue
            for method in sorted(methods - {"HEAD", "OPTIONS"}):
                key = (method, tmpl)
                if key in SKIP:
                    continue
                url = tmpl
                for name in ("company_id", "transaction_id"):
                    url = url.replace("{" + name + "}", subs[name])
                while "{" in url:
                    a = url.index("{"); b = url.index("}") + 1
                    url = url[:a] + "999999" + url[b:]
                kwargs = {"headers": bk_headers}
                if key in BODIES:
                    kwargs["json"] = _fill(BODIES[key], tx_id)
                elif key in FILES:
                    kwargs["files"] = FILES[key]
                if key in QUERY:
                    kwargs["params"] = QUERY[key]
                r = client.request(method, url, **kwargs)
                if key in OWNER_ONLY:
                    if r.status_code != 403:
                        blocked.append(f"{method} {tmpl} → {r.status_code}: es SOLO del dueño y el bookkeeper pudo")
                        print(f"  🔴 {method:6} {tmpl}  → {r.status_code} (¡solo del dueño!)")
                    continue
                if r.status_code == 403:
                    blocked.append(f"{method} {tmpl} → 403: {r.json().get('detail')}")
                    print(f"  ❌ {method:6} {tmpl}  → 403 (¡el bookkeeper quedó bloqueado!)")
        if not blocked:
            print("  ✅ El bookkeeper pasa en todas: ningún 403.")
    finally:
        with Session(engine) as s:
            drop_fixtures(s)
    leaks.extend(blocked)

    leaks.extend(check_anonymous_tokens(client))
    leaks.extend(check_demo_cleanup_firebase())
    leaks.extend(check_demo_doors(client))
    leaks.extend(check_owner_portal(client))
    leaks.extend(check_month_close(client))

    print(f"\n  Rutas comprobadas: {checked}  ·  permitidas al cliente: {len(CLIENT_ALLOWED)}")
    if BLOCKED:
        print(f"  🔒 Conexiones a internet BLOQUEADAS durante la prueba: {sorted(BLOCKED)}")
    if unproven:
        print(f"  ⚠️  Sin probar ({len(unproven)}): {', '.join(unproven)}")
    if leaks:
        print("\n  🔴 FALLÓ:")
        for x in leaks:
            print("   -", x)
        return 1
    print("  ✅ Ninguna fuga. El cliente no alcanza nada fuera de su lista blanca.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
