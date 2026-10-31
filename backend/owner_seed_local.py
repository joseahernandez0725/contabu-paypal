"""ContaBu — un DUEÑO DE PRUEBA para el portal del dueño, en tu base LOCAL (FASE 16, 31-oct-2026).

Invita al usuario de desarrollo `dev-owner` como dueño (rol CLIENT) de UN libro inventado del demo (por defecto
Rivera HVAC LLC). Así se prueba el portal con su identidad real, sin login de Firebase:

    python owner_seed_local.py                          # Rivera HVAC LLC
    python owner_seed_local.py "Casa Lupita Taqueria"

Luego, en frontend/.env.local: VITE_DEV_UID=dev-owner (y vuelve a tu uid para la consola).
🔒 Solo acepta los libros INVENTADOS del demo (`demo_data`): nunca invita a nadie al libro real.
"""
import sys

from sqlmodel import Session, select

from db import engine
from models import Company, CompanyMember, Role, User

FICTIONAL = {"Rivera HVAC LLC", "Sparkle Cleaning Co.", "Casa Lupita Taqueria"}
UID, EMAIL = "dev-owner", "owner@dev-owner.test"


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "Rivera HVAC LLC"
    if name not in FICTIONAL:
        print(f"Only the fictional demo books: {', '.join(sorted(FICTIONAL))}.")
        return 1
    with Session(engine) as session:
        company = session.exec(select(Company).where(Company.name == name).order_by(Company.id)).first()
        if company is None:
            print(f"'{name}' isn't in your local base: run seed_local.py first.")
            return 1
        user = session.exec(select(User).where(User.firebase_uid == UID)).first()
        if user is None:
            user = User(firebase_uid=UID, email=EMAIL)
            session.add(user)
            session.flush()
        m = session.exec(select(CompanyMember).where(CompanyMember.company_id == company.id)
                         .where(CompanyMember.email == EMAIL)).first()
        if m is None:
            session.add(CompanyMember(company_id=company.id, user_id=user.id, email=EMAIL, role=Role.CLIENT))
        session.commit()
        print(f"'{UID}' is the owner (CLIENT) of '{name}' (book {company.id}).")
        print("Open the portal with VITE_DEV_UID=dev-owner in frontend/.env.local, then restart vite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
