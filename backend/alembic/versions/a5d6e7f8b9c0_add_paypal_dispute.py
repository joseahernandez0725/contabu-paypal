"""paypaldispute: las disputas de PayPal de un libro (FASE 16, 3-nov)

Copia de lo que dice PayPal de cada disputa, más el resumen de la IA ya guardado. CASCADE con `company`; SET NULL con
la factura.

Revision ID: a5d6e7f8b9c0
Revises: f3c4d5e6a7b8
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a5d6e7f8b9c0"
down_revision: Union[str, Sequence[str], None] = "f3c4d5e6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paypaldispute",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dispute_id", sa.String(), nullable=False),
        sa.Column("transaction_ref", sa.String(), nullable=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoice.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("seller_response_due", sa.DateTime(), nullable=True),
        sa.Column("pp_updated_at", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "dispute_id", name="uq_paypaldispute_company_id"),
    )
    op.create_index("ix_paypaldispute_company_id", "paypaldispute", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_paypaldispute_company_id", table_name="paypaldispute")
    op.drop_table("paypaldispute")
