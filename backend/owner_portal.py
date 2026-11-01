"""
EL PORTAL DEL DUEÑO (FASE 16, área PayPal / portal del dueño · 31-oct-2026).

El dueño del negocio es el rol `CLIENT` AMPLIADO (no un rol nuevo): entra por invitación del bookkeeper
(`CompanyMember role=CLIENT`) y ve su propio menú —Resumen · Cuentas · Cobros—, nunca la consola.

Dos grupos de rutas, con permisos distintos:
  · `/api/owner/...` → lo que el PORTAL lee. Cada una entra en `CLIENT_ALLOWED` (check_permissions.py)
    con su razón. Las lee cualquier rol con acceso al libro: así la **vista previa** del bookkeeper
    ("Ver como el dueño") pide exactamente lo mismo que el dueño, y lo que no ve uno no lo ve el otro.
  · `/api/companies/{id}/owner-access` → lo que la CONSOLA hace con el dueño: invitarlo, verlo, quitarlo.
    El cliente NO las alcanza (las prueba check_permissions.py como cualquier ruta de consola).

Decisiones de Jose que este archivo respeta (FASE 16.A, 31-oct):
  · el dueño **nunca ve el P&L** ni transacciones sueltas: su Resumen son totales por categoría de cada
    mes **cerrado** por el bookkeeper (Paso 2); el mes en curso no aparece.
  · la IA no decide ni calcula: aquí no hay IA.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from auth import console_company, current_user, effective_role, owned_company, owner_company
from db import get_session
import month_close
from models import Company, CompanyMember, Firm, FirmMember, PayPalConnection, Role, User

router = APIRouter()


# ── LO QUE EL PORTAL LEE ─────────────────────────────────────────────────────────────────────────

@router.get("/api/owner/companies/{company_id}")
def owner_home(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """La portada del portal: el negocio, quién lo lleva y los meses que ya puede ver.

    `managed_by` es el sello de la firma que lleva el libro (`brand_name`, o su `name` si no tiene),
    la misma regla de la FASE 13: el cliente de Jose ve ContaBu porque su firma se llama así.
    `preview` = quien pregunta NO es el dueño (es el bookkeeper mirando): el frontend apaga las acciones.
    `closed_months`: la FOTO de cada mes cerrado (`month_close.owner_months`): lo que entró y lo que salió por
    categoría, sus totales y lo Pendiente declarado. Nunca el libro en vivo, nunca una transacción suelta.
    `years`: lo mismo ACUMULADO por año (`month_close.owner_years`): totales, acumulado mes a mes y por categoría.
    """
    company = owned_company(company_id, user, session)
    role = effective_role(company, user, session)
    firm = session.get(Firm, company.firm_id) if company.firm_id else None
    return {
        "company": {"id": company.id, "name": company.name},
        "managed_by": ((firm.brand_name or "").strip() or firm.name) if firm else None,
        "brand_color": firm.brand_color if firm else None,
        "brand_logo_url": firm.brand_logo_url if firm else None,
        "preview": role != Role.CLIENT,
        "closed_months": month_close.owner_months(session, company.id),
        "years": month_close.owner_years(session, company.id),   # el año acumulado (SQL sobre las fotos)
        # ¿Ya conectó su PayPal? El portal lo pide a la vista mientras no (Jose, 1-nov: es clave en el demo).
        "paypal": {"connected": session.exec(select(PayPalConnection.id)
                                             .where(PayPalConnection.company_id == company.id)).first() is not None},
    }


# ── LO QUE LA CONSOLA HACE CON EL DUEÑO ─────────────────────────────────────────────────────────
# Separado del EQUIPO a propósito (FASE 13.I Paso 8): si el dueño viviera en la misma lista que el
# equipo, el día del apuro se le invita como MEMBER y ve el libro entero.

class OwnerInvite(BaseModel):
    email: str


def _owner_rows(session: Session, company_id: int) -> list[CompanyMember]:
    return session.exec(
        select(CompanyMember).where(CompanyMember.company_id == company_id)
        .where(CompanyMember.role == Role.CLIENT).order_by(CompanyMember.id)
    ).all()


def _out(m: CompanyMember) -> dict:
    return {"id": m.id, "email": m.email, "pending": m.user_id is None}


@router.get("/api/companies/{company_id}/owner-access")
def list_owner_access(company_id: int, user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    """Los dueños con acceso al portal de este libro (normalmente uno; pueden ser dos socios)."""
    console_company(company_id, user, session)
    return [_out(m) for m in _owner_rows(session, company_id)]


@router.post("/api/companies/{company_id}/owner-access")
def invite_owner(company_id: int, payload: OwnerInvite, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    """Invita al dueño por email (rol CLIENT). Lo hace quien puede invitar al equipo: el OWNER del libro.

    Hoy no se manda correo (FASE 13.I Paso 11): el bookkeeper le avisa. Si el email ya tiene cuenta,
    queda enlazado al instante; si no, cuando entre con ese email (`auth.current_user`).

    - Si ese email ya es del EQUIPO de este libro → 409: bajarlo a CLIENT en silencio le quitaría la
      consola a un bookkeeper; subirlo sería al revés. Se resuelve a mano en Ajustes → Equipo.
    - Si es del equipo de la OFICINA o de otro libro de la misma firma → se invita igual y se AVISA
      (`warning`): un contador que además es cliente es un caso real (13.I Paso 8: avisar, no impedir).
    """
    company = owner_company(company_id, user, session)
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="email required")

    existing = session.exec(
        select(CompanyMember).where(CompanyMember.company_id == company_id).where(CompanyMember.email == email)
    ).first()
    if existing and existing.role != Role.CLIENT:
        raise HTTPException(status_code=409, detail="already on the team")
    owner = session.get(User, company.user_id) if company.user_id else None
    if owner and (owner.email or "").lower() == email:
        raise HTTPException(status_code=409, detail="already on the team")

    warning = None
    if company.firm_id is not None:
        in_firm = session.exec(
            select(FirmMember).where(FirmMember.firm_id == company.firm_id).where(FirmMember.email == email)
        ).first()
        other_book = session.exec(
            select(CompanyMember).join(Company, Company.id == CompanyMember.company_id)
            .where(Company.firm_id == company.firm_id).where(CompanyMember.company_id != company_id)
            .where(CompanyMember.email == email).where(CompanyMember.role != Role.CLIENT)
        ).first()
        if in_firm or other_book:
            warning = "team_elsewhere"

    if existing:
        return {**_out(existing), "warning": warning}
    invited = session.exec(select(User).where(User.email == email)).first()
    m = CompanyMember(company_id=company_id, email=email, role=Role.CLIENT,
                      user_id=invited.id if invited else None)
    session.add(m)
    session.commit()
    session.refresh(m)
    return {**_out(m), "warning": warning}


@router.delete("/api/companies/{company_id}/owner-access/{member_id}")
def remove_owner_access(company_id: int, member_id: int, user: User = Depends(current_user),
                        session: Session = Depends(get_session)):
    """Quita el acceso de un dueño al portal. Solo borra filas CLIENT: el equipo se maneja en Ajustes."""
    owner_company(company_id, user, session)
    m = session.get(CompanyMember, member_id)
    if m is None or m.company_id != company_id or m.role != Role.CLIENT:
        raise HTTPException(status_code=404, detail="owner not found")
    session.delete(m)
    session.commit()
    return {"deleted": member_id}
