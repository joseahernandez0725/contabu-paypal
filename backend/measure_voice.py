"""
MEDIR EL DICTADO (FASE 16, 12-nov-2026): 100 dictados (50 en inglés, 50 en español) por el mismo camino de
`voice_invoice.parse_voice`, sin la base: el código enmascara al cliente, Nemotron (Nebius) extrae, el código valida y
calcula la fecha. No escribe nada. Necesita `NEBIUS_API_KEY`; cuesta menos de un centavo (~$0.005 las 100).

    cd backend && python measure_voice.py          # los 100
    cd backend && python measure_voice.py comma    # 20 con la forma "Cliente, monto for…"

Correcto = el monto exacto, la fecha exacta (la que calcula el código) y el concepto con la palabra esperada. Las fechas
esperadas se cuentan desde hoy, así que el resultado no depende del día en que se corra.
"""
import calendar
import statistics
import sys
import unicodedata
from datetime import date, timedelta
from types import SimpleNamespace

import nemotron
import voice_invoice as vi

T = date.today()
def nxt(wd):   # el próximo día de la semana después de hoy (lunes=0)
    return T + timedelta(days=(wd - T.weekday() - 1) % 7 + 1)
def on(m, d):
    x = date(T.year, m, d)
    return x if x >= T else date(T.year + 1, m, d)
def day(d):
    x = date(T.year, T.month, d)
    if x <= T:
        y, m = (T.year + 1, 1) if T.month == 12 else (T.year, T.month + 1)
        x = date(y, m, d)
    return x
EOM = date(T.year, T.month, calendar.monthrange(T.year, T.month)[1])
D = lambda n: T + timedelta(days=n)

CUSTOMERS = [SimpleNamespace(name=n) for n in ["Maple Grove Apartments", "Bright Path Dental", "Cedar Ridge Clinic",
                                               "Lopez Family Bakery", "Harbor View Motel"]]
AMOUNTS = [("450 dollars", "450 dólares", "450.00"), ("$1,250.50", "1250.50 dólares", "1250.50"),
           ("two thousand one hundred fifty dollars", "dos mil ciento cincuenta dólares", "2150.00"), ("780", "780", "780.00"),
           ("three hundred twenty dollars", "trescientos veinte dólares", "320.00"), ("95 dollars", "95 dólares", "95.00"),
           ("1800 dollars", "1800 dólares", "1800.00"), ("five hundred dollars", "quinientos dólares", "500.00"),
           ("$2,400", "2400 dólares", "2400.00"), ("640 dollars", "640 dólares", "640.00")]
CONCEPTS = [("a furnace tune-up", "el mantenimiento del calentador", "furnace", "calentador"),
            ("the chiller repair", "la reparación del enfriador", "chiller", "enfriador"),
            ("filter replacement", "el cambio de filtros", "filter", "filtro"),
            ("duct cleaning", "la limpieza de ductos", "duct", "ducto"),
            ("a thermostat install", "la instalación del termostato", "thermostat", "termostato"),
            ("emergency service", "el servicio de emergencia", "emergency", "emergencia"),
            ("the rooftop unit installation", "la instalación de la unidad del techo", "rooftop", "techo"),
            ("ice machine maintenance", "el mantenimiento de la máquina de hielo", "ice", "hielo"),
            ("the quarterly maintenance plan", "el plan de mantenimiento trimestral", "maintenance", "mantenimiento"),
            ("the walk-in cooler compressor", "el compresor del cuarto frío", "compressor", "compresor")]
DUES = [("due next Friday", "vence el próximo viernes", nxt(4)), ("due on Monday", "vence el lunes", nxt(0)),
        ("due Wednesday", "vence el miércoles", nxt(2)), ("due tomorrow", "vence mañana", D(1)),
        ("due in 10 days", "vence en 10 días", D(10)), ("due in 30 days", "vence en 30 días", D(30)),
        ("due in a week", "vence en una semana", D(7)), ("due in two weeks", "vence en dos semanas", D(14)),
        ("due at the end of the month", "vence a fin de mes", EOM), ("due the 20th", "vence el 20", day(20)),
        ("due on the 5th", "vence el 5", day(5)), ("due December 10th", "vence el 10 de diciembre", on(12, 10)),
        ("due January 15", "vence el 15 de enero", on(1, 15)), ("", "", D(15))]
