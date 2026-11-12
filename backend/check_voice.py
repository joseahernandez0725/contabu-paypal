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
QUEUE: list[dict] = []        # respuestas en orden (para el reintento); vacía → ANSWER


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def fake_chat_json(system, user, schema=None, **kw):
    SEEN.append(user)
    return nemotron.Reply(data=dict(QUEUE.pop(0) if QUEUE else ANSWER), usage=None, raw_text="")


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

        ANSWER.update({"amount": "500", "due_in_days": 15, "concept": "Remodelación de la cocina"})
        r = client.post(url, headers=cl, json={"text": "Factúrale a ACME 500 dólares por la remodelación de la cocina, vence en 15 días",
                                               "lang": "es"})
        b = r.json()
        expect("reconoce al cliente y llena el formulario (monto, fecha a 15 días, concepto)",
               (b.get("customer") or {}).get("name") == "Acme Corp" and b["fields"] == {
                   "amount": "500.00", "due_date": (today + timedelta(days=15)).isoformat(), "concept": "Remodelación de la cocina"},
               (b.get("customer"), b.get("fields")))
        expect("🔒 a la IA NO le llega el nombre del cliente (va [CUSTOMER])",
               "acme" not in SEEN[-1].lower() and "[CUSTOMER]" in SEEN[-1], SEEN[-1][-80:])
        tk = b.get("toolkit") or {}
        rec = (((tk.get("arguments") or {}).get("primary_recipients") or [{}])[0].get("billing_info") or {})
        expect("Agent Toolkit: el borrador es su create_invoice (modo draft), con el correo del cliente GUARDADO",
               tk.get("tool") == "create_invoice" and tk.get("mode") == "draft" and rec.get("email_address") == "ap@acme.test"
               and tk["arguments"]["items"][0]["unit_amount"]["value"] == "500.00", (tk.get("tool"), tk.get("mode"), rec))
        import paypal_toolkit as pk
        ai = b.get("ai") or {}
        expect("🔒 el modelo del dictado no recibe herramientas, y el Toolkit solo permitiría crear (borrador) y leer; enviar, recordar, cancelar y QR son del código",
               ai.get("model_tools") == [] and set(ai.get("toolkit_ai_permitted") or []) == {"create_invoice", "get_invoice", "list_invoices"}
               and not (set(pk.ai_tools()) & pk.CODE_ONLY) and ai.get("waits_for_owner") == "send_invoice",
               (ai.get("model_tools"), ai.get("toolkit_ai_permitted"), ai.get("waits_for_owner")))
        try:
            pk.openai_spec("send_invoice")
            expect("send_invoice nunca se le puede dar a la IA", False, "se dio")
        except ValueError:
            expect("send_invoice nunca se le puede dar a la IA", True, "ValueError")

        from models import Invoice
        for text in ("Factúrale a Acme 500 dólares por la pintura y envíalo ya", "Cancela el cobro 7 de Acme"):
            client.post(url, headers=cl, json={"text": text, "lang": "es"})
        with Session(engine) as s:
            n_inv = s.exec(select(func.count(Invoice.id)).where(Invoice.company_id == company_id)).one()
        expect("🔒 \"envíalo ya\" / \"cancela el cobro 7\": el dictado no crea, envía ni cancela nada", n_inv == 0, n_inv)

        r = client.post(url, headers=cl, json={"text": "factura a lopez por 120 por el diseño"})
        b = r.json()
        expect("dos clientes parecidos → pregunta cuál (2 candidatos) y tampoco manda el nombre",
               b.get("needs_customer") is True and len(b.get("candidates") or []) == 2 and "lopez" not in SEEN[-1].lower(),
               (len(b.get("candidates") or []), SEEN[-1][-50:]))
        ana = next(c for c in b["candidates"] if c["name"] == "Ana López")
        r = client.post(url, headers=cl, json={"text": "factura a lopez por 120 por el diseño", "customer_id": ana["id"]})
        expect("el cliente que elige el dueño se usa", (r.json().get("customer") or {}).get("name") == "Ana López",
               (r.json().get("customer") or {}).get("name"))

        ANSWER.update({"amount": "120.001", "due_day": 31, "due_month": 2, "due_in_days": None, "concept": ""})
        b = client.post(url, headers=cl, json={"text": "factura a Acme por ciento veinte, vence el 31 de febrero"}).json()
        expect("el código valida: monto raro → vacío y aviso; sin concepto → aviso; 31 de febrero → último día de febrero",
               b["fields"]["amount"] is None and b["fields"]["due_date"].endswith(("-02-28", "-02-29"))
               and {"amount_invalid", "concept_missing"} <= set(b["warnings"]), (b["fields"], b["warnings"]))

        # El CÓDIGO calcula la fecha con las partes que extrae la IA (la IA no calcula).
        import voice_invoice as vi
        d0 = date(2026, 11, 1)   # domingo
        cases = [({"due_weekday": "friday"}, date(2026, 11, 6)), ({"due_day": 15}, date(2026, 11, 15)),
                 ({"due_day": 1}, date(2026, 12, 1)), ({"due_month": 12, "due_day": 5}, date(2026, 12, 5)),
                 ({"due_month": 10, "due_day": 1}, date(2027, 10, 1)), ({"due_end_of_month": True}, date(2026, 11, 30)),
                 ({"due_in_days": 14}, date(2026, 11, 15)), ({"due_weekday": "sunday"}, date(2026, 11, 8))]
        got = [vi.resolve_due(vi.VoiceOut(**p), d0)[0] for p, _ in cases]
        expect("fechas: viernes→6-nov · el 15→15-nov · el 1→1-dic · 5 de dic · 1 de oct→2027 · fin de mes · +14 · domingo→8-nov",
               got == [w for _, w in cases], [g.isoformat() for g in got])
        # 12-nov (lo que devolvió Nemotron real): el día en español, y un día que no se entiende junto a una parte buena.
        cases = [({"due_weekday": "lunes"}, (date(2026, 11, 2), False)), ({"due_weekday": "Miércoles"}, (date(2026, 11, 4), False)),
                 ({"due_weekday": "tomorrow", "due_in_days": 1}, (date(2026, 11, 2), False)),
                 ({"due_weekday": "someday"}, (None, True))]
        got = [vi.resolve_due(vi.VoiceOut(**p), d0) for p, _ in cases]
        expect("fechas: lunes→2-nov · Miércoles→4-nov · 'tomorrow' con +1→2-nov · un día que no existe, solo → inválida",
               got == [w for _, w in cases], got)
        # Un día de la semana que el dueño no dijo se descarta; uno que sí dijo (en inglés o en español) se usa.
        got = [vi._check({"amount": "10", "concept": "x", **p}, d0, t)[0]["due_date"] for p, t in [
            ({"due_in_days": 7, "due_weekday": "friday"}, "invoice [CUSTOMER] 10 for x, due in a week"),
            ({"due_in_days": 1, "due_weekday": "tuesday"}, "factura a [CUSTOMER] 10 por x, vence mañana"),
            ({"due_weekday": "wednesday"}, "factura a [CUSTOMER] 10 por x, vence el miércoles"),
            ({"due_weekday": "friday"}, "invoice [CUSTOMER] 10 for x, due next Friday")]]
        expect("un día de la semana que no se dijo no cuenta: semana→8-nov · mañana→2-nov · miércoles→4-nov · Friday→6-nov",
               got == ["2026-11-08", "2026-11-02", "2026-11-04", "2026-11-06"], got)
        got = [vi._check({"amount": "10", "concept": "x", **p}, d0, t)[0]["due_date"] for p, t in [
            ({"due_day": 5, "due_month": 10}, "factura a [CUSTOMER] 10 por x, vence el 5"),
            ({"due_day": 20, "due_month": 12, "due_in_days": 9}, "[CUSTOMER], 10 for x, due the 20th"),
            ({"due_day": 10, "due_month": 12}, "invoice [CUSTOMER] 10 for x, due December 10th"),
            ({"due_day": 10, "due_month": 12}, "factura a [CUSTOMER] 10 por x, vence el 10 de diciembre"),
            ({"due_day": 10, "due_month": 12}, "invoice [CUSTOMER] 10 for x, due 12/10")]]
        expect("un mes que no se dijo no cuenta: el 5→5-nov · the 20th→20-nov · December 10th, 10 de diciembre y 12/10→10-dic",
               got == ["2026-11-05", "2026-11-20", "2026-12-10", "2026-12-10", "2026-12-10"], got)

        # 12-nov: si el modelo escribe los campos dentro del concepto, un reintento; el segundo intento se usa.
        n0 = len(SEEN)
        QUEUE[:] = [{"amount": "1800", "concept": "filter replacement for [CUSTOMER], due next Friday, due_weekday: friday"},
                    {"amount": "1800", "due_in_days": 7, "concept": "filter replacement"}]
        b = client.post(url, headers=cl, json={"text": "Acme, 1800 dollars for filter replacement, due in a week"}).json()
        expect("campos dentro del concepto → un reintento, y se usa el bueno (2 llamadas)",
               len(SEEN) - n0 == 2 and b["fields"]["concept"] == "filter replacement"
               and b["fields"]["due_date"] == (today + timedelta(days=7)).isoformat(), (len(SEEN) - n0, b["fields"]))

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
