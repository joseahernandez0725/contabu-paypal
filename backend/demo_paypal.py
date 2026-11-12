"""
EL LIBRO SEMBRADO DE `paypal.contabu.com` (FASE 16 Paso 7, 2-nov-2026).

Decisión de Jose: un solo libro de prueba (Rivera HVAC LLC, ficticio) con todo lo que el portal muestra: tres meses
cerrados, ventas de PayPal con su comisión, un reembolso, retiros emparejados con su depósito, una compra emparejada con
su cargo y facturas cobradas. La visita guiada lo lleva `linked` a la cuenta de sandbox de la app; en el libro de un juez
no hay conexión: entra con las credenciales de su propia app.

Las filas pasan por el mismo camino que una sincronización real (`paypal.rows_for` → `paypal.insert_row`). Los ids son
inventados ("DEMO…"): nada de esto existe en PayPal. Las dos facturas abiertas (una vencida y una por vencer) van en
TODOS los libros desde el 12-nov, también en el del juez, para que Cobros > Análisis tenga a quién cobrarle; con fechas
relativas al día en que se siembra el libro, y `invoices.is_demo` hace que "Actualizar estado" las salte y que cobrarlas
responda `demo_invoice` en vez de un error de PayPal. Solo `flush()`: el commit es de quien llama.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import Session

import demo_data
import month_close
import paypal
from categories import seed_chart
from ingestion import ingest_parsed, make_fingerprint
from models import (AccountType, CategoryRule, Company, Contractor, ContractorPayment, Firm, Invoice, InvoiceCustomer,
                    MonthClose, PayPalConnection, PayPalTransferMatch, RuleScope, Transaction, User)
from rules import load_rules

CUSTOMERS = [("Maple Grove Apartments", "billing@maplegrove.example.com"),
             ("Bright Path Dental", "office@brightpath.example.com"),
             ("Oak Street Bakery", "owner@oakstreet.example.com")]
# (cliente, concepto, monto, día del pago)
PAID_INVOICES = [(0, "Rooftop unit repair", "1850.00", date(2026, 7, 9)),
                 (1, "Quarterly maintenance", "640.00", date(2026, 7, 22)),
                 (2, "Walk-in cooler service", "925.00", date(2026, 8, 6)),
                 (0, "Boiler inspection", "480.00", date(2026, 8, 19)),
                 (1, "Thermostat replacement", "1275.00", date(2026, 9, 3)),
                 (2, "Quarterly maintenance", "640.00", date(2026, 9, 17))]
REFUND = (2, "-125.00", date(2026, 8, 25))
WITHDRAWALS = [("1800.00", date(2026, 7, 28)), ("2200.00", date(2026, 9, 21))]
PURCHASE = ("Delta Valley Parts", "312.40", date(2026, 8, 12))
# Las facturas abiertas (12-nov): (cliente, número, concepto, monto, días desde hoy al vencimiento). La vencida sigue
# vencida y la otra sigue "por vencer" durante la calificación (1 al 15-dic) aunque el libro se siembre hoy.
OPEN_INVOICES = [(0, "0007", "Chiller tune-up", "2150.00", -27),
                 (1, "0008", "Filter replacement", "780.00", 45)]
CLOSED = [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]

# ── El libro del BANCO (5-nov, revisión 1 del demo; plan aprobado por Jose) ──────────────────────────────────────
# Propio de este demo: NO usa `demo_data.BOOKS` (ese libro lo siguen usando Nebius, las mediciones y `check_formula`).
# Todos los comercios, el banco, la nómina y el seguro son INVENTADOS (✅ buscados en la web el 5-nov: "Concord" es una
# marca de equipos HVAC y "Larkspur Courts" un complejo real, por eso no se usan). Julio a septiembre quedan cerrados y
# LIMPIOS (todo con regla o escrito a mano); octubre queda abierto con 4 Pendientes para enseñar el cierre. Sin errores
# sembrados para el revisor: la visita no usa IA.
NAME, SECTOR, ACCOUNT = "Rivera HVAC LLC", "construction", "****4471"
BANK_START, BANK_END = date(2026, 7, 1), date(2026, 10, 31)

BANK_RULES = [
    ("MOBILE DEPOSIT", AccountType.REVENUE, "Gross receipts or sales", "Customer checks"),
    ("BRANCH DEPOSIT CASH", AccountType.REVENUE, "Gross receipts or sales", "Cash sales"),
    ("CLAYTON RIDGE HVAC", AccountType.COGS, "Materials", "Clayton Ridge HVAC Supply"),
    ("DELTA VALLEY PARTS", AccountType.COGS, "Materials", "Delta Valley Parts"),
    ("BAYSIDE FUEL", AccountType.OPERATING_EXPENSE, "Car and truck expenses", "Bayside Fuel"),
    ("MILLBROOK LANE", AccountType.OPERATING_EXPENSE, "Rent or lease – other business property", "Millbrook Lane Properties"),
    ("ALDER CREEK INSURANCE", AccountType.OPERATING_EXPENSE, "Insurance", "Alder Creek Insurance"),
    ("KESTREL WIRELESS", AccountType.OPERATING_EXPENSE, "Utilities", "Kestrel Wireless"),
    ("EAST HILLS POWER", AccountType.OPERATING_EXPENSE, "Utilities", "East Hills Power & Gas"),
    ("LEDGERLINE PAYROLL", AccountType.OPERATING_EXPENSE, "Wages", "Ledgerline Payroll"),
    ("EFTPS", AccountType.OPERATING_EXPENSE, "Payroll taxes", "IRS"),
    ("FIELDNOTE APP", AccountType.OPERATING_EXPENSE, "Software", "Fieldnote"),
    ("MONTHLY SERVICE FEE", AccountType.OPERATING_EXPENSE, "Bank fees", "Harbor Point Bank"),
    ("ZELLE PAYMENT TO DANIEL RIVERA", AccountType.NON_PL, "Owner Draw", "Daniel Rivera"),
]
# Cheques a subcontratistas: el banco no dice a quién; el bookkeeper los escribió a mano (lo manual manda).
SUB_CHECKS = [(date(2026, 7, 14), "CHECK 1041", "2350.00", "Tallowmere Mechanical"),
              (date(2026, 8, 20), "CHECK 1046", "2900.00", "Tallowmere Mechanical"),
              (date(2026, 9, 16), "CHECK 1049", "1850.00", "Quillbrook Duct Works")]
# Lo que el bookkeeper todavía no resolvió: solo en octubre, el mes abierto.
OCTOBER_PENDING = [(date(2026, 10, 9), "CHECK 1052", "640.00", False),
                   (date(2026, 10, 15), "ZELLE PAYMENT FROM T NGUYEN", "450.00", True),
                   (date(2026, 10, 22), "POS PURCHASE 4417 HARDWARE STORE ANTIOCH CA", "84.12", False),
                   (date(2026, 10, 27), "ATM WITHDRAWAL 0931 ANTIOCH CA", "200.00", False)]


def _bank_dates(pick) -> list[date]:
    return [d for d in (BANK_START + timedelta(days=i) for i in range((BANK_END - BANK_START).days + 1)) if pick(d)]


def _bank_book() -> "demo_data.Book":
    """Las filas del banco ****4471, de julio a octubre. Montos de un generador con semilla: siempre las mismas."""
    b = demo_data.Book("paypal-rivera")
    weekly = lambda wd: _bank_dates(lambda d: d.weekday() == wd)  # noqa: E731
    monthly = lambda day: _bank_dates(lambda d: d.day == day)     # noqa: E731
    spread = lambda n: demo_data.spread(n, b.rng, BANK_START, BANK_END)  # noqa: E731
    b.add(weekly(3), lambda d: f"MOBILE DEPOSIT REF {d:%m%d}{b.rng.randint(100, 999)}", 2400, 4600, inflow=True)
    b.add(spread(4), "BRANCH DEPOSIT CASH HARBOR POINT BANK", 300, 550, inflow=True)
    b.add(spread(10), "CLAYTON RIDGE HVAC SUPPLY #1462 CONCORD CA", 300, 1600)
    b.add(spread(6), "DELTA VALLEY PARTS 0331 HAYWARD CA", 150, 700)
    b.add(spread(16), "BAYSIDE FUEL 0209 PITTSBURG CA", 62, 118)
    b.add(monthly(1), "ACH DEBIT MILLBROOK LANE PROPERTIES RENT", 2400)
    b.add(monthly(5), "ALDER CREEK INSURANCE PREMIUM", 412.50)
    b.add(monthly(12), "KESTREL WIRELESS AUTOPAY", 148.32)
    b.add(monthly(18), "EAST HILLS POWER & GAS ONLINE PMT", 176, 262)
    b.add([d for d in _bank_dates(lambda d: d >= date(2026, 7, 10)) if (d - date(2026, 7, 10)).days % 14 == 0],
          "LEDGERLINE PAYROLL NET PAY RIVERA HVAC", 1550, 1700)
    net = {m: sum(-a for d, desc, a, _t in b.rows if desc.startswith("LEDGERLINE") and d.month == m) for m in (7, 8, 9)}
    # La 941 del mes anterior, el 15 (≈ 32 % del neto: retenciones + Seguro Social y Medicare de las dos partes).
    for d, base, k in ((date(2026, 7, 15), net[7], Decimal("0.31")), (date(2026, 8, 14), net[7], Decimal("0.32")),
                       (date(2026, 9, 15), net[8], Decimal("0.32")), (date(2026, 10, 15), net[9], Decimal("0.32"))):
        b.add([d], "IRS USATAXPYMT EFTPS RIVERA HVAC LLC", float((base * k).quantize(Decimal("0.01"))))
    b.add(monthly(9), "FIELDNOTE APP SUBSCRIPTION", 59.00)
    b.add(monthly(28), "MONTHLY SERVICE FEE", 15)
    b.add(monthly(20), "ONLINE TRANSFER TO SAVINGS XXXXXX1209", 1000)
    b.add(monthly(25), "ONLINE TRANSFER TO CREDIT CARD XXXXXX8823", 650, 1400)
    b.add(spread(6), "ZELLE PAYMENT TO DANIEL RIVERA", 1200, 1800)
    for d, desc, amount, who in SUB_CHECKS:
        b.add([d], desc, float(amount), tag=f"sub:{who}")
    for d, desc, amount, inflow in OCTOBER_PENDING:
        b.add([d], desc, float(amount), inflow=inflow)
    return b


def _seed_bank_book(session: Session, firm: Firm, owner: User) -> Company:
    """El libro y su banco, por el MISMO motor que un CSV (`ingest_parsed`: regla → transferencia → Pendiente)."""
    company = Company(name=NAME, sector=SECTOR, user_id=owner.id, firm_id=firm.id)
    session.add(company)
    session.flush()
    seed_chart(session, company)
    session.add_all([CategoryRule(scope=RuleScope.COMPANY, company_id=company.id, match_text=m, account_type=at,
                                  category=cat, subcategory=sub) for m, at, cat, sub in BANK_RULES])
    session.flush()
    book = _bank_book()
    book.rows.sort(key=lambda r: (r[0], r[1]))
    parsed = [(d, desc, signed, ACCOUNT, make_fingerprint(d, desc, signed, ACCOUNT)) for d, desc, signed, _t in book.rows]
    ids = ingest_parsed(session, company.id, SECTOR, parsed, commit=False)["transaction_ids"]
    assert len(ids) == len(book.rows), "el motor omitió filas del demo"
    for (d, _desc, _s, tag), tid in zip(book.rows, ids):
        if tag and tag.startswith("sub:"):
            demo_data._manual(session, session.get(Transaction, tid), AccountType.COGS, "Contract labor – subcontractors",
                              tag[4:], owner, datetime.combine(d + timedelta(days=2), datetime.min.time()))
    session.flush()
    return company


def _fee(gross: str) -> str:
    """La comisión publicada de una factura en EE. UU. (3.49% + $0.49, `FASE 16.A`), en negativo como la manda PayPal."""
    return str(-(Decimal(gross) * Decimal("0.0349") + Decimal("0.49")).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _event(tid: str, code: str, value: str, day: date, *, fee: str = None, name: str = None) -> dict:
    info = {"transaction_id": tid, "transaction_event_code": code, "transaction_status": "S",
            "transaction_initiation_date": f"{day.isoformat()}T17:00:00+0000",
            "transaction_amount": {"currency_code": "USD", "value": value}}
    if fee:
        info["fee_amount"] = {"currency_code": "USD", "value": fee}
    return {"transaction_info": info, "payer_info": {"payer_name": {"alternate_full_name": name}} if name else {}}


def _bank_row(session: Session, company: Company, account: str, day: date, description: str, amount: str, inflow: bool) -> Transaction:
    """El otro lado en el banco, ya emparejado: `Transfer`, como lo deja `paypal.match_transfers`."""
    signed = Decimal(amount) if inflow else -Decimal(amount)
    tx = Transaction(company_id=company.id, date=day, description=description, amount=Decimal(amount), is_inflow=inflow,
                     bank_account=account, fingerprint=make_fingerprint(day, description, signed, account),
                     account_type=AccountType.NON_PL, category="Transfer", source="transfer", confidence=1.0)
    session.add(tx)
    session.flush()
    return tx


def seed(session: Session, firm: Firm, owner: User, *, linked: bool) -> list[Company]:
    company = _seed_bank_book(session, firm, owner)
    account = ACCOUNT
    paypal.ensure_accounts(session, company)
    rules = load_rules(session, company.id, company.sector or "")

    customers = []
    for cname, email in CUSTOMERS:
        c = InvoiceCustomer(company_id=company.id, name=cname, email=email)
        session.add(c)
        customers.append(c)
    session.flush()

    def post(event: dict) -> Transaction:
        first = None
        for row in paypal.rows_for(event):
            tx = paypal.insert_row(session, company, row, rules)
            first = first or tx
        return first

    for n, (who, concept, amount, day) in enumerate(PAID_INVOICES, start=1):
        pid = f"DEMOPAY{n:04d}"
        tx = post(_event(pid, "T0006", amount, day, fee=_fee(amount), name=customers[who].name))
        paid_at = datetime(day.year, day.month, day.day, 17, tzinfo=timezone.utc)
        session.add(Invoice(company_id=company.id, customer_id=customers[who].id, paypal_invoice_id=f"INV2-DEMO-{n:04d}",
                            number=f"{n:04d}", concept=concept, amount=Decimal(amount), due_date=day + timedelta(days=5),
                            status="PAID", payment_id=pid, transaction_id=tx.id, sent_at=paid_at - timedelta(days=10),
                            paid_at=paid_at, created_by_user_id=owner.id))
    who, value, day = REFUND
    # Sin comisión de vuelta (5-nov): en EE. UU. PayPal no devuelve lo que cobró al recibir el pago (✅ paypal.com/us/business/
    # paypal-business-fees, "Invoicing Transaction Refunds"; y el sandbox real del 4-nov, deuda #39).
    post(_event("DEMOREF0001", "T1107", value, day, name=customers[who].name))

    for n, (amount, day) in enumerate(WITHDRAWALS, start=1):
        w = post(_event(f"DEMOWD{n:04d}", "T0400", f"-{amount}", day))
        dep = _bank_row(session, company, account, day + timedelta(days=2), f"PAYPAL TRANSFER PPD ID {n}", amount, True)
        session.add(PayPalTransferMatch(company_id=company.id, withdrawal_tx_id=w.id, bank_tx_id=dep.id, status="auto"))
    merchant, amount, day = PURCHASE
    p = post(_event("DEMOBUY0001", "T0001", f"-{amount}", day, name=merchant))
    charge = _bank_row(session, company, account, day + timedelta(days=1), f"PAYPAL *{merchant.upper()}", amount, False)
    session.add(PayPalTransferMatch(company_id=company.id, withdrawal_tx_id=p.id, bank_tx_id=charge.id, status="auto"))
    session.flush()

    # Un pago a un contratista con PayPal Payouts (6-nov, el discurso de "PayPal como la cuenta principal"): en octubre, el
    # mes abierto, registrado por el MISMO camino que un pago real (`contractor_payouts._book`: mano de obra + su comisión).
    import contractor_payouts   # aquí: importa `paypal` y `rules`, que ya cargó este módulo
    quill = Contractor(company_id=company.id, name="Quillbrook Duct Works", email="billing@quillbrook.example.com")
    session.add_all([quill, Contractor(company_id=company.id, name="Tallowmere Mechanical", email="pay@tallowmere.example.com")])
    session.flush()
    pay = ContractorPayment(company_id=company.id, contractor_id=quill.id, amount=Decimal("450.00"), memo="Duct sealing, Oak Street job",
                            status="SUCCESS", paypal_batch_id="DEMOBATCH0001", paypal_item_id="DEMOITEM0001",
                            paypal_tx_id="DEMOPAYOUT0001", fee=Decimal("0.25"), created_by_user_id=owner.id,
                            paid_at=datetime(2026, 10, 20, 17, tzinfo=timezone.utc))
    session.add(pay)
    session.flush()
    contractor_payouts._book(session, company, pay, quill)
    session.flush()

    for m in CLOSED:
        session.add(MonthClose(company_id=company.id, month=m, closed_by_user_id=owner.id,
                               snapshot=month_close.build_snapshot(session, company.id, m)))

    import invoices   # aquí: `invoices` importa los routers de FastAPI, que este módulo no necesita al cargarse
    today = date.today()
    for who, number, concept, amount, due_in in OPEN_INVOICES:
        due = today + timedelta(days=due_in)
        sent = min(due - timedelta(days=30), today - timedelta(days=3))   # nunca en el futuro
        session.add(Invoice(company_id=company.id, customer_id=customers[who].id,
                            paypal_invoice_id=f"{invoices.DEMO_PREFIX}{number}", number=number, concept=concept,
                            amount=Decimal(amount), due_date=due, status="SENT",
                            sent_at=datetime(sent.year, sent.month, sent.day, 17, tzinfo=timezone.utc),
                            created_by_user_id=owner.id))
    if linked:
        session.add(PayPalConnection(company_id=company.id, mode="first_party", environment=paypal.environment(),
                                     last_synced_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                     connected_by_user_id=owner.id))
    session.flush()
    return [company]


if __name__ == "__main__":   # el libro en una base LOCAL: `python demo_paypal.py <uid de desarrollo>`
    import os
    import sys
    from sqlmodel import select
    from db import engine
    from models import FirmMember, FirmRole
    if os.getenv("AUTH_DEV_MODE", "").lower() not in {"1", "true", "yes"} or len(sys.argv) != 2:
        sys.exit("Local only: AUTH_DEV_MODE=true python demo_paypal.py <uid>")
    with Session(engine) as s:
        uid = sys.argv[1]
        if s.exec(select(User).where(User.firebase_uid == uid)).first():
            sys.exit(f"'{uid}' already exists: use a new uid.")
        u = User(firebase_uid=uid, email=f"{uid}@dev.local")
        s.add(u)
        s.flush()
        f = Firm(name="Demo bookkeeping office", owner_user_id=u.id)
        s.add(f)
        s.flush()
        s.add(FirmMember(firm_id=f.id, user_id=u.id, email=u.email, role=FirmRole.OWNER))
        seed(s, f, u, linked=False)
        s.commit()
        print(f"Seeded Rivera HVAC LLC for '{uid}'.")
