"""
PRUEBA DE LOS PAGOS Y DEVOLUCIONES FUERA DE PAYPAL, SIN RED (FASE 16, 3-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_offline.py

Reemplaza a PayPal por uno de mentira y comprueba, sobre un libro de prueba que crea y borra:
  · registrar un pago: llama a PayPal con método, fecha y monto; la factura queda PAGADA; NO se crea ninguna venta;
  · sin fila del banco, se declara "todavía no está en el banco";
  · con UNA fila del banco (mismo monto, dentro de la ventana, Pendiente) → venta (Gross receipts) ligada a la factura;
  · con DOS, ninguna se toca: la elige el bookkeeper (confirmar → `source="manual"`);
  · nunca toca una fila escrita a mano, de un mes cerrado, de otra dirección o fuera de la ventana;
  · la devolución fuera de PayPal → la salida del banco queda en Returns and allowances;
  · solo el dueño registra; el bookkeeper confirma.
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
from models import (AccountType, Invoice, InvoiceCustomer, InvoiceOffline, MonthClose, PayPalConnection,  # noqa: E402
                    Transaction)

fails: list[str] = []
CALLS: list[tuple] = []
STATUS = {"INV2-A": "SENT", "INV2-B": "SENT", "INV2-C": "SENT"}
DUE: dict = {}                # lo que falta según PayPal (parciales, 5-nov)
PAID: dict = {}               # lo cobrado según PayPal


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def fake_api(method, path, **kw):
    CALLS.append((method, path, kw.get("json")))
    pid = path.split("/")[4] if path.startswith("/v2/invoicing/invoices/") else ""
    if method == "POST" and path.endswith("/payments"):
        v = Decimal(kw["json"]["amount"]["value"])
        PAID[pid] = PAID.get(pid, Decimal("0")) + v
        if pid in DUE:
            DUE[pid] = str(Decimal(DUE[pid]) - v)
        STATUS[pid] = "PARTIALLY_PAID" if pid in DUE and Decimal(DUE[pid]) > 0 else "MARKED_AS_PAID"   # ✅ lo marcado a mano no es PAID
        return {"payment_id": f"EXTR-{pid}"}
    if method == "POST" and path.endswith("/refunds"):
        STATUS[pid] = "MARKED_AS_REFUNDED"
        return {"refund_id": f"EXTR-R-{pid}"}
    if method == "GET" and pid:
        paid = str(PAID.get(pid, Decimal("0")))
        return {"status": STATUS.get(pid, "SENT"), **({"due_amount": {"value": DUE[pid]}} if pid in DUE else {}),
                "payments": {"paid_amount": {"value": paid}, "transactions": [{"type": "EXTERNAL", "method": "CHECK"}]}}
    return {}


def bank(s, company_id, amount, day, inflow=True, **kw):
    t = Transaction(company_id=company_id, date=day, description=kw.pop("description", "MOBILE DEPOSIT"),
                    amount=Decimal(amount), is_inflow=inflow, bank_account="Chase ••1234", **kw)
    s.add(t)
    s.flush()
    return t.id


def main() -> int:
    paypal.api = fake_api
    client = TestClient(app, raise_server_exceptions=False)
    cl = {"X-Dev-Uid": cp.CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": cp.BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    today = date.today()
    d0 = today - timedelta(days=2)
    with Session(engine) as s:
        cp.drop_fixtures(s)
        company_id, _ = cp.build_fixtures(s)
        s.add(PayPalConnection(company_id=company_id, mode="first_party", environment="sandbox"))
        c = InvoiceCustomer(company_id=company_id, name="Zoltan Quimby", email="zq@test.local")
        s.add(c)
        s.flush()
        cust_id = c.id
        ids = {}
        for pid, amt in (("INV2-A", "750.00"), ("INV2-B", "320.00"), ("INV2-C", "90.00")):
            inv = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id=pid, number=pid[-1], concept="Job",
                          amount=Decimal(amt), due_date=today + timedelta(days=10), status="SENT")
            s.add(inv)
            s.flush()
            ids[pid] = inv.id
        # A ($750): una sola fila buena + trampas que NO valen (escrita a mano, otra dirección, fuera de la ventana).
        good_a = bank(s, company_id, "750.00", d0 + timedelta(days=1))
        manual = bank(s, company_id, "750.00", d0, source="manual", account_type=AccountType.OTHER_INCOME, category="Other")
        out_a = bank(s, company_id, "750.00", d0, inflow=False, description="CHECK 1001")
        late_a = bank(s, company_id, "750.00", d0 + timedelta(days=30))
        # B ($320): dos candidatas → duda → el bookkeeper.
        b1 = bank(s, company_id, "320.00", d0)
        b2 = bank(s, company_id, "320.00", d0 + timedelta(days=2))
        s.commit()
        n_before = len(s.exec(select(Transaction).where(Transaction.company_id == company_id)).all())
    base = f"/api/owner/companies/{company_id}/invoices"
    try:
        print("\n  --- pagos y devoluciones fuera de PayPal (sin red) ---")
        r = client.post(f"{base}/{ids['INV2-A']}/record-payment", headers=bk, json={"method": "CHECK", "day": d0.isoformat()})
        expect("el bookkeeper NO registra pagos (solo el dueño)", r.status_code == 403, r.status_code)
        r = client.post(f"{base}/{ids['INV2-A']}/record-payment", headers=cl, json={"method": "BITCOIN", "day": d0.isoformat()})
        expect("un método que PayPal no admite → 422", r.status_code == 422, r.status_code)
        r = client.post(f"{base}/{ids['INV2-A']}/record-payment", headers=cl,
                        json={"method": "CHECK", "day": (today + timedelta(days=1)).isoformat()})
        expect("una fecha futura → 422", r.status_code == 422, r.status_code)

        CALLS.clear()
        r = client.post(f"{base}/{ids['INV2-A']}/record-payment", headers=cl,
                        json={"method": "CHECK", "day": d0.isoformat(), "note": "Check #4410"})
        post = next((x for x in CALLS if x[0] == "POST"), None)
        expect("PayPal recibe método, fecha, monto y nota",
               post and post[1] == "/v2/invoicing/invoices/INV2-A/payments" and post[2] == {
                   "method": "CHECK", "payment_date": d0.isoformat(),
                   "amount": {"currency_code": "USD", "value": "750.00"}, "note": "Check #4410"}, post)
        inv = next((i for i in r.json().get("invoices", []) if i["id"] == ids["INV2-A"]), {})
        expect("la factura queda PAGADA y en contabilidad (la fila del banco)",
               inv.get("status") == "MARKED_AS_PAID" and inv.get("in_books") and inv.get("offline", {}).get("in_bank"),
               (inv.get("status"), inv.get("in_books"), inv.get("offline")))
        with Session(engine) as s:
            ta = s.get(Transaction, good_a)
            expect("UNA fila del banco → Gross receipts or sales (REVENUE), por el código",
                   (ta.account_type, ta.category, ta.source) == (AccountType.REVENUE, paypal.SALE, "invoice"),
                   (ta.account_type, ta.category, ta.source))
            untouched = [s.get(Transaction, i) for i in (manual, out_a, late_a)]
            expect("la escrita a mano, la de salida y la de fuera de la ventana NO se tocan",
                   [t.category for t in untouched] == ["Other", None, None], [t.category for t in untouched])
            n_rows = len(s.exec(select(Transaction).where(Transaction.company_id == company_id)).all())
            expect("NO se crea ninguna venta nueva (las mismas filas que antes)", n_rows == n_before, (n_before, n_rows))

        r = client.post(f"{base}/{ids['INV2-B']}/record-payment", headers=cl, json={"method": "BANK_TRANSFER", "day": d0.isoformat()})
        inv = next((i for i in r.json().get("invoices", []) if i["id"] == ids["INV2-B"]), {})
        expect("con DOS filas posibles, ninguna se toca: queda para el bookkeeper",
               inv.get("status") == "MARKED_AS_PAID" and not inv.get("in_books") and inv.get("offline", {}).get("in_bank") is False,
               (inv.get("in_books"), inv.get("offline")))
        r = client.get(f"/api/companies/{company_id}/invoices/offline", headers=bk)
        v = r.json()
        prop = next((p for p in v.get("proposals", []) if p["amount"] == "320.00"), None)
        expect("el bookkeeper ve la propuesta con sus 2 filas", prop and {c["id"] for c in prop["candidates"]} == {b1, b2},
               prop and [c["id"] for c in prop["candidates"]])
        r = client.post(f"/api/companies/{company_id}/invoices/offline/{prop['id']}/confirm", headers=cl, json={"bank_tx_id": b2})
        expect("el dueño NO confirma en la consola", r.status_code == 403, r.status_code)
        r = client.post(f"/api/companies/{company_id}/invoices/offline/{prop['id']}/confirm", headers=bk, json={"bank_tx_id": 999999})
        expect("una fila que no es candidata → 422", r.status_code == 422, r.status_code)
        r = client.post(f"/api/companies/{company_id}/invoices/offline/{prop['id']}/confirm", headers=bk, json={"bank_tx_id": b2})
        with Session(engine) as s:
            t2, t1 = s.get(Transaction, b2), s.get(Transaction, b1)
            expect("confirmada por el bookkeeper: venta y `source=manual`; la otra sigue Pendiente",
                   r.status_code == 200 and (t2.category, t2.source) == (paypal.SALE, "manual") and t1.category is None,
                   (r.status_code, t2.category, t2.source, t1.category))

        r = client.post(f"{base}/{ids['INV2-C']}/record-payment", headers=cl, json={"method": "CASH", "day": d0.isoformat()})
        inv = next((i for i in r.json().get("invoices", []) if i["id"] == ids["INV2-C"]), {})
        expect("efectivo sin depósito: PAGADA y declarada 'todavía no está en el banco'",
               inv.get("status") == "MARKED_AS_PAID" and inv.get("offline") == {"kind": "payment", "method": "CASH",
                                                                      "day": d0.isoformat(), "in_bank": False},
               inv.get("offline"))
        with Session(engine) as s:
            dep = bank(s, company_id, "90.00", today)
            s.commit()
        r = client.post(f"{base}/refresh", headers=cl)
        inv = next((i for i in r.json().get("invoices", []) if i["id"] == ids["INV2-C"]), {})
        expect("cuando el efectivo llega al banco, al abrir Cobros se empareja solo",
               inv.get("offline", {}).get("in_bank") and inv.get("in_books"), inv.get("offline"))

        r = client.post(f"{base}/{ids['INV2-A']}/record-payment", headers=cl, json={"method": "CASH", "day": d0.isoformat()})
        expect("una factura ya pagada no se vuelve a cobrar → 409", r.status_code == 409, r.status_code)
        s_ = r.json() if r.status_code == 200 else client.get(base, headers=cl).json()
        expect("lo cobrado fuera de PayPal cuenta en 'Cobrado en el año' (SQL): 750 + 320 + 90",
               s_["summary"]["collected_this_year"] == "1160.00", s_["summary"])

        print("\n  --- pagada en el momento (sobre el borrador) ---")
        with Session(engine) as s:
            dr = Invoice(company_id=company_id, customer_id=cust_id, paypal_invoice_id="INV2-N", number="N", concept="On site",
                         amount=Decimal("135.00"), due_date=today, status="DRAFT")
            s.add(dr)
            s.commit()
            dr_id = dr.id
        STATUS["INV2-N"] = "DRAFT"
        CALLS.clear()
        r = client.post(f"{base}/{dr_id}/record-payment", headers=cl, json={"method": "CASH", "day": today.isoformat()})
        inv = next((i for i in r.json().get("invoices", []) if i["id"] == dr_id), {})
        expect("un BORRADOR se cobra en el momento: queda pagado sin enviarse (ningún /send)",
               r.status_code == 200 and inv.get("status") == "MARKED_AS_PAID" and not any(c[1].endswith("/send") for c in CALLS),
               (r.status_code, inv.get("status"), [c[1] for c in CALLS]))

        print("\n  --- parciales (5-nov) ---")
        with Session(engine) as s:
            pr = Invoice(company_id=company_id, customer_id=cust_id, paypal_invoice_id="INV2-P", number="P", concept="Big job",
                         amount=Decimal("1000.00"), due_date=today + timedelta(days=9), status="SENT")
            s.add(pr)
            s.commit()
            pr_id = pr.id
        STATUS["INV2-P"] = "SENT"
        DUE["INV2-P"] = "1000.00"
        before = client.get(base, headers=cl).json()["summary"]
        r = client.post(f"{base}/{pr_id}/record-payment", headers=cl, json={"method": "CHECK", "day": today.isoformat(), "amount": "1200.00"})
        expect("más de lo que falta → 422", r.status_code == 422, r.status_code)
        CALLS.clear()
        r = client.post(f"{base}/{pr_id}/record-payment", headers=cl, json={"method": "CHECK", "day": today.isoformat(), "amount": "400.00"})
        post = next((c for c in CALLS if c[0] == "POST"), None)
        summ = r.json().get("summary", {})
        expect("un anticipo de $400: PayPal recibe 400, la factura queda PARCIAL",
               r.status_code == 200 and post and post[2]["amount"]["value"] == "400.00"
               and next(i for i in r.json()["invoices"] if i["id"] == pr_id)["status"] == "PARTIALLY_PAID", (r.status_code, post and post[2]))
        expect("por cobrar baja $400 y cobrado sube $400 (SQL)",
               Decimal(summ["outstanding"]) == Decimal(before["outstanding"]) - 400
               and Decimal(summ["collected_this_year"]) == Decimal(before["collected_this_year"]) + 400, (before, summ))
        r = client.post(f"{base}/{pr_id}/record-payment", headers=cl, json={"method": "CASH", "day": today.isoformat()})
        post = [c for c in CALLS if c[0] == "POST"][-1]
        expect("el resto sin monto = lo que falta según PayPal ($600)", r.status_code == 200 and post[2]["amount"]["value"] == "600.00", post[2])
        r = client.post(f"{base}/{pr_id}/record-refund", headers=cl, json={"method": "CHECK", "day": today.isoformat(), "amount": "1500"})
        expect("devolver más de lo cobrado → 422", r.status_code == 422, r.status_code)

        print("\n  --- la devolución ---")
        with Session(engine) as s:
            t = s.get(Transaction, out_a)               # el cheque viejo de $750 ya tiene su categoría: no es candidato
            t.account_type, t.category = AccountType.OPERATING_EXPENSE, "Repairs and maintenance"
            out_r = bank(s, company_id, "750.00", today, inflow=False, description="CHECK 1002")
            s.commit()
        r = client.post(f"{base}/{ids['INV2-A']}/record-refund", headers=cl, json={"method": "CHECK", "day": today.isoformat()})
        inv = next((i for i in r.json().get("invoices", []) if i["id"] == ids["INV2-A"]), {})
        with Session(engine) as s:
            tr, old_out = s.get(Transaction, out_r), s.get(Transaction, out_a)
            expect("devuelta fuera de PayPal: el cheque que salió → Returns and allowances",
                   inv.get("status") == "MARKED_AS_REFUNDED" and inv.get("offline", {}).get("kind") == "refund"
                   and (tr.account_type, tr.category) == (AccountType.REVENUE, paypal.REFUND),
                   (inv.get("status"), inv.get("offline"), tr.category))
            expect("una salida ya categorizada como otra cosa no se toca", old_out.category == "Repairs and maintenance",
                   old_out.category)
        expect("devuelta fuera de PayPal y emparejada con el banco → 'devolución en contabilidad'",
               inv.get("refund_in_books") is True, inv.get("refund_in_books"))
        r = client.post(f"{base}/{ids['INV2-B']}/record-refund", headers=cl, json={"method": "CASH", "day": "2020-01-01"})
        expect("una fecha de hace más de un año → 422", r.status_code == 422, r.status_code)

        print("\n  --- un mes cerrado no se toca ---")
        with Session(engine) as s:
            inv = Invoice(company_id=company_id, customer_id=cust_id, paypal_invoice_id="INV2-D", number="D", concept="Job",
                          amount=Decimal("55.00"), due_date=today, status="SENT")
            s.add(inv)
            old_day = date(today.year - 1, 6, 10)
            locked = bank(s, company_id, "55.00", old_day)
            s.add(MonthClose(company_id=company_id, month=date(old_day.year, 6, 1)))
            s.commit()
            inv_d = inv.id
        STATUS["INV2-D"] = "SENT"
        client.post(f"{base}/{inv_d}/record-payment", headers=cl, json={"method": "CASH", "day": old_day.isoformat()})
        with Session(engine) as s:
            expect("la fila de un mes cerrado NO se toca", s.get(Transaction, locked).category is None,
                   s.get(Transaction, locked).category)
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
        with Session(engine) as s:
            left = len(s.exec(select(InvoiceOffline).where(InvoiceOffline.company_id == company_id)).all())
        expect("la limpieza no deja nada", left == 0, left)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
