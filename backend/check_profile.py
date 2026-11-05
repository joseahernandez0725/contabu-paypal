"""
PRUEBA DE LOS DATOS DEL NEGOCIO EN SUS FACTURAS Y EL NÚMERO PROPIO, SIN RED (FASE 16, 4-nov-2026).

    cd backend && AUTH_DEV_MODE=true .venv/bin/python check_profile.py

Reemplaza a PayPal por uno de mentira que GUARDA lo que recibió y comprueba: los datos se validan con los límites de
PayPal (estado de 2 letras, código postal, teléfono, sitio web); solo el dueño los guarda; la factura nueva lleva a PayPal
la dirección, el teléfono, el sitio, el tax ID, la nota y los términos, el número propio y la orden de compra; un número
repetido (PayPal: 422 en `invoice_number`) → 409 `duplicate_number`; el número que sigue sale de PayPal.
Código de salida 1 si algo falla. No sale a internet (usa el bloqueo de check_permissions).
"""

import os
import struct
import sys
import zlib
from datetime import date, timedelta

os.environ.setdefault("AUTH_DEV_MODE", "true")

import check_permissions as cp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

import paypal  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import InvoiceCustomer, PayPalConnection  # noqa: E402

fails: list[str] = []
SENT: list[dict] = []
USED: set[str] = set()


def expect(label, ok, got):
    print(f"  {'✅' if ok else '❌'} {label} → {got}")
    if not ok:
        fails.append(f"{label} → {got}")


