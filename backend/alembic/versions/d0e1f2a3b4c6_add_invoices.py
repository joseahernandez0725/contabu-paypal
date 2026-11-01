"""add invoicecustomer + invoice (FASE 16 Paso 5 — Cobros: facturas del dueño con la Invoicing API de PayPal)

Tablas nuevas. CASCADE con `company`; SET NULL con el cliente y con la transacción del libro.

Revision ID: d0e1f2a3b4c6
Revises: c9d0e1f2a3b5
Create Date: 2026-11-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d0e1f2a3b4c6"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoicecustomer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_invoicecustomer_company_id", "invoicecustomer", ["company_id"])
    op.create_table(
        "invoice",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("invoicecustomer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("paypal_invoice_id", sa.String(), nullable=True),
        sa.Column("number", sa.String(), nullable=True),
        sa.Column("concept", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="DRAFT"),
        sa.Column("recipient_view_url", sa.String(), nullable=True),
        sa.Column("payment_id", sa.String(), nullable=True),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transaction.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_invoice_company_id", "invoice", ["company_id"])
    op.create_index("ix_invoice_paypal_invoice_id", "invoice", ["paypal_invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_paypal_invoice_id", table_name="invoice")
    op.drop_index("ix_invoice_company_id", table_name="invoice")
    op.drop_table("invoice")
    op.drop_index("ix_invoicecustomer_company_id", table_name="invoicecustomer")
    op.drop_table("invoicecustomer")
