"""invoiceoffline: pagos y devoluciones de una factura hechos fuera de PayPal (FASE 16, 3-nov)

No crean venta: se emparejan con la fila del banco. CASCADE con `company` y con la factura; SET NULL con la fila del
banco (y única: una fila del banco no paga dos facturas).

Revision ID: b6e7f8a9c0d1
Revises: a5d6e7f8b9c0
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6e7f8a9c0d1"
down_revision: Union[str, Sequence[str], None] = "a5d6e7f8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoiceoffline",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("paypal_ref", sa.String(), nullable=True),
        sa.Column("bank_tx_id", sa.Integer(), sa.ForeignKey("transaction.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("bank_tx_id", name="uq_invoiceoffline_bank_tx"),
    )
    op.create_index("ix_invoiceoffline_company_id", "invoiceoffline", ["company_id"])
    op.create_index("ix_invoiceoffline_invoice_id", "invoiceoffline", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoiceoffline_invoice_id", table_name="invoiceoffline")
    op.drop_index("ix_invoiceoffline_company_id", table_name="invoiceoffline")
    op.drop_table("invoiceoffline")
