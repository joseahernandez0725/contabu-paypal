"""
CERRAR EL MES (FASE 16, área PayPal / portal del dueño · 31-oct-2026).

Decisión de Jose (31-oct): el bookkeeper CIERRA cada mes; al cerrarlo, sus categorías suben al portal del dueño y el
mes **ya no se puede modificar**. Es el "cerrar el mes = pegar como valores" del 03-oct (la fórmula del libro).
Reabrir es una decisión registrada (quién y cuándo), y se puede cerrar de nuevo (otra foto).

Tres piezas:
  1. **El guardián** (`_guard`, en el `before_flush` de TODA sesión): un movimiento con fecha en un mes cerrado no
     cambia de sección, categoría, subcategoría, monto, fecha ni dirección, ni se borra → `MonthClosedError` → 409.
     Es UNO para los ~10 caminos que cambian una categoría (reglas, edición, deshacer, plan de cuentas, banco…) y
     para los que se agreguen después: lo que nadie previó falla fuerte, no cambia un mes cerrado en silencio.
     Lo que entra NUEVO a un mes cerrado (el banco asienta tarde) sí entra: queda "después del cierre" (`late`) y
     la foto no cambia; el bookkeeper decide reabrir (Jose, 31-oct: por ahora, solo reabrir).
  2. **Los caminos en lote** (el motor de reglas, el banco) SALTAN los meses cerrados con `open_rows()` /
     `locked_months()` en vez de fallar: cambiar una regla no puede quedar bloqueado por un mes cerrado.
  3. **La foto** (`MonthClose.snapshot`): los totales por categoría del mes al cerrarlo, calculados por SQL con
     el signo de su sección (`reports.signed_amount`), y lo Pendiente declarado (regla 4). El portal lee la foto.

Renombrar una cuenta del plan sí llega a los meses cerrados (`allow_rename()`): es la misma cuenta con otro nombre.
Moverla de SECCIÓN no: cambia el resultado de un mes cerrado → 409, hay que reabrirlo.
"""

import contextlib
import contextvars
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import Date, case, cast, event, exists, func, inspect, text
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, select

from auth import console_company, current_user, owned_company
from db import get_session
from categories import CONTRA_LINES
from models import AccountType, Company, CompanyCategory, MonthClose, Transaction, User
from reports import signed_amount

router = APIRouter()

# Lo que un mes cerrado no deja cambiar en sus movimientos: lo que mueve un número o su lugar en el resultado.
# (La procedencia —`source`, `matched_rule_id`—, las notas y las marcas no cambian ninguna cifra.)
LOCKED_FIELDS = ("account_type", "category", "subcategory", "amount", "date", "is_inflow")

_IN = (AccountType.REVENUE, AccountType.OTHER_INCOME)
_OUT = (AccountType.COGS, AccountType.OPERATING_EXPENSE, AccountType.OTHER_EXPENSE)


class MonthClosedError(Exception):
    def __init__(self, month: date):
        super().__init__(f"month closed: {month:%Y-%m}")
        self.month = month


async def on_month_closed(_request, exc: MonthClosedError):
    """El 409 que ve el frontend: `detail` fijo (lo traduce la pantalla) y el mes que hay que reabrir."""
    return JSONResponse(status_code=409, content={"detail": "month_closed", "month": f"{exc.month:%Y-%m}"})


def first_of_month(d: date) -> date:
    return d.replace(day=1)


def _parse_month(raw: str) -> date:
    try:
        y, m = raw.split("-")
        return date(int(y), int(m), 1)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")


def _month_end(m: date) -> date:
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def closable_on(company: Company, m: date) -> date:
    """El primer día en que el mes se puede CERRAR: su último día + los días de espera del libro (Jose, 31-oct: "no se
    puede cerrar un mes hasta que pasen los días de espera"). Es la misma cuenta con que la importación retiene lo
    reciente (`ingestion`: retenido mientras `hoy − fecha < días`): cerrar antes dejaría fuera lo que el banco todavía
    está asentando. Es el "concilia primero, bloquea al final" de Xero (FASE 16.B §9)."""
    from ingestion import settlement_buffer_days   # aquí: ingestion → rules → month_close sería un ciclo
    return date.fromordinal(_month_end(m).toordinal() + settlement_buffer_days(company))


# ── Qué está cerrado ─────────────────────────────────────────────────────────────────────────────

def locked_months(session: Session, company_id: int) -> set[date]:
    """Los meses cerrados VIGENTES del libro (día 1 de cada uno)."""
    with session.no_autoflush:
        return set(session.exec(
            select(MonthClose.month).where(MonthClose.company_id == company_id)
            .where(MonthClose.reopened_at.is_(None))
        ).all())


