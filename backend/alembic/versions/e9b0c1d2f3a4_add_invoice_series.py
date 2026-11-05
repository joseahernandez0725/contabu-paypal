"""invoiceseries + invoice.series_id: facturas recurrentes (FASE 16, 4-nov)

CASCADE con `company`; SET NULL con el cliente y, en `invoice.series_id`, con la serie.

Revision ID: e9b0c1d2f3a4
Revises: d8a9b0c1e2f3
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9b0c1d2f3a4"
down_revision: Union[str, Sequence[str], None] = "d8a9b0c1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoiceseries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("invoicecustomer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("paypal_series_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("concept", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("interval_unit", sa.String(), nullable=False),
        sa.Column("interval_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("total_cycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payment_term", sa.String(), nullable=False, server_default="NET_15"),
        sa.Column("status", sa.String(), nullable=False, server_default="DRAFT"),
        sa.Column("completed_cycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_date", sa.Date(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_invoiceseries_company_id", "invoiceseries", ["company_id"])
    op.create_index("ix_invoiceseries_paypal_series_id", "invoiceseries", ["paypal_series_id"])
    op.add_column("invoice", sa.Column("series_id", sa.Integer(), sa.ForeignKey("invoiceseries.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice", "series_id")
    op.drop_index("ix_invoiceseries_paypal_series_id", table_name="invoiceseries")
    op.drop_index("ix_invoiceseries_company_id", table_name="invoiceseries")
    op.drop_table("invoiceseries")