def png(w: int, h: int) -> bytes:
    """Un PNG válido de w × h (gris, sin librerías)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def fake_api(method, path, **kw):
    if method == "POST" and path == "/v2/invoicing/generate-next-invoice-number":
        return {"invoice_number": "RH-2026-0103"}
    if method == "POST" and path == "/v2/invoicing/invoices":
        body = kw.get("json") or {}
        n = (body.get("detail") or {}).get("invoice_number")
        if n and n in USED:
            raise paypal.PayPalError("UNPROCESSABLE_ENTITY", "invoice_number")
        USED.add(n or f"AUTO{len(USED)}")
        SENT.append(body)
        total = body["items"][0]["unit_amount"]["value"]
        return {"id": f"INV2-P{len(SENT)}", "status": "DRAFT", "detail": {"invoice_number": n or "0099"},
                "amount": {"currency_code": "USD", "value": total}}
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
        s.commit()
        cust = c.id
    base = f"/api/owner/companies/{company_id}"
    good = {"address_line": "1200 NW 7th St", "city": "Miami", "state": "fl", "postal_code": "33125",
            "phone": "+1 (305) 555-0199", "website": "https://riverahvac.example", "tax_id": "12-3456789",
            "note": "Thank you for your business!", "terms": "Payment due on the date shown."}
    due = (date.today() + timedelta(days=10)).isoformat()
    try:
        print("\n  --- los datos del negocio y el número propio (sin red) ---")
        r = client.put(f"{base}/invoice-profile", headers=bk, json=good)
        expect("el bookkeeper NO guarda los datos del negocio", r.status_code == 403, r.status_code)
        for field, val in (("state", "Florida"), ("postal_code", "331"), ("phone", "12"), ("website", "riverahvac")):
            r = client.put(f"{base}/invoice-profile", headers=cl, json={**good, field: val})
            expect(f"{field} inválido → 422 bad_{field}", r.status_code == 422 and r.json().get("detail") == f"bad_{field}",
                   (r.status_code, r.json().get("detail")))
        r = client.put(f"{base}/invoice-profile", headers=cl, json=good)
        v = r.json()
        expect("se guardan limpios: estado en mayúsculas, teléfono solo dígitos y sin el +1",
               r.status_code == 200 and v["state"] == "FL" and v["phone"] == "3055550199", (r.status_code, v.get("state"), v.get("phone")))
        r = client.get(f"{base}/invoice-profile", headers=bk)
        expect("el bookkeeper los lee", r.status_code == 200 and r.json()["city"] == "Miami", r.status_code)

        r = client.get(f"{base}/invoices/next-number", headers=cl)
        expect("el número que sigue sale de PayPal", r.json() == {"number": "RH-2026-0103"}, r.json())

        r = client.post(f"{base}/invoices", headers=cl, json={"customer_id": cust, "concept": "Job", "amount": "100.00",
                                                              "due_date": due, "number": "RH-2026-0103", "reference": "PO-778"})
        b = SENT[-1] if SENT else {}
        inv = b.get("invoicer", {})
        expect("la factura lleva a PayPal dirección, teléfono, sitio y tax ID",
               r.status_code == 200 and inv.get("address") == {"address_line_1": "1200 NW 7th St", "admin_area_2": "Miami",
                                                                "admin_area_1": "FL", "postal_code": "33125", "country_code": "US"}
               and inv.get("phones") == [{"country_code": "1", "national_number": "3055550199", "phone_type": "MOBILE"}]
               and inv.get("website") == "https://riverahvac.example" and inv.get("tax_id") == "12-3456789", (r.status_code, inv))
        d = b.get("detail", {})
        expect("y el número propio, la orden de compra, la nota y los términos",
               (d.get("invoice_number"), d.get("reference"), d.get("note"), d.get("terms_and_conditions"))
               == ("RH-2026-0103", "PO-778", good["note"], good["terms"]), d)
        lst = r.json().get("invoices", [])
        expect("la lista muestra el número propio", lst and lst[0]["number"] == "RH-2026-0103", lst and lst[0]["number"])

        r = client.post(f"{base}/invoices", headers=cl, json={"customer_id": cust, "concept": "Job", "amount": "50.00",
                                                              "due_date": due, "number": "RH-2026-0103"})
        expect("un número repetido → 409 duplicate_number (PayPal lo rechaza)", r.status_code == 409
               and r.json().get("detail") == "duplicate_number", (r.status_code, r.json().get("detail")))
        r = client.post(f"{base}/invoices", headers=cl, json={"customer_id": cust, "concept": "Job", "amount": "50.00",
                                                              "due_date": due, "number": "<script>"})
        expect("un número con caracteres raros → 422", r.status_code == 422, r.status_code)
        r = client.post(f"{base}/invoices", headers=cl, json={"customer_id": cust, "concept": "Job", "amount": "50.00", "due_date": due})
        expect("sin número propio: no se manda (PayPal pone el que sigue)",
               r.status_code == 200 and "invoice_number" not in SENT[-1]["detail"], SENT[-1]["detail"].get("invoice_number"))
        print("\n  --- el logo ---")
        logo = f"{base}/invoice-profile/logo"
        r = client.post(logo, headers=bk, files={"file": ("l.png", png(200, 60), "image/png")})
        expect("el bookkeeper NO sube el logo", r.status_code == 403, r.status_code)
        r = client.post(logo, headers=cl, files={"file": ("l.jpg", b"\xff\xd8\xff" + b"0" * 100, "image/jpeg")})
        expect("algo que no es PNG → 422 bad_logo", r.status_code == 422 and r.json().get("detail") == "bad_logo", r.status_code)
        r = client.post(logo, headers=cl, files={"file": ("l.png", png(400, 60), "image/png")})
        expect("más de 250 × 90 → 422 logo_too_big", r.status_code == 422 and r.json().get("detail") == "logo_too_big", r.status_code)
        r = client.post(logo, headers=cl, files={"file": ("l.png", png(250, 90), "image/png")})
        path = r.json().get("logo_path") or ""
        expect("un PNG de 250 × 90 se guarda y da su ruta pública", r.status_code == 200 and path.startswith("/api/public/invoice-logo/"), path)
        expect("en local (sin URL pública) NO va a PayPal", r.json().get("logo_on_paypal") is False, r.json().get("logo_on_paypal"))
        r = client.get(path)
        expect("la ruta pública lo sirve SIN sesión (PayPal la pide)", r.status_code == 200 and r.headers["content-type"] == "image/png"
               and r.content == png(250, 90), (r.status_code, r.headers.get("content-type")))
        r = client.get("/api/public/invoice-logo/adivinanza-de-veinte-caracteres")
        expect("con una clave inventada → 404", r.status_code == 404, r.status_code)
        os.environ["PAYPAL_WEBHOOK_BASE_URL"] = "https://api.example.test"
        try:
            client.post(f"{base}/invoices", headers=cl, json={"customer_id": cust, "concept": "Logo", "amount": "20.00", "due_date": due})
            expect("publicada (con URL pública), la factura lleva logo_url a esa ruta",
                   SENT[-1]["invoicer"].get("logo_url") == "https://api.example.test" + path, SENT[-1]["invoicer"].get("logo_url"))
        finally:
            os.environ.pop("PAYPAL_WEBHOOK_BASE_URL", None)
        r = client.delete(logo, headers=cl)
        expect("quitar el logo: la ruta pública deja de servirlo", r.json().get("logo_path") is None and client.get(path).status_code == 404,
               r.json().get("logo_path"))
    finally:
        with Session(engine) as s:
            cp.drop_fixtures(s)
    print(f"\n  {'✅ todo bien' if not fails else '🔴 FALLÓ: ' + str(len(fails))}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
