"""
PRUEBA DE LAS DISPUTAS DE PAYPAL, SIN RED (FASE 16, 3-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_disputes.py

Reemplaza a Nemotron y a PayPal por unos de mentira que GUARDAN lo que recibieron y comprueba, sobre un libro de
prueba que crea y borra:
  · leer de PayPal: la copia se guarda, se liga a su factura por el id del cobro, y no se vuelve a pedir el detalle
    si la disputa no cambió;
  · lo que está EN DISPUTA lo suma SQL y solo cuenta las abiertas (regla 4: se declara);
  · 🔒 a la IA no le llegan ni el cliente, ni el negocio, ni correos, teléfonos o montos; un resumen con cifras se
    rechaza (1 reintento y 502); el bueno se guarda y la segunda vez no se llama a la IA;
  · aceptar y escribir: solo el dueño, solo si PayPal lo permite, y como multipart con la parte `input`;
  · simular (sandbox): solo las acciones conocidas;
  · el libro: una reversión o un contracargo restan de la venta (Returns and allowances); el que vuelve, la repone.
Código de salida 1 si algo falla. No sale a internet (usa el bloqueo de check_permissions).
"""

import json
import os
import sys
from datetime import date
from decimal import Decimal

os.environ.setdefault("AUTH_DEV_MODE", "true")
os.environ["PAYPAL_ENV"] = "sandbox"

import check_permissions as cp  # noqa: E402  (sus datos de prueba y su bloqueo de internet)
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import nemotron  # noqa: E402
import paypal  # noqa: E402
import paypal_disputes as pd  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import AccountType, Company, Invoice, InvoiceCustomer, PayPalConnection, PayPalDispute  # noqa: E402

fails: list[str] = []
SEEN: list[str] = []          # lo que le llegó a la "IA"
ANSWERS: list[dict] = []      # lo que contesta la "IA", en orden
CALLS: list[tuple] = []       # lo que se le pidió a "PayPal"
STATE = {"business": "", "update": "2026-11-01T10:00:00.000Z", "stage": "INQUIRY", "status": "WAITING_FOR_SELLER_RESPONSE"}


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def fake_chat_json(system, user, schema=None, **kw):
    SEEN.append(user)
    return nemotron.Reply(data=dict(ANSWERS.pop(0)), usage=None, raw_text="")


def _open_detail() -> dict:
    return {
        "dispute_id": "PP-D-OPEN", "create_time": "2026-10-30T15:00:00.000Z", "update_time": STATE["update"],
        "disputed_transactions": [{"seller_transaction_id": "PAY-123", "invoice_number": "0042",
                                   "buyer": {"name": "Zoltan Quimby"}}],
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "status": STATE["status"],
        "dispute_amount": {"currency_code": "USD", "value": "1250.00"},
        "dispute_life_cycle_stage": STATE["stage"], "dispute_channel": "INTERNAL",
        "seller_response_due_date": "2026-11-20T15:00:00.000Z",
        "messages": [{"posted_by": "BUYER", "time_posted": "2026-10-30T15:00:00.000Z",
                      "content": f"Hi, this is Zoltan. {STATE['business']} never came to fix the AC. I paid $1,250.00. "
                                 "Call me at (305) 555-0199 or zoltan@zq.test, see https://photos.test/ac"}],
        "allowed_response_options": {"accept_claim": {"accept_claim_types": ["REFUND"]},
                                     **({"make_offer": {"offer_types": ["REFUND"]}} if STATE.get("offer") else {})},
        "links": [{"rel": "self", "href": "x"}, {"rel": "accept_claim", "href": "x"}, {"rel": "send_message", "href": "x"},
                  {"rel": "require_evidence", "href": "x"},
                  *([{"rel": "make_offer", "href": "x"}, {"rel": "provide_evidence", "href": "x"}] if STATE.get("offer") else [])],
    }


