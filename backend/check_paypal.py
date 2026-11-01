"""
PRUEBA DE PAYPAL SIN RED (FASE 16 Paso 3, 31-oct-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_paypal.py

Reemplaza la llamada a PayPal (`paypal._search`) por movimientos de ejemplo armados con el esquema OFICIAL de
Transaction Search (transaction_info / payer_info) y comprueba, sobre un libro de prueba que crea y borra:
  · venta por el BRUTO + comisión aparte · reembolso que RESTA del ingreso · retiro al banco fuera del P&L ·
    retenciones y lo no exitoso no se asientan · el saldo inicial (T1900) queda Pendiente;
  · una REGLA del bookkeeper gana sobre el código de PayPal;
  · sincronizar dos veces no duplica; el plan del libro gana "Merchant fees" y "Returns and allowances";
  · la ventana Cuentas del dueño no trae ningún token.
Código de salida 1 si algo falla. No toca libros reales ni sale a internet (usa el bloqueo de check_permissions).
"""

import os
import sys
from decimal import Decimal

os.environ.setdefault("AUTH_DEV_MODE", "true")
os.environ["PAYPAL_CLIENT_ID"] = os.environ.get("PAYPAL_CLIENT_ID") or "check"
os.environ["PAYPAL_CLIENT_SECRET"] = os.environ.get("PAYPAL_CLIENT_SECRET") or "check"
os.environ["PAYPAL_ENV"] = "sandbox"
os.environ["PAYPAL_SANDBOX_MERCHANT_EMAIL"] = "seller@business.example.com"

import check_permissions as cp  # noqa: E402  (sus datos de prueba y su bloqueo de internet)
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import paypal  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import AccountType, CategoryRule, CompanyCategory, PayPalEntry, RuleScope, Transaction  # noqa: E402
from reports import signed_amount  # noqa: E402


def tx(tid, code, value, fee=None, status="S", name=None, day="2026-06-15"):
    info = {"transaction_id": tid, "transaction_event_code": code, "transaction_status": status,
            "transaction_initiation_date": f"{day}T18:30:00+0000",
            "transaction_amount": {"currency_code": "USD", "value": value}}
    if fee is not None:
        info["fee_amount"] = {"currency_code": "USD", "value": fee}
    return {"transaction_info": info, "payer_info": {"payer_name": {"alternate_full_name": name}} if name else {}}


SAMPLE = [
    tx("SALE1", "T0006", "100.00", "-3.49", name="Ana Lopez"),        # venta con su comisión
    tx("REF1", "T1107", "-40.00", "1.00", name="Ana Lopez"),          # reembolso; PayPal devuelve parte de la comisión
    tx("WD1", "T0400", "-50.00"),                                     # retiro al banco
    tx("HOLD1", "T2101", "-20.00"),                                   # retención: no se asienta
    tx("OPEN1", "T1900", "5000.00"),                                  # saldo inicial de la cuenta de prueba
    tx("PEND1", "T0006", "80.00", "-2.80", status="P", name="Bo"),    # pendiente: base caja, no entra
    tx("BUY1", "T0001", "-25.00", name="Home Depot"),                 # compra pagada con PayPal → la regla decide
    tx("WD2", "T0400", "-30.00", day="2026-06-20"),                   # retiro con DOS depósitos posibles en el banco
]
# Paso 4: depósitos del BANCO (otra cuenta). 50 → un solo candidato, Pendiente → se empareja solo.
# 30 → dos candidatos → lo confirma el bookkeeper. 999 → no se parece a nada.
BANK = [("2026-06-17", "PAYPAL TRANSFER PPD ID 1", "50.00"), ("2026-06-21", "PAYPAL TRANSFER PPD ID 2", "30.00"),
        ("2026-06-22", "ONLINE DEPOSIT", "30.00"), ("2026-06-18", "CLIENT CHECK", "999.00")]

