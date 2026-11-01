"""
PRUEBA DEL COBRO POR VOZ, SIN RED (FASE 16 Paso 6, 1-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_voice.py

Reemplaza a Nemotron (`nemotron.chat_json`) por uno de mentira que GUARDA lo que recibió y comprueba, sobre un libro de
prueba que crea y borra:
  · 🔒 el nombre del cliente NUNCA llega a la IA (va [CUSTOMER]); sin cliente reconocido, la IA ni se llama;
  · dos clientes parecidos → pregunta cuál; el que elige el dueño se usa;
  · el código valida: monto con 3 decimales → aviso y vacío; fecha pasada → 15 días y aviso; "en 15 días" → la fecha;
  · el bookkeeper no puede usarlo (solo el dueño); cada llamada queda registrada (agente "invoice_voice").
Código de salida 1 si algo falla. No sale a internet (usa el bloqueo de check_permissions).
"""

import os
import sys
from datetime import date, timedelta

os.environ.setdefault("AUTH_DEV_MODE", "true")

import check_permissions as cp  # noqa: E402  (sus datos de prueba y su bloqueo de internet)
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import nemotron  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import AgentCall, InvoiceCustomer  # noqa: E402

fails: list[str] = []
SEEN: list[str] = []          # lo que le llegó a la "IA"
ANSWER: dict = {}


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def fake_chat_json(system, user, schema=None, **kw):
    SEEN.append(user)
    return nemotron.Reply(data=dict(ANSWER), usage=None, raw_text="")


def main() -> int:
    nemotron.chat_json = fake_chat_json
    client = TestClient(app, raise_server_exceptions=False)
    cl = {"X-Dev-Uid": cp.CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}
    bk = {"X-Dev-Uid": cp.BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    with Session(engine) as s:
        cp.drop_fixtures(s)
        company_id, _ = cp.build_fixtures(s)
        for n, e in (("Acme Corp", "ap@acme.test"), ("Ana López", "ana@lopez.test"), ("Anita Lopez Design", "hi@anita.test")):
            s.add(InvoiceCustomer(company_id=company_id, name=n, email=e))
        s.commit()
    url = f"/api/owner/companies/{company_id}/invoices/voice"
    today = date.today()
    try:
        print("\n  --- cobro por voz (sin red) ---")
        r = client.post(url, headers=bk, json={"text": "Factúrale a Acme 500 dólares"})
        expect("el bookkeeper NO dicta facturas (solo el dueño)", r.status_code == 403, r.status_code)

        SEEN.clear()
        r = client.post(url, headers=cl, json={"text": "Factúrale a Pedro Pérez 300 dólares por la pintura"})
        expect("sin cliente reconocido → pide elegirlo y la IA NI SE LLAMA",
               r.json().get("needs_customer") is True and not SEEN, (r.json().get("needs_customer"), len(SEEN)))

        ANSWER.update({"amount": "500", "due_in_days": 15, "concept": "Remodelación de la cocina", "due_date": None})
        r = client.post(url, headers=cl, json={"text": "Factúrale a ACME 500 dólares por la remodelación de la cocina, vence en 15 días",
                                               "lang": "es"})
        b = r.json()
        expect("reconoce al cliente y llena el formulario (monto, fecha a 15 días, concepto)",
               (b.get("customer") or {}).get("name") == "Acme Corp" and b["fields"] == {
                   "amount": "500.00", "due_date": (today + timedelta(days=15)).isoformat(), "concept": "Remodelación de la cocina"},
               (b.get("customer"), b.get("fields")))
        expect("🔒 a la IA NO le llega el nombre del cliente (va [CUSTOMER])",
               "acme" not in SEEN[-1].lower() and "[CUSTOMER]" in SEEN[-1], SEEN[-1][-80:])

        r = client.post(url, headers=cl, json={"text": "factura a lopez por 120 por el diseño"})
        b = r.json()
        expect("dos clientes parecidos → pregunta cuál (2 candidatos) y tampoco manda el nombre",
               b.get("needs_customer") is True and len(b.get("candidates") or []) == 2 and "lopez" not in SEEN[-1].lower(),
               (len(b.get("candidates") or []), SEEN[-1][-50:]))
        ana = next(c for c in b["candidates"] if c["name"] == "Ana López")
        r = client.post(url, headers=cl, json={"text": "factura a lopez por 120 por el diseño", "customer_id": ana["id"]})
        expect("el cliente que elige el dueño se usa", (r.json().get("customer") or {}).get("name") == "Ana López",
               (r.json().get("customer") or {}).get("name"))

        ANSWER.update({"amount": "120.001", "due_date": "2000-01-01", "due_in_days": None, "concept": ""})
        b = client.post(url, headers=cl, json={"text": "factura a Acme por ciento veinte"}).json()
        expect("el código valida: monto raro → vacío y aviso; fecha pasada → 15 días y aviso; sin concepto → aviso",
               b["fields"]["amount"] is None and b["fields"]["due_date"] == (today + timedelta(days=15)).isoformat()
               and {"amount_invalid", "due_past", "due_default", "concept_missing"} <= set(b["warnings"]),
               (b["fields"], b["warnings"]))

        with Session(engine) as s:
            n = s.exec(select(func.count(AgentCall.id)).where(AgentCall.company_id == company_id)
                       .where(AgentCall.agent == "invoice_voice")).one()
        expect("cada llamada a la IA queda registrada (agente invoice_voice)", n == len(SEEN), (n, len(SEEN)))
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
