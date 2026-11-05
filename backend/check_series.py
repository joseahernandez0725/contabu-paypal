"""
PRUEBA DE LAS FACTURAS RECURRENTES, SIN RED (FASE 16, 4-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_series.py

Reemplaza a PayPal por uno de mentira que se comporta como el sandbox (✅ 4-nov: la serie se crea en DRAFT y se activa;
la factura generada lleva el mismo `template_id` de la serie; se busca con `search-invoices`) y comprueba: validaciones;
solo el dueño programa y detiene; PayPal recibe el plan, el plazo y la plantilla con los datos del negocio; al abrir Cobros
se traen las facturas generadas de ESA serie (no otra del mismo cliente y monto) y no se duplican; detener la serie.
Código de salida 1 si algo falla. No sale a internet (usa el bloqueo de check_permissions).
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

os.environ.setdefault("AUTH_DEV_MODE", "true")

import check_permissions as cp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import paypal  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import Invoice, InvoiceCustomer, InvoiceProfile, PayPalConnection  # noqa: E402

fails: list[str] = []
CALLS: list[tuple] = []
SERIES = {"status": "DRAFT", "completed": 0}
TODAY = date.today()


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def _series():
    return {"id": "RI-1", "status": SERIES["status"],
            "plan_detail": {"completed_cycles": SERIES["completed"], "next_occurrence_date": (TODAY + timedelta(days=30)).isoformat(),
                            "start_series_date": TODAY.isoformat(), "total_cycles": 12 - SERIES["completed"],   # ✅ lo que FALTA
                            "frequency": {"interval_unit": "MONTH", "interval_count": 1}},
            "recurring_info": {"configuration": {"template_id": "TEMP-SERIE"}, "detail": {"currency_code": "USD"},
                               "invoicer": {"business_name": "B"}, "primary_recipients": [{"billing_info": {"email_address": "zq@test.local"}}],
                               "items": [{"id": "ITEM-1", "name": "Monthly maintenance plan", "quantity": "1",
                                          "unit_amount": {"currency_code": "USD", "value": "79.00"}}]}}


def _inv(iid, template, number):
    return {"id": iid, "status": "SENT", "configuration": {"template_id": template},
            "detail": {"invoice_number": number, "invoice_date": TODAY.isoformat(),
                       "payment_term": {"term_type": "NET_15", "due_date": (TODAY + timedelta(days=15)).isoformat()},
                       "metadata": {"recipient_view_url": f"https://pay/{iid}"}}}


def fake_api(method, path, **kw):
    CALLS.append((method, path, kw.get("json")))
    if method == "POST" and path == "/v2/invoicing/recurring-invoices":
        return {"id": "RI-1", "status": "DRAFT"}
    if method == "POST" and path == "/v2/invoicing/recurring-invoices/RI-1/activate":
        SERIES["status"] = "ACTIVE"
        return {}
    if method == "POST" and path == "/v2/invoicing/recurring-invoices/RI-1/cancel":
        SERIES["status"] = "CANCELLED"
        return {}
    if method == "GET" and path == "/v2/invoicing/recurring-invoices/RI-1":
        return _series()
    if method == "PUT" and path == "/v2/invoicing/recurring-invoices/RI-1":
        return {}
    if method == "POST" and path == "/v2/invoicing/search-invoices":
        return {"items": [{"id": "INV2-GEN1"}, {"id": "INV2-OTRA"}]}       # la generada y otra del mismo cliente y monto
    if method == "GET" and path == "/v2/invoicing/invoices/INV2-GEN1":
        return _inv("INV2-GEN1", "TEMP-SERIE", "RH-0200")
    if method == "GET" and path == "/v2/invoicing/invoices/INV2-OTRA":
        return _inv("INV2-OTRA", "TEMP-OTRA", "RH-0199")
    if method == "GET" and path.startswith("/v2/invoicing/invoices/"):
        return {"status": "SENT"}
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
        s.add(InvoiceProfile(company_id=company_id, city="Miami", state="FL", note="Thanks!", terms="Net 15."))
        c = InvoiceCustomer(company_id=company_id, name="Zoltan Quimby", email="zq@test.local")
        s.add(c)
        s.commit()
        cust = c.id
    base = f"/api/owner/companies/{company_id}"
    good = {"customer_id": cust, "concept": "Monthly maintenance plan", "amount": "79.00", "interval_unit": "MONTH",
            "interval_count": 1, "start_date": TODAY.isoformat(), "total_cycles": 12, "payment_term": "NET_15"}
    try:
        print("\n  --- facturas recurrentes (sin red) ---")
        r = client.post(f"{base}/series", headers=bk, json=good)
        expect("el bookkeeper NO programa series (solo el dueño)", r.status_code == 403, r.status_code)
        for field, val, code in (("interval_unit", "DAY", "bad_frequency"), ("interval_count", 13, "bad_frequency"),
                                 ("total_cycles", 100, "bad_cycles"), ("payment_term", "NET_60", "bad_term"),
                                 ("start_date", (TODAY - timedelta(days=1)).isoformat(), "bad_start"), ("amount", "-5", "bad_amount")):
            r = client.post(f"{base}/series", headers=cl, json={**good, field: val})
            expect(f"{field}={val} → 422 {code}", r.status_code == 422 and r.json().get("detail") == code, (r.status_code, r.json().get("detail")))

        CALLS.clear()
        r = client.post(f"{base}/series", headers=cl, json=good)
        create = next((c for c in CALLS if c[1] == "/v2/invoicing/recurring-invoices"), None)
        body = create[2] if create else {}
        expect("PayPal recibe el plan: cada mes, desde hoy, 12 veces",
               body.get("plan_detail") == {"frequency": {"interval_unit": "MONTH", "interval_count": 1},
                                            "start_series_date": TODAY.isoformat(), "total_cycles": 12}, body.get("plan_detail"))
        ri = body.get("recurring_info", {})
        expect("y la plantilla: plazo de 15 días, datos del negocio, nota y términos, el concepto y el monto",
               ri.get("detail", {}).get("payment_term") == {"term_type": "NET_15"} and ri.get("detail", {}).get("note") == "Thanks!"
               and ri.get("invoicer", {}).get("address", {}).get("admin_area_2") == "Miami"
               and ri.get("items", [{}])[0].get("unit_amount", {}).get("value") == "79.00", ri.get("detail"))
        expect("se activa en el mismo paso", any(c[1].endswith("/activate") for c in CALLS), [c[1] for c in CALLS])
        sr = (r.json().get("series") or [{}])[0]
        expect("la serie queda ACTIVA, con su próxima fecha", sr.get("status") == "ACTIVE" and sr.get("next_date"), sr)

        SERIES["completed"] = 1
        r = client.post(f"{base}/invoices/refresh", headers=cl)
        mine = [i for i in r.json().get("invoices", []) if i.get("series_id")]
        expect("al abrir Cobros llega la factura que generó ESA serie (no la otra del mismo cliente y monto)",
               r.status_code == 200 and [i["number"] for i in mine] == ["RH-0200"], (r.status_code, [i["number"] for i in mine]))
        expect("con su vencimiento (plazo de PayPal) y su enlace de pago",
               mine and mine[0]["due_date"] == (TODAY + timedelta(days=15)).isoformat() and mine[0]["recipient_view_url"] == "https://pay/INV2-GEN1",
               mine and (mine[0]["due_date"], mine[0]["recipient_view_url"]))
        client.post(f"{base}/invoices/refresh", headers=cl)
        with Session(engine) as s:
            n = len(s.exec(select(Invoice).where(Invoice.company_id == company_id).where(Invoice.series_id.is_not(None))).all())
        expect("abrir Cobros otra vez no la duplica", n == 1, n)

        sid = sr.get("id")
        print("\n  --- editar (5-nov) ---")
        r = client.put(f"{base}/series/{sid}", headers=bk, json={"concept": "X", "amount": "90.00"})
        expect("el bookkeeper NO edita series", r.status_code == 403, r.status_code)
        CALLS.clear()
        r = client.put(f"{base}/series/{sid}", headers=cl, json={"concept": "Monthly maintenance plan (2027)", "amount": "85.00", "payment_term": "NET_30"})
        put = next((c for c in CALLS if c[0] == "PUT"), None)
        it = (put[2]["recurring_info"]["items"][0]) if put else {}
        expect("PUT a PayPal con el nuevo concepto, monto y plazo (sin el id del ítem)",
               put and it.get("name") == "Monthly maintenance plan (2027)" and it.get("unit_amount", {}).get("value") == "85.00"
               and "id" not in it and put[2]["recurring_info"]["detail"]["payment_term"] == {"term_type": "NET_30"}, put and it)
        expect("el calendario sigue donde iba: inicio = la PRÓXIMA fecha y total = lo que falta (11 de 12)",
               put and put[2]["plan_detail"].get("start_series_date") == (TODAY + timedelta(days=30)).isoformat()
               and put[2]["plan_detail"].get("total_cycles") == 11, put and put[2]["plan_detail"])
        sr2 = (r.json().get("series") or [{}])[0]
        expect("la serie lo muestra", (sr2.get("concept"), sr2.get("amount"), sr2.get("payment_term")) == ("Monthly maintenance plan (2027)", "85.00", "NET_30"), sr2)
        r = client.put(f"{base}/series/{sid}", headers=cl, json={"concept": "X", "amount": "-1"})
        expect("monto inválido → 422", r.status_code == 422, r.status_code)
        r = client.post(f"{base}/series/{sid}/cancel", headers=bk)
        expect("el bookkeeper NO detiene series", r.status_code == 403, r.status_code)
        r = client.post(f"{base}/series/{sid}/cancel", headers=cl)
        expect("detener: CANCELLED y sin próxima fecha", (r.json().get("series") or [{}])[0].get("status") == "CANCELLED"
               and (r.json().get("series") or [{}])[0].get("next_date") is None, r.json())
        r = client.post(f"{base}/series/{sid}/cancel", headers=cl)
        expect("detenida otra vez → 409", r.status_code == 409, r.status_code)
        r = client.get(f"{base}/series", headers=bk)
        expect("el bookkeeper ve las series", r.status_code == 200 and len(r.json()["series"]) == 1, r.status_code)
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