fails: list[str] = []


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def main() -> int:
    paypal._search = lambda start, end: SAMPLE        # sin red: el historial de ejemplo
    client = TestClient(app, raise_server_exceptions=False)
    cl = {"X-Dev-Uid": cp.CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": cp.BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    with Session(engine) as s:
        cp.drop_fixtures(s)
        company_id, _ = cp.build_fixtures(s)
        s.add(CategoryRule(scope=RuleScope.COMPANY, company_id=company_id, match_text="HOME DEPOT", account_type=AccountType.COGS,
                           category="Materials", subcategory="HOME DEPOT"))
        from datetime import date as _d
        for day, desc, amt in BANK:
            s.add(Transaction(company_id=company_id, date=_d.fromisoformat(day), description=desc, amount=Decimal(amt),
                              is_inflow=True, bank_account="Chase Checking"))
        s.commit()
    try:
        print("\n  --- PayPal, una cuenta más del libro (sin red) ---")
        # "Conectar con PayPal" (Log in with PayPal): el `state` firmado amarra el libro y el usuario.
        url = client.post(f"/api/companies/{company_id}/paypal/authorize", headers=cl).json()["url"]
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(url).query)["state"][0]
        expect("el enlace va a PayPal (sandbox) con el regreso configurado", url.startswith("https://www.sandbox.paypal.com/connect?")
               and "redirect_uri=" in url, url[:40])
        r = client.post("/api/paypal/oauth", headers=bk, json={"code": "c", "state": state})
        expect("el `state` de OTRO usuario no sirve (400)", r.status_code == 400, r.status_code)
        r = client.post("/api/paypal/oauth", headers=cl, json={"code": "c", "state": state[:-2] + "00"})
        expect("un `state` alterado no sirve (400)", r.status_code == 400, r.status_code)
        paypal._identity = lambda code: {"email": "someone.else@personal.example.com", "payer_id": "OTHER"}
        r = client.post("/api/paypal/oauth", headers=cl, json={"code": "c", "state": state})
        expect("iniciar sesión con OTRA cuenta → 409 y se dice cuál (no se conecta)",
               r.status_code == 409 and r.json()["detail"]["code"] == "paypal_other_account", r.status_code)
        paypal._app_account_id = lambda: "ACC-DEMO"
        paypal._identity = lambda code: {"email": None, "payer_id": None, "account_id": "ACC-OTHER"}
        r = client.post("/api/paypal/oauth", headers=cl, json={"code": "c", "state": state})
        expect("por id de cuenta: OTRA cuenta de comerciante → 409", r.status_code == 409, r.status_code)
        paypal._identity = lambda code: {"email": "seller@business.example.com", "payer_id": "SELLER1"}
        r = client.post("/api/paypal/oauth", headers=cl, json={"code": "c", "state": state})
        got = r.json().get("imported") if r.status_code == 200 else r.json()
        expect("el DUEÑO conecta su PayPal y se importa", r.status_code == 200 and got.get("new") == 8, got)

        with Session(engine) as s:
            rows = {(t.description, t.amount, t.is_inflow): (t.account_type.value if t.account_type else None, t.category, t.source)
                    for t in s.exec(select(Transaction).where(Transaction.company_id == company_id)
                                    .where(Transaction.bank_account == "PayPal")).all()}
            sec = dict(s.exec(select(Transaction.account_type, func.sum(signed_amount()))
                              .where(Transaction.company_id == company_id).where(Transaction.bank_account == "PayPal")
                              .group_by(Transaction.account_type)).all())
            fees = s.exec(select(func.sum(signed_amount())).where(Transaction.company_id == company_id)
                          .where(Transaction.category == "Merchant fees")).one()
            chart = {c.name for c in s.exec(select(CompanyCategory).where(CompanyCategory.company_id == company_id)).all()}
        D = Decimal
        expect("venta por el bruto → ingreso", rows.get(("PAYPAL PAYMENT FROM ANA LOPEZ", D("100.00"), True)) ==
               ("REVENUE", "Gross receipts or sales", "paypal"), rows.get(("PAYPAL PAYMENT FROM ANA LOPEZ", D("100.00"), True)))
        expect("la comisión, aparte, en Merchant fees", rows.get(("PAYPAL FEE", D("3.49"), False)) ==
               ("OPERATING_EXPENSE", "Merchant fees", "paypal"), rows.get(("PAYPAL FEE", D("3.49"), False)))
        expect("el reembolso resta del ingreso (Revenue = 100 − 40)", sec.get(AccountType.REVENUE) == D("60.00"),
               sec.get(AccountType.REVENUE))
        expect("la comisión devuelta resta del gasto (3.49 − 1.00)", fees == D("2.49"), fees)
        expect("retiro al banco → Transfer, fuera del P&L", rows.get(("PAYPAL TRANSFER TO BANK", D("50.00"), False)) ==
               ("NON_PL", "Transfer", "paypal"), rows.get(("PAYPAL TRANSFER TO BANK", D("50.00"), False)))
        expect("el saldo inicial T1900 queda Pendiente", rows.get(("PAYPAL T1900", D("5000.00"), True)) == (None, None, None),
               rows.get(("PAYPAL T1900", D("5000.00"), True)))
        expect("la REGLA del bookkeeper gana (Home Depot → Materials)",
               rows.get(("PAYPAL PAYMENT TO HOME DEPOT", D("25.00"), False)) == ("COGS", "Materials", "rule"),
               rows.get(("PAYPAL PAYMENT TO HOME DEPOT", D("25.00"), False)))
        expect("la retención y lo pendiente no se asientan", len(rows) == 8, len(rows))
        expect("…pero se DECLARAN (regla 4): 1 retención, 1 pendiente", got.get("skipped") == {"hold": 1, "pending": 1},
               got.get("skipped"))
        expect("el plan gana Merchant fees y Returns and allowances", {"Merchant fees", "Returns and allowances"} <= chart,
               sorted({"Merchant fees", "Returns and allowances"} - chart) or "ok")

        # Candado: la MISMA cuenta de PayPal no se conecta a un segundo libro.
        with Session(engine) as s:
            from models import Company, PayPalConnection
            other = Company(name="PERMCHECK SECOND BOOK", sector="construction", firm_id=s.get(Company, company_id).firm_id,
                            user_id=s.get(Company, company_id).user_id)
            s.add(other); s.commit(); s.refresh(other); other_id = other.id
        url2 = client.post(f"/api/companies/{other_id}/paypal/authorize", headers=bk).json()["url"]
        r = client.post("/api/paypal/oauth", headers=bk, json={"code": "c", "state": parse_qs(urlparse(url2).query)["state"][0]})
        expect("la misma cuenta de PayPal NO se conecta a un segundo libro (409 paypal_in_use)",
               r.status_code == 409 and r.json()["detail"]["code"] == "paypal_in_use", r.status_code)

        r = client.post(f"/api/companies/{company_id}/paypal/sync", headers=bk)
        got = r.json().get("imported") if r.status_code == 200 else r.json()
        expect("sincronizar otra vez no duplica", r.status_code == 200 and got.get("new") == 0 and got.get("known") == 8, got)

        # ── Paso 4: retiro de PayPal ↔ depósito del banco ──
        v = client.get(f"/api/companies/{company_id}/paypal/transfers", headers=bk).json()
        with Session(engine) as s:
            dep50 = s.exec(select(Transaction).where(Transaction.company_id == company_id)
                           .where(Transaction.description == "PAYPAL TRANSFER PPD ID 1")).one()
            d50 = (dep50.account_type.value if dep50.account_type else None, dep50.category, dep50.source)
        expect("un solo depósito Pendiente → se empareja SOLO como Transfer", len(v["matched"]) == 1 and
               v["matched"][0]["status"] == "auto" and d50 == ("NON_PL", "Transfer", "transfer"), (len(v["matched"]), d50))
        prop = v["proposals"][0] if v["proposals"] else {}
        expect("dos depósitos posibles → lo confirma el bookkeeper (2 candidatos)",
               len(v["proposals"]) == 1 and len(prop.get("candidates", [])) == 2, len(prop.get("candidates", [])))
        r = client.post(f"/api/companies/{company_id}/paypal/transfers/{prop['withdrawal']['id']}/confirm",
                        headers=cl, json={"bank_tx_id": prop["candidates"][1]["id"]})
        expect("el dueño NO confirma transferencias", r.status_code == 403, r.status_code)
        r = client.post(f"/api/companies/{company_id}/paypal/transfers/{prop['withdrawal']['id']}/confirm",
                        headers=bk, json={"bank_tx_id": prop["candidates"][1]["id"]})
        with Session(engine) as s:
            picked = s.get(Transaction, prop["candidates"][1]["id"]); other = s.get(Transaction, prop["candidates"][0]["id"])
            got = ((picked.category, picked.source), other.account_type)
        expect("confirmado: el elegido queda Transfer como decisión del bookkeeper; el otro, intacto",
               r.status_code == 200 and got == (("Transfer", "manual"), None), (r.status_code, got))
        with Session(engine) as s:
            sec = dict(s.exec(select(Transaction.account_type, func.sum(signed_amount()))
                              .where(Transaction.company_id == company_id).group_by(Transaction.account_type)).all())
        expect("la venta NO se cuenta dos veces (Revenue sigue en 60)", sec.get(AccountType.REVENUE) == Decimal("60.00"),
               sec.get(AccountType.REVENUE))
        r = client.post(f"/api/companies/{company_id}/paypal/sync", headers=cl)
        expect("el dueño NO sincroniza (eso es del bookkeeper)", r.status_code == 403, r.status_code)

        # ── Paso 5: Cobros (la Invoicing API, de mentira: sin red) ──
        state = {"status": "DRAFT", "amount": "120.00"}
        def fake_api(method, path, **kw):
            if method == "POST" and path == "/v2/invoicing/invoices":
                return {"id": "INV2-TEST", "status": "DRAFT", "detail": {"invoice_number": "0001"},
                        "amount": {"currency_code": "USD", "value": state["amount"]}}
            if path.endswith("/send"):
                state["status"] = "SENT"; return {}
            body = {"id": "INV2-TEST", "status": state["status"],
                    "detail": {"metadata": {"recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#INV2TEST"}}}
            if state["status"] == "PAID":
                body["payments"] = {"transactions": [{"type": "PAYPAL", "payment_id": "INVPAY1", "payment_date": "2026-06-25",
                                                      "amount": {"currency_code": "USD", "value": "120.00"}}]}
            return body
        paypal.api = fake_api
        base = f"/api/owner/companies/{company_id}"
        r = client.post(f"{base}/customers", headers=bk, json={"name": "Acme Corp", "email": "ap@acme.test"})
        expect("el bookkeeper NO agrega clientes ni factura (solo el dueño)", r.status_code == 403, r.status_code)
        cust = client.post(f"{base}/customers", headers=cl, json={"name": "Acme Corp", "email": "ap@acme.test"}).json()
        r = client.post(f"{base}/invoices", headers=cl, json={"customer_id": cust["id"], "concept": "Kitchen remodel",
                                                              "amount": "120.001", "due_date": "2099-01-01"})
        expect("el código valida el monto (3 decimales → 422)", r.status_code == 422, r.status_code)
        r = client.post(f"{base}/invoices", headers=cl, json={"customer_id": cust["id"], "concept": "Kitchen remodel",
                                                              "amount": "120.00", "due_date": "2099-01-01"})
        inv = (r.json().get("invoices") or [{}])[0] if r.status_code == 200 else {}
        expect("el dueño crea un BORRADOR (no se envía nada)", r.status_code == 200 and inv.get("status") == "DRAFT", inv.get("status"))
        r = client.post(f"{base}/invoices/{inv['id']}/send", headers=bk)
        expect("el bookkeeper NO envía facturas", r.status_code == 403, r.status_code)
        r = client.post(f"{base}/invoices/{inv['id']}/send", headers=cl)
        sent = r.json()["invoices"][0] if r.status_code == 200 else {}
        expect("enviada: estado SENT y el enlace de pago del cliente", sent.get("status") == "SENT" and
               (sent.get("recipient_view_url") or "").startswith("https://www.sandbox.paypal.com/"), sent.get("status"))
        state["status"] = "PAID"
        r = client.post(f"{base}/invoices/refresh", headers=cl)
        paid = r.json()["invoices"][0] if r.status_code == 200 else {}
        with Session(engine) as s:
            sale = s.exec(select(Transaction).where(Transaction.company_id == company_id)
                          .where(Transaction.description == "PAYPAL INVOICE 0001 FROM ACME CORP")).first()
        expect("pagada: la venta entra SOLA al libro, ligada a su factura (ingreso por el bruto)",
               paid.get("status") == "PAID" and paid.get("in_books") and sale is not None and sale.amount == Decimal("120.00")
               and sale.category == "Gross receipts or sales", (paid.get("status"), paid.get("in_books")))
        expect("las cifras: cobrado en el año 120, por cobrar 0", r.json()["summary"]["collected_this_year"] == "120.00"
               and r.json()["summary"]["outstanding"] == "0", r.json()["summary"])
        # Transaction Search trae después el MISMO pago con su comisión: la venta no se duplica, la comisión entra.
        paypal._search = lambda start, end: SAMPLE + [tx("INVPAY1", "T0006", "120.00", "-4.49", name="Acme Corp", day="2026-06-25")]
        got = client.post(f"/api/companies/{company_id}/paypal/sync", headers=bk).json().get("imported") or {}
        with Session(engine) as s:
            sales = s.exec(select(func.count(Transaction.id)).where(Transaction.company_id == company_id)
                           .where(Transaction.amount == Decimal("120.00")).where(Transaction.is_inflow.is_(True))).one()
        expect("la sincronización reconoce el pago de la factura: 1 venta, + su comisión", sales == 1 and got.get("new") == 1, (sales, got.get("new")))

        acc = client.get(f"/api/owner/companies/{company_id}/accounts", headers=cl)
        text = acc.text.lower()
        expect("Cuentas del dueño: su PayPal conectado y ningún token",
               acc.status_code == 200 and acc.json()["paypal"]["connected"] and "token" not in text and "item_id" not in text,
               acc.status_code)
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
            left = s.exec(select(func.count(PayPalEntry.id)).where(PayPalEntry.company_id == company_id)).one()
        expect("la limpieza no deja nada", left == 0, left)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
