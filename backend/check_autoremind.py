"""
PRUEBA DE LOS RECORDATORIOS AUTOMÁTICOS DE PAYPAL, SIN RED (FASE 16, 3-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_autoremind.py

Reemplaza a PayPal por uno de mentira que se comporta como el sandbox (✅ probado el 3-nov: `setup-reminders` crea las
dos reglas INACTIVAS; el PUT cambia días y repeticiones e IGNORA el estado; `resume`/`suspend` activan y desactivan) y
comprueba: leer (dueño y bookkeeper); cambiar (solo el dueño); límites 1–7 y "antes" siempre 1 vez; si la cuenta nunca
las tuvo, se crean primero; el estado se cambia con resume/suspend, no con el PUT; sin cambios, no se llama a PayPal.
Código de salida 1 si algo falla. No sale a internet (usa el bloqueo de check_permissions).
"""

import os
import sys

os.environ.setdefault("AUTH_DEV_MODE", "true")

import check_permissions as cp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

import paypal  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402
from models import Invoice, InvoiceCustomer, PayPalConnection  # noqa: E402

fails: list[str] = []
CALLS: list[tuple] = []
PP: dict = {}                 # la "cuenta de PayPal": tipo → regla


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def fake_api(method, path, **kw):
    CALLS.append((method, path, kw.get("json")))
    if method == "GET" and path == "/v2/invoicing/reminders":
        return {"configurations": [dict(c) for c in PP.values()]}
    if method == "POST" and path == "/v2/invoicing/setup-reminders":
        PP["BEFORE_DUE"] = {"id": "RC-B", "type": "BEFORE_DUE", "status": "INACTIVE", "interval": {"unit": "DAY", "value": 2}, "repetition": 1}
        PP["AFTER_DUE"] = {"id": "RC-A", "type": "AFTER_DUE", "status": "INACTIVE", "interval": {"unit": "DAY", "value": 2}, "repetition": 2}
        return {}
    by_id = {c["id"]: c for c in PP.values()}
    rid = path.split("/")[4] if path.startswith("/v2/invoicing/reminders/") else ""
    if method == "PUT" and rid in by_id:
        by_id[rid].update(interval=kw["json"]["interval"], repetition=kw["json"]["repetition"])   # el estado, no
        return {}
    if method == "POST" and path.endswith(("/resume", "/suspend")) and rid in by_id:
        by_id[rid]["status"] = "ACTIVE" if path.endswith("/resume") else "INACTIVE"
        return {}
    if method == "POST" and path == "/v2/invoicing/invoices/INV2-NONE/cancel-reminders":
        raise paypal.PayPalError("UNPROCESSABLE_ENTITY", "id")        # ✅ sandbox: sin automáticos programados
    if method == "POST" and path.endswith("/cancel-reminders"):
        return {}
    return {}