def open_rows():
    """Filtro SQL para los caminos EN LOTE: solo los movimientos de meses NO cerrados. `.where(open_rows())`."""
    return ~exists().where(
        MonthClose.company_id == Transaction.company_id,
        MonthClose.reopened_at.is_(None),
        MonthClose.month == cast(func.date_trunc("month", Transaction.date), Date),
    )


def is_locked(session: Session, tx: Transaction) -> bool:
    return first_of_month(tx.date) in locked_months(session, tx.company_id)


_allow_rename: contextvars.ContextVar[bool] = contextvars.ContextVar("month_close_allow_rename", default=False)


@contextlib.contextmanager
def allow_rename():
    """Dentro de este bloque, cambiar SOLO el nombre de la categoría de un movimiento cerrado está permitido
    (renombrar una cuenta del plan). Cambiarle la sección sigue bloqueado."""
    token = _allow_rename.set(True)
    try:
        yield
    finally:
        _allow_rename.reset(token)


# ── El guardián ─────────────────────────────────────────────────────────────────────────────────

@event.listens_for(OrmSession, "before_flush")
def _guard(session, _flush_context, _instances) -> None:
    cache: dict[int, set[date]] = {}

    def closed(company_id: int) -> set[date]:
        if company_id not in cache:
            with session.no_autoflush:
                cache[company_id] = set(session.execute(
                    select(MonthClose.month).where(MonthClose.company_id == company_id)
                    .where(MonthClose.reopened_at.is_(None))
                ).scalars().all())
        return cache[company_id]

    for obj in list(session.deleted):
        if isinstance(obj, Transaction) and obj.date and first_of_month(obj.date) in closed(obj.company_id):
            raise MonthClosedError(first_of_month(obj.date))

    for obj in list(session.dirty):
        if not isinstance(obj, Transaction):
            continue
        state = inspect(obj)
        changed = {f for f in LOCKED_FIELDS if state.attrs[f].history.has_changes()}
        if not changed or (changed == {"category"} and _allow_rename.get()):
            continue
        old_date = (state.attrs["date"].history.deleted or [obj.date])[0]
        for d in {old_date, obj.date}:
            if d and first_of_month(d) in closed(obj.company_id):
                raise MonthClosedError(first_of_month(d))


# ── La foto del mes ─────────────────────────────────────────────────────────────────────────────

def _money(x) -> str:
    return str((x or Decimal("0")).quantize(Decimal("0.01")))


def build_snapshot(session: Session, company_id: int, m: date) -> dict:
    """Los totales del mes, calculados por SQL (regla de oro): por (lado, categoría), por lado, y lo Pendiente.
    `in` = Ingresos y Otros ingresos · `out` = Costo de ventas, Gastos de operación y Otros gastos. Fuera del P&L
    (transferencias, aportes y retiros del dueño) no entra: no es ni lo que entró ni lo gastado del negocio."""
    start, end = m, _month_end(m)
    side = case((Transaction.account_type.in_(_IN), "in"), else_="out")
    base = (Transaction.company_id == company_id, Transaction.date >= start, Transaction.date <= end)
    pl = Transaction.account_type.in_(_IN + _OUT)

    rows = session.exec(
        select(side, Transaction.category, func.sum(signed_amount()), func.count(Transaction.id))
        .where(*base).where(pl).group_by(side, Transaction.category)
        .order_by(side, func.sum(signed_amount()).desc())
    ).all()
    totals = dict(session.exec(
        select(side, func.sum(signed_amount())).where(*base).where(pl).group_by(side)
    ).all())
    pend = session.exec(
        select(func.count(Transaction.id),
               func.sum(case((Transaction.is_inflow.is_(True), Transaction.amount), else_=0)),
               func.sum(case((Transaction.is_inflow.is_(False), Transaction.amount), else_=0)))
        .where(*base).where(Transaction.account_type.is_(None))
    ).one()
    return {
        "in": [{"category": c or "Other", "amount": _money(a), "count": n} for s, c, a, n in rows if s == "in"],
        "out": [{"category": c or "Other", "amount": _money(a), "count": n} for s, c, a, n in rows if s == "out"],
        "total_in": _money(totals.get("in")),
        "total_out": _money(totals.get("out")),
        "pending": {"count": pend[0] or 0, "money_in": _money(pend[1]), "money_out": _money(pend[2])},
    }