NAMES = ["Maple Grove Apartments", "Bright Path Dental", "Cedar Ridge Clinic", "Lopez Family Bakery", "Harbor View Motel"]
EN = ["Invoice {c} {a} for {k}", "Bill {c} {a} for {k}", "{c}, {a} for {k}", "Send {c} an invoice for {a} for {k}"]
ES = ["Factúrale a {c} {a} por {k}", "Factura a {c} por {a} por {k}", "{c}, {a} por {k}", "Mándale a {c} una factura de {a} por {k}"]
CASES = []
for lang, tpls in (("en", EN), ("es", ES)):
    for i in range(50):
        j = i + (7 if lang == "es" else 0)            # el español no repite las mismas combinaciones
        a, k, d, c = AMOUNTS[(j * 3) % 10], CONCEPTS[j % 10], DUES[j % 14], NAMES[j % 5]
        e = lang == "en"
        text = tpls[j % 4].format(c=c, a=a[0] if e else a[1], k=k[0] if e else k[1])
        if d[0]:
            text += ", " + (d[0] if e else d[1])
        CASES.append((lang, text, a[2], d[2], k[2] if e else k[3]))
if "comma" in sys.argv:   # `python measure_voice.py comma`: 20 dictados con la forma "Cliente, monto for…", la que más fallaba
    CASES = []
    for lang in ("en", "es"):
        for j in range(10):
            a, k, d, c = AMOUNTS[(j * 7) % 10], CONCEPTS[(j * 3) % 10], DUES[j % 13], NAMES[j % 5]
            e = lang == "en"
            text = (EN if e else ES)[2].format(c=c, a=a[0] if e else a[1], k=k[0] if e else k[1]) + ", " + (d[0] if e else d[1])
            CASES.append((lang, text, a[2], d[2], k[2] if e else k[3]))
    CASES += [("en", "Cedar Ridge Clinic, 1800 dollars for filter replacement, due next Friday", "1800.00", nxt(4), "filter"),
              ("en", "Cedar Ridge Clinic, 780 for filter replacement, due December 10th", "780.00", on(12, 10), "filter")]

def plain(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")

ok, lat, cost, toks, names_leaked, errors, retries = 0, [], [], [], 0, 0, 0
for i, (lang, text, amount, due, word) in enumerate(CASES, 1):
    named = vi.match_customers(text, CUSTOMERS)
    masked = vi.mask(text, named)
    if any(t in plain(masked) for c in named for t in vi._tokens(c.name)):
        names_leaked += 1
    msg = f"TODAY: {T.isoformat()}\nLANGUAGE: {lang}\nDICTATION: {masked}"
    reply, spent = None, []          # el mismo reintento de `parse_voice`
    for attempt in range(2):
        try:
            got = nemotron.chat_json(vi._SYSTEM, msg, vi.VoiceOut, max_tokens=1200, reasoning=False)
        except nemotron.NemotronError as e:
            last_err = e.code
            if reply is not None or e.code != "out_of_tokens" or attempt == 1:
                break
            continue
        reply = got
        spent.append(got.usage)
        if attempt == 0 and vi.garbled(got.data):
            retries += 1
            continue
        break
    if reply is None:
        errors += 1
        print(f"{i:3} {lang} ❌ error de la API ({last_err}) | {text}")
        continue
    fields, warns = vi._check(reply.data, T, masked)
    good = (fields["amount"] == amount and fields["due_date"] == due.isoformat() and word in plain(fields["concept"]))
    ok += good
    ms, usd, tk = (sum(x.latency_ms for x in spent), sum(x.cost_usd for x in spent),
                   sum(x.prompt_tokens + x.completion_tokens for x in spent))
    lat.append(ms / 1000); cost.append(usd); toks.append(tk)
    print(f"{i:3} {lang} {'✅' if good else '❌'}{' ↻' if len(spent) > 1 else ''} {ms/1000:.2f}s ${usd:.6f} {tk}t "
          f"| {fields['amount']} {fields['due_date']} '{fields['concept']}' {warns if warns else ''}"
          + ("" if good else f" ← esperado {amount} {due} '{word}' · crudo {dict((k, v) for k, v in reply.data.items() if v not in (None, ''))}"))
print(f"\n{ok} de {len(CASES)} correctos · reintentos {retries} · errores de la API {errors} · nombres que llegaron al modelo: {names_leaked} · modelo {nemotron.model_name()}")
print(f"tiempo {min(lat):.2f}–{max(lat):.2f} s (mediana {statistics.median(lat):.2f}) · costo ${min(cost):.6f}–${max(cost):.6f} "
      f"(total ${sum(cost):.5f}) · tokens {min(toks)}–{max(toks)}")
