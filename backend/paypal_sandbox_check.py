"""
PRUEBA DEL SANDBOX DE PAYPAL — SOLO SANDBOX (FASE 16, PayPal / portal del dueño · 31-oct-2026).

    cd backend && .venv/bin/python paypal_sandbox_check.py

Responde las preguntas del Paso 0 que la documentación no contesta (`docs/FASE 16.B.md` §8). No toca la
base ni la app: solo llama a PayPal con las llaves de `backend/.env` e imprime qué respondió.
  1. ¿Las llaves sirven? (token OAuth y los permisos que trae la app)
  2. ¿Partner Referrals responde en sandbox SIN ser partner aprobado? → camino `partner` o `first_party`
  3. ¿Invoicing funciona en la cuenta de la app? (crea una factura en BORRADOR y la borra; no envía nada)
  4. ¿Transaction Search funciona en la cuenta de la app? (últimos 30 días, 5 filas)

Nunca imprime las llaves ni el token. 🔴 Se niega a correr si PAYPAL_ENV no es sandbox.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv(".env")

BASE = "https://api-m.sandbox.paypal.com"
FEATURES = ["INVOICE_READ_WRITE", "ADVANCED_TRANSACTIONS_SEARCH", "PAYMENT", "REFUND",
            "ACCESS_MERCHANT_INFORMATION"]


def show(label: str, r: httpx.Response) -> dict:
    """Imprime el código y, si falló, el error de PayPal (name/message/details: no traen secretos)."""
    try:
        body = r.json()
    except ValueError:
        body = {}
    ok = "✅" if r.is_success else "❌"
    print(f"{ok} {label}: HTTP {r.status_code}")
    if not r.is_success:
        print("   ", {k: body.get(k) for k in ("name", "message", "error", "error_description") if body.get(k)})
        for d in (body.get("details") or [])[:3]:
            print("    ·", d.get("issue"), "—", d.get("description"))
    return body


def main() -> None:
    if os.getenv("PAYPAL_ENV", "sandbox") != "sandbox":
        raise SystemExit("🔴 PAYPAL_ENV no es sandbox. Esta prueba SOLO corre en sandbox.")
    cid, secret = os.getenv("PAYPAL_CLIENT_ID"), os.getenv("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit("Faltan PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET en backend/.env (los de Sandbox).")

    with httpx.Client(base_url=BASE, timeout=30) as http:
        # 1. Token
        r = http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=(cid, secret))
        tok = show("1. token OAuth", r)
        if not r.is_success:
            raise SystemExit("Sin token no se puede seguir: revisa que las llaves sean las de SANDBOX.")
        scopes = tok.get("scope", "")
        print(f"    app_id={tok.get('app_id')} · permisos: invoicing={'invoicing' in scopes} · "
              f"reporting={'reporting' in scopes}")
        h = {"Authorization": f"Bearer {tok['access_token']}", "Content-Type": "application/json"}

        # 2. Partner Referrals (la conexión de CADA negocio en producción)
        body = {
            "tracking_id": f"contabu-check-{uuid.uuid4().hex[:8]}",
            "operations": [{
                "operation": "API_INTEGRATION",
                "api_integration_preference": {"rest_api_integration": {
                    "integration_method": "PAYPAL",
                    "integration_type": "THIRD_PARTY",
                    "third_party_details": {"features": FEATURES},
                }},
            }],
            "products": ["EXPRESS_CHECKOUT"],
            "legal_consents": [{"type": "SHARE_DATA_CONSENT", "granted": True}],
            "partner_config_override": {"return_url": "http://localhost:5173/"},
        }
        r = http.post("/v2/customer/partner-referrals", json=body, headers=h)
        ref = show("2. Partner Referrals", r)
        link = next((l["href"] for l in ref.get("links", []) if l.get("rel") == "action_url"), None)
        print("    →", "camino `partner`: hay enlace para que el negocio conecte su PayPal" if link
              else "camino `first_party` para el demo (sin enlace de onboarding)")

        # 3. Invoicing: borrador y se borra (no se envía nada a nadie)
        inv = {"detail": {"currency_code": "USD", "note": "ContaBu sandbox check (draft, deleted)"},
               "items": [{"name": "Sandbox check", "quantity": "1",
                          "unit_amount": {"currency_code": "USD", "value": "1.00"}}]}
        r = http.post("/v2/invoicing/invoices", json=inv, headers={**h, "Prefer": "return=representation"})
        created = show("3. Invoicing: crear borrador", r)
        inv_id = created.get("id") or next(
            (l["href"].rsplit("/", 1)[-1] for l in created.get("links", []) if l.get("rel") == "self"), None)
        if r.is_success and inv_id:
            got = show("   leer el borrador", http.get(f"/v2/invoicing/invoices/{inv_id}", headers=h))
            print(f"    estado={got.get('status')} · total={got.get('amount', {}).get('value')}")
            show("   borrar el borrador", http.delete(f"/v2/invoicing/invoices/{inv_id}", headers=h))

        # 4. Transaction Search (rango máximo: 31 días)
        end = datetime.now(timezone.utc).replace(microsecond=0)
        params = {"start_date": (end - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
                  "end_date": end.isoformat().replace("+00:00", "Z"), "fields": "transaction_info",
                  "page_size": 5}
        r = http.get("/v1/reporting/transactions", params=params, headers=h)
        ts = show("4. Transaction Search (30 días)", r)
        if r.is_success:
            rows = ts.get("transaction_details", [])
            print(f"    filas={ts.get('total_items', len(rows))} · ejemplo:")
            for t in rows[:5]:
                i = t.get("transaction_info", {})
                print("     ", i.get("transaction_event_code"), i.get("transaction_status"),
                      (i.get("transaction_amount") or {}).get("value"), "comisión:",
                      (i.get("fee_amount") or {}).get("value"))


if __name__ == "__main__":
    main()
