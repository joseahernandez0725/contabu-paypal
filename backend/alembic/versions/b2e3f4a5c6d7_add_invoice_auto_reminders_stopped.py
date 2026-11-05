"""invoice.auto_reminders_stopped_at: el dueño detuvo los recordatorios automáticos de una factura (FASE 16, 5-nov)

PayPal no deja reactivarlos (`/cancel-reminders` es irreversible): se guarda cuándo, para que la pantalla lo diga.

Revision ID: b2e3f4a5c6d7
Revises: a1d2e3f4b5c6
Create Date: 2026-11-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2e3f4a5c6d7"
down_revision: Union[str, Sequence[str], None] = "a1d2e3f4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoice", sa.Column("auto_reminders_stopped_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice", "auto_reminders_stopped_at")
