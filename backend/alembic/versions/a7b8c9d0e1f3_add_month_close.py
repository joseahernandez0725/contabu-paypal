"""add monthclose (FASE 16 — cerrar el mes: el candado y la foto que ve el dueño)

Un mes cerrado congela sus movimientos (el guardián vive en `month_close.py`) y guarda la foto de sus totales
por categoría, que es lo que lee el portal del dueño. Tabla nueva: no toca las de otras áreas.
ON DELETE CASCADE: borrar un libro (`DELETE /companies/{id}`) se lleva sus cierres sin tocar ese código.

Revision ID: a7b8c9d0e1f3
Revises: f6a7b8c9d0e2
Create Date: 2026-10-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f3"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monthclose",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=False),
        sa.Column("closed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reopened_at", sa.DateTime(), nullable=True),
        sa.Column("reopened_by_user_id", sa.Integer(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
    )
    op.create_index("ix_monthclose_company_id", "monthclose", ["company_id"])
    # Un solo cierre VIGENTE por (libro, mes); los reabiertos quedan como historia.
    op.create_index("uq_monthclose_open", "monthclose", ["company_id", "month"], unique=True,
                    postgresql_where=sa.text("reopened_at IS NULL"))


def downgrade() -> None:
    op.drop_index("uq_monthclose_open", table_name="monthclose")
    op.drop_index("ix_monthclose_company_id", table_name="monthclose")
    op.drop_table("monthclose")
