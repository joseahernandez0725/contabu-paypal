"""
PRUEBA DE LOS RECORDATORIOS DE COBRO Y EL QR, SIN RED (FASE 16, 1-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_reminders.py

Reemplaza a Nemotron y a PayPal por unos de mentira que GUARDAN lo que recibieron y comprueba, sobre un libro de
prueba que crea y borra:
  · 🔒 a la IA no le llegan ni el cliente, ni el negocio, ni el monto: solo idioma, tono y estado (días contados por el código);
  · el código rechaza un borrador con cifras propias o sin {amount}/{link} (reintenta una vez y si no, 502);
  · el código pone los datos reales (monto, número, fecha larga en ES, enlace de pago);
  · el recordatorio oficial: uno cada 24 h (el segundo → 409); solo facturas abiertas; solo el dueño;
  · el QR sale del multipart de PayPal.
Código de salida 1 si algo falla. No sale a internet (usa el bloqueo de check_permissions).
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

os.environ.setdefault("AUTH_DEV_MODE", "true")

import check_permissions as cp  # noqa: E402  (sus datos de prueba y su bloqueo de internet)
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

import invoice_reminders as ir  # noqa: E402
import nemotron  # noqa: E402
import paypal  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import Invoice, InvoiceCustomer, PayPalConnection  # noqa: E402

fails: list[str] = []
SEEN: list[str] = []          # lo que le llegó a la "IA"
ANSWERS: list[dict] = []      # lo que contesta la "IA", en orden
PAYPAL: list[tuple] = []      # lo que se le pidió a "PayPal"


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def fake_chat_json(system, user, schema=None, **kw):
    SEEN.append(user)
    return nemotron.Reply(data=dict(ANSWERS.pop(0)), usage=None, raw_text="")


KEYS: list = []               # con qué llaves se llamó a "PayPal" (las del libro, no las de la app)


def fake_api(method, path, **kw):
    PAYPAL.append((method, path, kw.get("json")))
    KEYS.append(paypal._own_keys.get())
    return {}


GOOD = {"subject": "Invoice {number} from {business}",
        "body": "Hi {customer}, a friendly note: invoice {number} for {amount} was due on {due_date}, {days_overdue} ago. "
                "You can pay by card or PayPal here: {link}. Thank you! {business}"}


def main() -> int:
    nemotron.chat_json = fake_chat_json
    paypal.api = fake_api
    client = TestClient(app, raise_server_exceptions=False)
    cl = {"X-Dev-Uid": cp.CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": cp.BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    today = date.today()
    with Session(engine) as s:
        cp.drop_fixtures(s)
        company_id, _ = cp.build_fixtures(s)
        # Una conexión con llaves PROPIAS (3-nov): el recordatorio tiene que usar las de este libro.
        s.add(PayPalConnection(company_id=company_id, mode="own_keys", environment="sandbox", client_id="CID-LIBRO",
                               client_secret_enc=paypal.encrypt_token("secret-del-libro")))
        c = InvoiceCustomer(company_id=company_id, name="Zoltan Quimby", email="ap@zq.test")
        s.add(c)
        s.flush()
        sent = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id="INV2-TEST", number="0042",
                       concept="Kitchen remodel", amount=Decimal("1250.00"), due_date=today - timedelta(days=12),
                       status="SENT", recipient_view_url="https://www.sandbox.paypal.com/invoice/p/#TEST")
        draft = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id="INV2-DRAFT", concept="X",
                        amount=Decimal("10.00"), due_date=today + timedelta(days=5), status="DRAFT")
        s.add(sent)
        s.add(draft)
        s.commit()
        sent_id, draft_id = sent.id, draft.id
    base = f"/api/owner/companies/{company_id}/invoices"
    try:
        print("\n  --- recordatorios y QR (sin red) ---")
        r = client.post(f"{base}/{sent_id}/reminder-draft", headers=bk, json={"lang": "es"})
        expect("el bookkeeper NO redacta recordatorios (solo el dueño)", r.status_code == 403, r.status_code)

        ANSWERS[:] = [GOOD]
        r = client.post(f"{base}/{sent_id}/reminder-draft", headers=cl, json={"lang": "es", "tone": "friendly"})
        b = r.json()
        expect("🔒 a la IA le llega solo idioma, tono y estado (días contados por el código)",
               r.status_code == 200 and SEEN[-1] == "LANGUAGE: es\nTONE: friendly\nSTATE: overdue", SEEN[-1:])
        leaked = [w for w in ("zoltan", "quimby", "1250", "1,250", "0042", "permcheck") if w in SEEN[-1].lower()]
        expect("🔒 ni el cliente, ni el monto, ni el número, ni el negocio llegan a la IA", not leaked, leaked)
        due_es = ir._long_date(today - timedelta(days=12), "es")
        expect("el código pone los datos reales (nombre, $1,250.00, #0042, fecha larga en ES, enlace)",
               all(x in b.get("body", "") for x in ("Hi Zoltan", "$1,250.00 USD", "0042", due_es, "12 días", "#TEST")),
               b.get("body", "")[:120])
        expect("mailto al correo del cliente guardado", (b.get("mailto") or "").startswith("mailto:ap@zq.test?subject="),
               (b.get("mailto") or "")[:40])

        bad_digits = {"subject": "Invoice", "body": "Please pay $1,250 here: {link} — {amount}"}
        no_link = {"subject": "Invoice", "body": "Please pay {amount} soon."}
        ANSWERS[:] = [bad_digits, no_link]
        r = client.post(f"{base}/{sent_id}/reminder-draft", headers=cl, json={"lang": "en"})
        expect("cifras propias o sin {link} → rechazado, 1 reintento y 502 (nunca un texto con montos de la IA)",
               r.status_code == 502 and not ANSWERS, (r.status_code, len(ANSWERS)))
        ANSWERS[:] = [bad_digits, GOOD]
        r = client.post(f"{base}/{sent_id}/reminder-draft", headers=cl, json={"lang": "en"})
        expect("si el reintento sale bien, se usa", r.status_code == 200 and "$1,250.00" in r.json().get("body", ""),
               r.status_code)
        r = client.post(f"{base}/{draft_id}/reminder-draft", headers=cl, json={"lang": "en"})
        expect("un borrador (no enviado) no se recuerda → 409", r.status_code == 409, r.status_code)

        PAYPAL.clear()
        r = client.post(f"{base}/{sent_id}/remind", headers=cl)
        inv = next((i for i in r.json().get("invoices", []) if i["id"] == sent_id), {})
        expect("recordatorio oficial: llama a /remind sin texto propio y cuenta 1",
               r.status_code == 200 and PAYPAL == [("POST", "/v2/invoicing/invoices/INV2-TEST/remind",
                                                    {"send_to_recipient": True, "send_to_invoicer": False})]
               and inv.get("reminder_count") == 1 and (inv.get("last_reminder_at") or "").endswith("Z"),
               (r.status_code, PAYPAL, inv.get("reminder_count")))
        expect("🔑 el recordatorio usa las llaves de ESTE libro, no las de la app",
               KEYS[-1:] == [("CID-LIBRO", "secret-del-libro")], KEYS[-1:])
        r = client.post(f"{base}/{sent_id}/remind", headers=cl)
        expect("un segundo recordatorio antes de 24 h → 409 y PayPal no se llama",
               r.status_code == 409 and len(PAYPAL) == 1, (r.status_code, len(PAYPAL)))
        r = client.post(f"{base}/{sent_id}/remind", headers=bk)
        expect("el bookkeeper no manda recordatorios → 403", r.status_code == 403, r.status_code)

        mp = (b'--abc\r\nContent-Disposition: form-data; name="image"\r\nContent-Type: text/plain\r\n\r\n'
              b'iVBORw0KGgoAAAANSUhEUg==\r\n--abc--\r\n')
        expect("el QR sale del multipart de PayPal", ir.parse_qr("multipart/form-data; boundary=abc", mp)
               == "iVBORw0KGgoAAAANSUhEUg==", ir.parse_qr("multipart/form-data; boundary=abc", mp))
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