def owner_months(session: Session, company_id: int) -> list[dict]:
    """Lo que el portal del dueño muestra: los meses cerrados VIGENTES con su foto, el más reciente primero."""
    closes = session.exec(
        select(MonthClose).where(MonthClose.company_id == company_id).where(MonthClose.reopened_at.is_(None))
        .order_by(MonthClose.month.desc())
    ).all()
    return [{"month": f"{c.month:%Y-%m}", "closed_at": c.closed_at.isoformat(), **(c.snapshot or {})} for c in closes]


def owner_years(session: Session, company_id: int) -> list[dict]:
    """El AÑO del dueño (Jose, 31-oct: "una acumulación del año, no un reporte mes a mes"): por cada año con meses
    cerrados, sus totales, el acumulado mes a mes y lo gastado por categoría en el año. TODO sale de SQL sobre las
    FOTOS de los meses cerrados vigentes (nunca del libro en vivo): `json_array_elements` + `SUM`, y el acumulado
    con una ventana (`SUM() OVER`). El más reciente primero."""
    p = {"c": company_id}
    open_ = "company_id = :c AND reopened_at IS NULL AND snapshot IS NOT NULL"
    months = session.execute(text(f"""
        SELECT EXTRACT(YEAR FROM month)::int AS y, to_char(month, 'YYYY-MM') AS m,
               (snapshot->>'total_in')::numeric AS t_in, (snapshot->>'total_out')::numeric AS t_out,
               SUM((snapshot->>'total_in')::numeric)  OVER (PARTITION BY EXTRACT(YEAR FROM month) ORDER BY month) AS cum_in,
               SUM((snapshot->>'total_out')::numeric) OVER (PARTITION BY EXTRACT(YEAR FROM month) ORDER BY month) AS cum_out
        FROM monthclose WHERE {open_} ORDER BY month"""), p).all()
    totals = session.execute(text(f"""
        SELECT EXTRACT(YEAR FROM month)::int AS y,
               SUM((snapshot->>'total_in')::numeric), SUM((snapshot->>'total_out')::numeric),
               SUM((snapshot->'pending'->>'count')::int),
               SUM((snapshot->'pending'->>'money_in')::numeric), SUM((snapshot->'pending'->>'money_out')::numeric)
        FROM monthclose WHERE {open_} GROUP BY 1"""), p).all()
    cats = session.execute(text(f"""
        SELECT EXTRACT(YEAR FROM mc.month)::int AS y, side, e->>'category' AS cat,
               SUM((e->>'amount')::numeric) AS amt, SUM((e->>'count')::int) AS n
        FROM monthclose mc
        CROSS JOIN LATERAL (SELECT 'in' AS side, x FROM json_array_elements(mc.snapshot->'in') x
                            UNION ALL SELECT 'out', x FROM json_array_elements(mc.snapshot->'out') x) s(side, e)
        WHERE mc.company_id = :c AND mc.reopened_at IS NULL AND mc.snapshot IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY 1, 2, amt DESC"""), p).all()
    out = []
    for y, t_in, t_out, pn, p_in, p_out in sorted(totals, key=lambda r: r[0], reverse=True):
        out.append({
            "year": y,
            "total_in": _money(t_in), "total_out": _money(t_out),
            "months": [{"month": m, "total_in": _money(a), "total_out": _money(b),
                        "cum_in": _money(ci), "cum_out": _money(co)} for yy, m, a, b, ci, co in months if yy == y],
            "in_by_category": [{"category": c, "amount": _money(a), "count": int(n)} for yy, sd, c, a, n in cats
                               if yy == y and sd == "in"],
            "out_by_category": [{"category": c, "amount": _money(a), "count": int(n)} for yy, sd, c, a, n in cats
                                if yy == y and sd == "out"],
            "pending": {"count": int(pn or 0), "money_in": _money(p_in), "money_out": _money(p_out)},
        })
    return out


# ── Las rutas de la CONSOLA (el cliente no las alcanza: check_permissions.py) ───────────────────

def _active(session: Session, company_id: int, m: date):
    return session.exec(
        select(MonthClose).where(MonthClose.company_id == company_id).where(MonthClose.month == m)
        .where(MonthClose.reopened_at.is_(None))
    ).first()


