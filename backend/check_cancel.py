"""
PRUEBA DE CANCELAR UNA FACTURA Y DESCARTAR UN BORRADOR, SIN RED (FASE 16, 3-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_cancel.py

Reemplaza a PayPal por uno de mentira y comprueba, sobre un libro de prueba que crea y borra:
  · cancelar: solo el dueño, solo una enviada y sin cobrar; PayPal recibe si avisa al cliente (sin texto propio: PayPal
    lo ignora); la factura queda CANCELLED y deja de contar en "por cobrar" (SQL); el libro no cambia;
  · descartar: solo un borrador; se borra en PayPal y aquí; una enviada no se descarta.
Código de salida 1 si algo falla. No sale a internet (usa el bloqueo de check_permissions).
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

os.environ.setdefault("AUTH_DEV_MODE", "true")

import check_permissions as cp  # noqa: E402  (sus datos de prueba y su bloqueo de internet)
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import paypal  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import Invoice, InvoiceCustomer, PayPalConnection, Transaction  # noqa: E402

fails: list[str] = []
CALLS: list[tuple] = []
STATUS = {"INV2-S": "SENT"}


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def fake_api(method, path, **kw):
    CALLS.append((method, path, kw.get("json")))
    pid = path.split("/")[4] if path.startswith("/v2/invoicing/invoices/") else ""
    if method == "POST" and path.endswith("/cancel"):
        STATUS[pid] = "CANCELLED"
        return {}
    if method == "GET" and pid:
        return {"status": STATUS.get(pid, "SENT")}
    return {}


def main() -> int:
    paypal.api = fake_api
    client = TestClient(app, raise_server_exceptions=False)
    cl = {"X-Dev-Uid": cp.CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": cp.BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    today = date.today()
    with Session(engine) as s:
        cp.drop_fixtures(s)
        company_id, _ = cp.build_fixtures(s)
        s.add(PayPalConnection(company_id=company_id, mode="first_party", environment="sandbox"))
        c = InvoiceCustomer(company_id=company_id, name="Zoltan Quimby", email="zq@test.local")
        s.add(c)
        s.flush()
        c_id = c.id
        sent = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id="INV2-S", number="0010", concept="Job",
                       amount=Decimal("400.00"), due_date=today + timedelta(days=9), status="SENT")
        draft = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id="INV2-D", number="0011", concept="Oops",
                        amount=Decimal("15.00"), due_date=today + timedelta(days=9), status="DRAFT")
        s.add(sent)
        s.add(draft)
        s.commit()
        sent_id, draft_id = sent.id, draft.id
        n_tx = len(s.exec(select(Transaction).where(Transaction.company_id == company_id)).all())
    base = f"/api/owner/companies/{company_id}/invoices"
    try:
        print("\n  --- cancelar y descartar (sin red) ---")
        before = client.get(base, headers=cl).json()["summary"]["outstanding"]
        r = client.post(f"{base}/{sent_id}/cancel", headers=bk, json={"notify": True})
        expect("el bookkeeper NO cancela facturas (solo el dueño)", r.status_code == 403, r.status_code)
        r = client.post(f"{base}/{draft_id}/cancel", headers=cl, json={"notify": True})
        expect("un borrador no se cancela (se descarta) → 409", r.status_code == 409, r.status_code)

        CALLS.clear()
        r = client.post(f"{base}/{sent_id}/cancel", headers=cl, json={"notify": False})
        post = next((x for x in CALLS if x[0] == "POST"), None)
        expect("PayPal recibe solo si avisa al cliente (sin asunto ni nota: los ignora)",
               post == ("POST", "/v2/invoicing/invoices/INV2-S/cancel", {"send_to_recipient": False, "send_to_invoicer": False}),
               post)
        v = r.json()
        inv = next((i for i in v.get("invoices", []) if i["id"] == sent_id), {})
        expect("la factura queda CANCELLED", inv.get("status") == "CANCELLED", inv.get("status"))
        expect("deja de contar en 'por cobrar' (SQL): 400 → 0", before == "400.00" and v["summary"]["outstanding"] == "0",
               (before, v["summary"]["outstanding"]))
        r = client.post(f"{base}/{sent_id}/cancel", headers=cl, json={"notify": True})
        expect("cancelada otra vez → 409", r.status_code == 409, r.status_code)

        with Session(engine) as s:
            d2 = Invoice(company_id=company_id, customer_id=c_id, paypal_invoice_id="INV2-Q", number="0012", concept="QR",
                         amount=Decimal("80.00"), due_date=today, status="DRAFT")
            s.add(d2)
            s.commit()
            d2_id = d2.id
        STATUS["INV2-Q"] = "SENT"
        CALLS.clear()
        r = client.post(f"{base}/{d2_id}/send", headers=cl, json={"notify": False})
        post = next((x for x in CALLS if x[0] == "POST"), None)
        expect("cobro con QR en persona: se envía SIN correo al cliente",
               r.status_code == 200 and post and post[2] == {"send_to_recipient": False, "send_to_invoicer": False}, post)
        CALLS.clear()
        client.post(f"{base}/{d2_id}/cancel", headers=cl, json={"notify": False})   # (se deja limpia)

        r = client.post(f"{base}/{sent_id}/discard", headers=cl)
        expect("una enviada (o cancelada) no se descarta → 409", r.status_code == 409, r.status_code)
        r = client.post(f"{base}/{draft_id}/discard", headers=bk)
        expect("el bookkeeper NO descarta borradores", r.status_code == 403, r.status_code)
        CALLS.clear()
        r = client.post(f"{base}/{draft_id}/discard", headers=cl)
        expect("descartar: DELETE en PayPal y desaparece de la lista",
               r.status_code == 200 and ("DELETE", "/v2/invoicing/invoices/INV2-D", None) in CALLS
               and all(i["id"] != draft_id for i in r.json().get("invoices", [])), (r.status_code, CALLS))
        with Session(engine) as s:
            n_after = len(s.exec(select(Transaction).where(Transaction.company_id == company_id)).all())
        expect("el libro no cambia (base caja: una factura sin cobrar no tiene filas)", n_after == n_tx, (n_tx, n_after))
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
