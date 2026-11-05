"""add invoice.reminder_count + invoice.last_reminder_at (FASE 16 — recordatorios de cobro)

Cuántas veces se mandó el recordatorio oficial de PayPal y cuándo fue el último: la pantalla lo muestra y el código
no deja mandar otro antes de 24 horas.

Revision ID: f3c4d5e6a7b8
Revises: e1a2b3c4d5f7
Create Date: 2026-11-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3c4d5e6a7b8"
down_revision: Union[str, Sequence[str], None] = "e1a2b3c4d5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoice", sa.Column("reminder_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("invoice", sa.Column("last_reminder_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice", "last_reminder_at")
    op.drop_column("invoice", "reminder_count")