CLOSED_DETAIL = {
    "dispute_id": "PP-D-DONE", "create_time": "2026-09-01T10:00:00.000Z", "update_time": "2026-09-20T10:00:00.000Z",
    "disputed_transactions": [{"seller_transaction_id": "PAY-OTHER"}], "reason": "UNAUTHORISED", "status": "RESOLVED",
    "dispute_amount": {"currency_code": "USD", "value": "80.00"}, "dispute_life_cycle_stage": "CHARGEBACK",
    "dispute_outcome": {"outcome_code": "RESOLVED_SELLER_FAVOUR"}, "links": [{"rel": "self", "href": "x"}],
}


def fake_api(method, path, **kw):
    files = kw.get("files") or {}
    if files.get("evidence-file"):
        CALLS.append((method, path, None, files.get("input")))
        return {}
    if method == "POST" and path.endswith(("/accept-claim", "/send-message")) and "json" in kw:
        CALLS.append((method, path + " (json→415)", kw.get("json"), None))
        raise paypal.PayPalError("UNSUPPORTED_MEDIA_TYPE")
    CALLS.append((method, path, kw.get("json"), (kw.get("files") or {}).get("input")))
    if method == "GET" and path == "/v1/customer/disputes":
        return {"items": [{"dispute_id": "PP-D-OPEN", "update_time": STATE["update"]},
                          {"dispute_id": "PP-D-DONE", "update_time": CLOSED_DETAIL["update_time"]}]}
    if method == "GET" and path.endswith("/PP-D-OPEN"):
        return _open_detail()
    if method == "GET" and path.endswith("/PP-D-DONE"):
        return CLOSED_DETAIL
    if method == "GET" and path == "/v2/invoicing/invoices/INV2-X":
        return {"status": "REFUNDED"}               # ✅ sandbox 3-nov: así queda la factura de una disputa perdida
    return {}


GOOD = {"summary": "The customer says the service was not received. PayPal is waiting for your response.",
        "customer_says": "The customer says nobody came to fix the air conditioner.",
        "options": "Accepting the claim means PayPal refunds the customer from your balance. "
                   "You can also reply to the customer before deciding."}