def main() -> int:
    paypal.api = fake_api
    client = TestClient(app, raise_server_exceptions=False)
    cl = {"X-Dev-Uid": cp.CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": cp.BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    with Session(engine) as s:
        cp.drop_fixtures(s)
        company_id, _ = cp.build_fixtures(s)
        s.add(PayPalConnection(company_id=company_id, mode="first_party", environment="sandbox"))
        c = InvoiceCustomer(company_id=company_id, name="Zoltan Quimby", email="zq@test.local")
        s.add(c)
        s.flush()
        due = date.today() + timedelta(days=7)
        a = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id="INV2-AUTO", number="0050", concept="Job",
                    amount=Decimal("80.00"), due_date=due, status="SENT")
        b = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id="INV2-NONE", number="0051", concept="Job",
                    amount=Decimal("80.00"), due_date=due, status="SENT")
        s.add(a)
        s.add(b)
        s.commit()
        ia, ib = a.id, b.id
    url = f"/api/owner/companies/{company_id}/auto-reminders"
    good = {"before": {"active": True, "days": 3}, "after": {"active": True, "days": 5, "times": 4}}
    try:
        print("\n  --- recordatorios automáticos (sin red) ---")
        r = client.get(url, headers=bk)
        expect("una cuenta sin reglas: el bookkeeper lee (nada configurado)", r.status_code == 200
               and r.json() == {"before": None, "after": None}, r.json())
        r = client.put(url, headers=bk, json=good)
        expect("el bookkeeper NO cambia las reglas", r.status_code == 403, r.status_code)
        for bad, why in (({"before": {"active": True, "days": 8}, "after": good["after"]}, "8 días"),
                         ({"before": good["before"], "after": {"active": True, "days": 2, "times": 0}}, "0 veces"),
                         ({"before": good["before"], "after": {"active": True, "days": 2, "times": 8}}, "8 veces")):
            r = client.put(url, headers=cl, json=bad)
            expect(f"fuera de los límites de PayPal ({why}) → 422", r.status_code == 422, r.status_code)
        r = client.put(url, headers=cl, json={**good, "before": {"active": True, "days": 3, "times": 5}})
        expect("'antes del vencimiento' más de 1 vez → 422 (PayPal no lo permite)", r.status_code == 422, r.status_code)
        CALLS.clear()
        r = client.put(url, headers=cl, json=good)
        v = r.json()
        expect("la primera vez se crean las reglas (setup-reminders)", any(c[1] == "/v2/invoicing/setup-reminders" for c in CALLS),
               [c[1] for c in CALLS])
        expect("quedan como pidió el dueño",
               v == {"before": {"id": "RC-B", "active": True, "days": 3, "times": 1},
                     "after": {"id": "RC-A", "active": True, "days": 5, "times": 4}}, v)
        expect("se activan con resume (el PUT ignora el estado)",
               {c[1] for c in CALLS if c[1].endswith("/resume")} == {"/v2/invoicing/reminders/RC-B/resume",
                                                                    "/v2/invoicing/reminders/RC-A/resume"}, [c[1] for c in CALLS])
        CALLS.clear()
        r = client.put(url, headers=cl, json={**good, "after": {"active": False, "days": 5, "times": 4}})
        expect("desactivar 'después': solo suspend, sin PUT (los días no cambiaron)",
               [c[:2] for c in CALLS if c[0] != "GET"] == [("POST", "/v2/invoicing/reminders/RC-A/suspend")]
               and r.json()["after"]["active"] is False, [c[:2] for c in CALLS if c[0] != "GET"])
        CALLS.clear()
        client.put(url, headers=cl, json={**good, "after": {"active": False, "days": 5, "times": 4}})
        expect("sin cambios, solo se lee (ninguna escritura en PayPal)", all(c[0] == "GET" for c in CALLS), [c[:2] for c in CALLS])
        print("\n  --- detener los automáticos de UNA factura (5-nov) ---")
        inv = f"/api/owner/companies/{company_id}/invoices"
        r = client.post(f"{inv}/{ia}/stop-auto-reminders", headers=bk)
        expect("el bookkeeper NO los detiene", r.status_code == 403, r.status_code)
        CALLS.clear()
        r = client.post(f"{inv}/{ia}/stop-auto-reminders", headers=cl)
        row = next((x for x in r.json().get("invoices", []) if x["id"] == ia), {})
        expect("el dueño los detiene: PayPal cancel-reminders y la factura lo dice",
               r.status_code == 200 and ("POST", "/v2/invoicing/invoices/INV2-AUTO/cancel-reminders", {}) in CALLS
               and row.get("auto_reminders_stopped") is True, (r.status_code, row.get("auto_reminders_stopped")))
        r = client.post(f"{inv}/{ia}/stop-auto-reminders", headers=cl)
        expect("otra vez → 409 (ya detenidos; PayPal no deja reactivarlos)", r.status_code == 409, r.status_code)
        r = client.post(f"{inv}/{ib}/stop-auto-reminders", headers=cl)
        expect("sin automáticos programados en PayPal → 409 no_auto_reminders", r.status_code == 409
               and r.json().get("detail") == "no_auto_reminders", (r.status_code, r.json().get("detail")))
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