@router.get("/api/companies/{company_id}/months")
def list_months(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Los meses del libro para la tarjeta "Cierre de mes": cuántos movimientos, cuántos Pendientes, si está
    cerrado (cuándo y quién) y cuántos llegaron DESPUÉS del cierre. El mes en curso sale como `current` y uno que
    terminó pero sigue en sus días de espera, como `waiting` (con `closable_on`): ninguno de los dos se cierra."""
    company = console_company(company_id, user, session)
    month = cast(func.date_trunc("month", Transaction.date), Date)
    stats = session.exec(
        select(month, func.count(Transaction.id),
               func.sum(case((Transaction.account_type.is_(None), 1), else_=0)))
        .where(Transaction.company_id == company_id).group_by(month)
    ).all()
    closes = {c.month: c for c in session.exec(
        select(MonthClose).where(MonthClose.company_id == company_id).where(MonthClose.reopened_at.is_(None))
    ).all()}
    emails = {u.id: u.email for u in session.exec(
        select(User).where(User.id.in_({c.closed_by_user_id for c in closes.values()} - {None}))
    ).all()} if closes else {}
    # Cuentas con total NEGATIVO en su sección (5-nov): lo contrario superó a lo natural (p. ej. un depósito escrito como
    # gasto). El signo es correcto (`signed_amount`), pero casi siempre es un error de clasificación: se avisa al cerrar.
    # Las cuentas que restan por naturaleza (Returns and allowances, por su línea del IRS) no se avisan.
    contra = select(CompanyCategory.name).where(CompanyCategory.company_id == company_id) \
        .where(CompanyCategory.irs_line.in_(CONTRA_LINES))
    negative: dict[date, list[dict]] = {}
    for m, c, a in session.exec(
        select(month, Transaction.category, func.sum(signed_amount()))
        .where(Transaction.company_id == company_id).where(Transaction.account_type.in_(_IN + _OUT))
        .where(Transaction.category.not_in(contra))
        .group_by(month, Transaction.category).having(func.sum(signed_amount()) < 0)
        .order_by(month, func.sum(signed_amount()))
    ).all():
        negative.setdefault(m, []).append({"category": c or "Other", "amount": _money(a)})
    today = date.today()
    this_month = first_of_month(today)
    out = []
    for m, n, pending in sorted(stats, key=lambda r: r[0], reverse=True):
        c = closes.get(m)
        late = session.exec(
            select(func.count(Transaction.id)).where(Transaction.company_id == company_id)
            .where(Transaction.date >= m).where(Transaction.date <= _month_end(m))
            .where(Transaction.created_at > c.closed_at)
        ).one() if c else 0
        ready = closable_on(company, m)
        out.append({
            "month": f"{m:%Y-%m}", "count": n, "pending": int(pending or 0),
            "status": "closed" if c else "current" if m >= this_month else "waiting" if today < ready else "open",
            "closable_on": ready.isoformat(),
            "closed_at": c.closed_at.isoformat() if c else None,
            "closed_by": emails.get(c.closed_by_user_id) if c else None,
            "late": late,
            "negative": negative.get(m, []),
        })
    return out


@router.post("/api/companies/{company_id}/months/{month}/close")
def close_month(company_id: int, month: str, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Cierra un mes que ya terminó: guarda la foto y desde ese momento el guardián lo congela.
    Se puede cerrar con Pendientes (Jose, 31-oct): la foto los declara y el dueño los ve."""
    company = owned_company(company_id, user, session, write=True)
    m = _parse_month(month)
    if m >= first_of_month(date.today()):
        raise HTTPException(status_code=422, detail="month_not_over")
    if date.today() < closable_on(company, m):
        raise HTTPException(status_code=422, detail={"code": "waiting_days", "closable_on": closable_on(company, m).isoformat()})
    if _active(session, company_id, m):
        raise HTTPException(status_code=409, detail="already_closed")
    close = MonthClose(company_id=company_id, month=m, closed_by_user_id=user.id,
                       snapshot=build_snapshot(session, company_id, m))
    session.add(close)
    session.commit()
    return {"month": f"{m:%Y-%m}", "status": "closed"}


@router.post("/api/companies/{company_id}/months/{month}/reopen")
def reopen_month(company_id: int, month: str, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    """Reabre un mes cerrado: queda quién y cuándo (la fila no se borra). El dueño deja de verlo hasta que se
    cierre de nuevo."""
    owned_company(company_id, user, session, write=True)
    m = _parse_month(month)
    close = _active(session, company_id, m)
    if close is None:
        raise HTTPException(status_code=404, detail="not_closed")
    close.reopened_at = datetime.now(timezone.utc)   # igual que `models._utcnow` (closed_at, created_at)
    close.reopened_by_user_id = user.id
    session.add(close)
    session.commit()
    return {"month": f"{m:%Y-%m}", "status": "open"}