def main() -> int:
    nemotron.chat_json = fake_chat_json
    paypal.api = fake_api
    client = TestClient(app, raise_server_exceptions=False)
    cl = {"X-Dev-Uid": cp.CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": cp.BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    with Session(engine) as s:
        cp.drop_fixtures(s)
        company_id, _ = cp.build_fixtures(s)
        STATE["business"] = s.get(Company, company_id).name       # el negocio, nombrado por el comprador
        s.add(PayPalConnection(company_id=company_id, mode="first_party", environment="sandbox"))
        c = InvoiceCustomer(company_id=company_id, name="Zoltan Quimby", email="zoltan@zq.test")
        s.add(c)
        s.flush()
        inv = Invoice(company_id=company_id, customer_id=c.id, paypal_invoice_id="INV2-X", number="0042",
                      concept="AC repair", amount=Decimal("1250.00"), due_date=date(2026, 10, 20), status="PAID",
                      payment_id="PAY-123")
        s.add(inv)
        s.commit()
        inv_id = inv.id
    base = f"/api/owner/companies/{company_id}/disputes"
    try:
        print("\n  --- disputas (sin red) ---")
        r = client.post(f"{base}/refresh", headers=bk)
        v = r.json()
        rows = {d["dispute_id"]: d for d in v.get("disputes", [])}
        expect("leer de PayPal: 2 disputas guardadas (el bookkeeper puede refrescar)",
               r.status_code == 200 and set(rows) == {"PP-D-OPEN", "PP-D-DONE"}, (r.status_code, list(rows)))
        expect("se liga a su factura por el id del cobro", (rows.get("PP-D-OPEN") or {}).get("invoice", {}).get("id") == inv_id,
               (rows.get("PP-D-OPEN") or {}).get("invoice"))
        expect("EN DISPUTA = solo las abiertas, sumado por SQL ($1,250, no $1,330)",
               v.get("summary") == {"open": 1, "in_dispute": "1250.00"}, v.get("summary"))
        expect("la abierta va primero", list(rows)[0] == "PP-D-OPEN" and v["disputes"][0]["dispute_id"] == "PP-D-OPEN",
               [d["dispute_id"] for d in v["disputes"]])
        o = rows["PP-D-OPEN"]
        expect("acciones que PayPal permite: aceptar, escribir (INQUIRY) y en sandbox pedir pruebas",
               o["can_accept"] and o["can_message"] and o["sandbox_actions"] == ["seller_evidence"],
               (o["can_accept"], o["can_message"], o["sandbox_actions"]))
        expect("la cerrada no se puede aceptar ni escribir", not rows["PP-D-DONE"]["can_accept"]
               and not rows["PP-D-DONE"]["can_message"], rows["PP-D-DONE"]["status"])

        CALLS.clear()
        client.post(f"{base}/refresh", headers=cl)
        details = [x for x in CALLS if x[0] == "GET" and x[1] != "/v1/customer/disputes"]
        expect("sin cambios en PayPal, no se pide el detalle otra vez", details == [], details)

        oid = o["id"]
        r = client.post(f"{base}/{oid}/summary", headers=bk, json={"lang": "en"})
        expect("el bookkeeper NO pide el resumen de la IA (solo el dueño)", r.status_code == 403, r.status_code)

        ANSWERS[:] = [dict(GOOD, summary="The customer paid $1,250 on day 30."), dict(GOOD, options="Call 305.")]
        r = client.post(f"{base}/{oid}/summary", headers=cl, json={"lang": "en"})
        expect("un resumen con cifras → rechazado, 1 reintento y 502", r.status_code == 502 and not ANSWERS,
               (r.status_code, len(ANSWERS)))
        sent = SEEN[-1]
        leaked = [w for w in ("zoltan", "quimby", STATE["business"].lower(), "permcheck", "1,250", "1250", "555", "@", "http")
                  if w in sent.lower()]
        expect("🔒 a la IA no le llegan nombres, correos, teléfonos, enlaces ni montos", not leaked, leaked)
        expect("🔒 lo privado se reemplaza por marcadores",
               all(x in sent for x in ("[customer]", "[amount]", "[phone]", "[email]", "[link]")), sent.splitlines()[-1][:160])

        ANSWERS[:] = [GOOD]
        r = client.post(f"{base}/{oid}/summary", headers=cl, json={"lang": "en"})
        got = next((d for d in r.json().get("disputes", []) if d["id"] == oid), {}).get("summary")
        expect("el resumen bueno se guarda y sale en la vista", r.status_code == 200 and got and got["options"] == GOOD["options"],
               r.status_code)
        n = len(SEEN)
        r = client.post(f"{base}/{oid}/summary", headers=cl, json={"lang": "en"})
        expect("la segunda vez se lee guardado: NO se llama a la IA", r.status_code == 200 and len(SEEN) == n, len(SEEN) - n)
        r = client.get(f"{base}?lang=en", headers=bk)
        got = next((d for d in r.json().get("disputes", []) if d["id"] == oid), {}).get("summary")
        expect("el bookkeeper (y la visita) lo ven guardado, sin gastar IA", bool(got) and len(SEEN) == n, bool(got))

        r = client.post(f"{base}/{oid}/accept", headers=bk, json={})
        expect("el bookkeeper NO acepta reclamaciones → 403", r.status_code == 403, r.status_code)
        CALLS.clear()
        r = client.post(f"{base}/{oid}/accept", headers=cl, json={"note": "Sorry for the trouble."})
        post = next((x for x in CALLS if x[0] == "POST" and x[3]), None)
        body = json.loads(post[3][1]) if post and post[3] else {}
        expect("aceptar: JSON y, si PayPal da 415, multipart con `input` {note, REFUND}; se relee la disputa",
               r.status_code == 200 and post[1] == "/v1/customer/disputes/PP-D-OPEN/accept-claim"
               and body == {"note": "Sorry for the trouble.", "accept_claim_type": "REFUND"}
               and any(x[0] == "GET" for x in CALLS), (r.status_code, post and post[1], body))
        did_done = rows["PP-D-DONE"]["id"]
        r = client.post(f"{base}/{did_done}/accept", headers=cl, json={})
        expect("una disputa cerrada no se acepta → 409", r.status_code == 409, r.status_code)

        r = client.post(f"{base}/{oid}/message", headers=cl, json={"message": "  "})
        expect("mensaje vacío → 422", r.status_code == 422, r.status_code)
        CALLS.clear()
        r = client.post(f"{base}/{oid}/message", headers=cl, json={"message": "We will come tomorrow at 9."})
        post = next((x for x in CALLS if x[0] == "POST" and x[3]), None)
        expect("escribir: el texto del dueño, tal cual", r.status_code == 200 and post
               and json.loads(post[3][1]) == {"message": "We will come tomorrow at 9."}, r.status_code)
        STATE.update(stage="CHARGEBACK", update="2026-11-02T10:00:00.000Z")
        client.post(f"{base}/refresh", headers=cl)
        r = client.post(f"{base}/{oid}/message", headers=cl, json={"message": "Hello"})
        expect("fuera de la etapa INQUIRY no se escribe (regla de PayPal) → 409", r.status_code == 409, r.status_code)
        v = client.get(f"{base}?lang=en", headers=cl).json()
        o2 = next(d for d in v["disputes"] if d["id"] == oid)
        expect("si la disputa cambia en PayPal, el resumen viejo ya no se muestra", o2["summary"] is None, o2["summary"])

        print("\n  --- ofrecer y subir pruebas (5-nov) ---")
        STATE.update(stage="INQUIRY", status="WAITING_FOR_SELLER_RESPONSE", update="2026-11-05T10:00:00.000Z", offer=True)
        client.post(f"{base}/refresh", headers=cl)
        o3 = next(d for d in client.get(f"{base}?lang=en", headers=cl).json()["disputes"] if d["id"] == oid)
        expect("PayPal permite ofrecer y subir pruebas → la pantalla lo sabe", o3["can_offer"] and o3["can_evidence"], (o3["can_offer"], o3["can_evidence"]))
        r = client.post(f"{base}/{oid}/offer", headers=bk, json={"amount": "100.00"})
        expect("el bookkeeper NO ofrece", r.status_code == 403, r.status_code)
        r = client.post(f"{base}/{oid}/offer", headers=cl, json={"amount": "5000.00"})
        expect("ofrecer más que lo disputado → 422", r.status_code == 422, r.status_code)
        CALLS.clear()
        r = client.post(f"{base}/{oid}/offer", headers=cl, json={"amount": "300.00", "note": "Partial refund for the delay."})
        post = next((x for x in CALLS if x[0] == "POST" and x[1].endswith("/make-offer")), None)
        expect("la oferta: REFUND por $300 con la nota", r.status_code == 200 and post and post[2] == {
            "note": "Partial refund for the delay.", "offer_amount": {"currency_code": "USD", "value": "300.00"}, "offer_type": "REFUND"}, post)
        r = client.post(f"{base}/{oid}/evidence", headers=bk, data={"evidence_type": "OTHER", "notes": "x"})
        expect("el bookkeeper NO sube pruebas", r.status_code == 403, r.status_code)
        r = client.post(f"{base}/{oid}/evidence", headers=cl, data={"evidence_type": "POLICE_REPORT", "notes": "x"})
        expect("un tipo fuera de la lista → 422", r.status_code == 422, r.status_code)
        r = client.post(f"{base}/{oid}/evidence", headers=cl, data={"evidence_type": "OTHER"}, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
        expect("un archivo que no es PDF ni imagen → 422", r.status_code == 422, r.status_code)
        CALLS.clear()
        r = client.post(f"{base}/{oid}/evidence", headers=cl, data={"evidence_type": "PROOF_FOR_SOFTWARE_OR_SERVICE_DELIVERED", "notes": "Signed work order"},
                        files={"file": ("work-order.pdf", b"%PDF-1.4 test", "application/pdf")})
        post = next((x for x in CALLS if x[1].endswith("/provide-evidence")), None)
        inp = json.loads(post[3][1]) if post and post[3] else {}
        expect("la prueba va como multipart: input {tipo, notas, documento} + el archivo",
               r.status_code == 200 and inp == {"evidences": [{"evidence_type": "PROOF_FOR_SOFTWARE_OR_SERVICE_DELIVERED",
                                                               "notes": "Signed work order", "documents": [{"name": "work-order.pdf"}]}]}, (r.status_code, inp))

        r = client.post(f"{base}/{oid}/simulate", headers=cl, json={"action": "delete_everything"})
        expect("simular: una acción desconocida → 404", r.status_code == 404, r.status_code)
        CALLS.clear()
        r = client.post(f"{base}/{oid}/simulate", headers=cl, json={"action": "seller_favor"})
        post = next((x for x in CALLS if x[0] == "POST"), None)
        expect("simular (sandbox): adjudicate a favor del negocio",
               r.status_code == 200 and post and post[1].endswith("/adjudicate")
               and post[2] == {"adjudication_outcome": "SELLER_FAVOR"}, (r.status_code, post))

        STATE.update(status="RESOLVED", update="2026-11-03T10:00:00.000Z")
        r = client.post(f"{base}/refresh", headers=cl)
        with Session(engine) as s:
            st = s.get(Invoice, inv_id).status
        expect("disputa resuelta → se relee su factura: REFUNDED (Cobros solo consultaba las abiertas)",
               r.status_code == 200 and st == "REFUNDED", (r.status_code, st))
        lst = client.get(f"/api/owner/companies/{company_id}/invoices", headers=cl).json()["invoices"]
        row = next(i for i in lst if i["id"] == inv_id)
        expect("devuelta y sin su fila todavía → 'devolución pendiente de registrar'", row["refund_in_books"] is False,
               row["refund_in_books"])
        reversal = {"transaction_info": {
            "transaction_id": "REV-1", "paypal_reference_id": "PAY-123", "transaction_event_code": "T1106",
            "transaction_status": "S", "transaction_initiation_date": "2026-11-03T10:00:00+0000",
            "transaction_amount": {"currency_code": "USD", "value": "-1250.00"}}}
        with Session(engine) as s:
            for rw in paypal.rows_for(reversal):
                paypal.insert_row(s, s.get(Company, company_id), rw, [])
            s.commit()
        lst = client.get(f"/api/owner/companies/{company_id}/invoices", headers=cl).json()["invoices"]
        row = next(i for i in lst if i["id"] == inv_id)
        expect("llega la reversión de ESE cobro (paypal_reference_id) → 'devolución en contabilidad'",
               row["refund_in_books"] is True, row["refund_in_books"])

        print("\n  --- el libro: disputas en la sincronización (función pura) ---")

        def one(code, value):
            return paypal.rows_for({"transaction_info": {
                "transaction_id": "T", "transaction_event_code": code, "transaction_status": "S",
                "transaction_initiation_date": "2026-11-01T10:00:00+0000",
                "transaction_amount": {"currency_code": "USD", "value": value}}})
        for code, value in (("T1106", "-1250.00"), ("T1201", "-80.00"), ("T1202", "80.00"), ("T1205", "80.00")):
            rws = one(code, value)
            expect(f"{code} {value} → Returns and allowances (REVENUE)",
                   len(rws) == 1 and rws[0]["preset"] == (AccountType.REVENUE, paypal.REFUND), [x["preset"] for x in rws])
        rws = one("T1106", "50.00")
        expect("una reversión que ENTRA (no es de una venta) queda Pendiente", rws and rws[0]["preset"] is None,
               [x["preset"] for x in rws])
        with Session(engine) as s:
            n_rows = len(s.exec(select(PayPalDispute).where(PayPalDispute.company_id == company_id)).all())
        expect("redact tapa el nombre aunque esté en minúsculas",
               pd.redact("zoltan said hi", ["Zoltan Quimby"]) == "[customer] said hi", pd.redact("zoltan said hi", ["Zoltan Quimby"]))
        expect("la limpieza borra las disputas con el libro (CASCADE)", n_rows == 2, n_rows)
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
        with Session(engine) as s:
            left = len(s.exec(select(PayPalDispute).where(PayPalDispute.company_id == company_id)).all())
        expect("la limpieza no deja nada", left == 0, left)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
