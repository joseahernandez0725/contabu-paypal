"""add paypalconnection + paypalentry (FASE 16 Paso 3 — PayPal, una cuenta más del libro)

Tablas nuevas: no toca las de otras áreas. ON DELETE CASCADE con `company` (borrar un libro se las lleva) y SET NULL
con `transaction` (si se borra una transacción, la entrada queda como historia y no se vuelve a importar).

Revision ID: b8c9d0e1f2a4
Revises: a7b8c9d0e1f3
Create Date: 2026-10-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a4"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paypalconnection",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(), nullable=False, server_default="first_party"),
        sa.Column("environment", sa.String(), nullable=False, server_default="sandbox"),
        sa.Column("merchant_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("connected_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_paypalconnection_company_id", "paypalconnection", ["company_id"], unique=True)
    op.create_table(
        "paypalentry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paypal_id", sa.String(), nullable=False),
        sa.Column("event_code", sa.String(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transaction.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "paypal_id", name="uq_paypalentry_company_id"),
    )
    op.create_index("ix_paypalentry_company_id", "paypalentry", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_paypalentry_company_id", table_name="paypalentry")
    op.drop_table("paypalentry")
    op.drop_index("ix_paypalconnection_company_id", table_name="paypalconnection")
    op.drop_table("paypalconnection")
