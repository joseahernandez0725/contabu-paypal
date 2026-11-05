"""contractor + contractorpayment: pagos a contratistas con PayPal Payouts (FASE 16, 4-nov)

CASCADE con `company`; SET NULL con el contratista y con la fila del libro.

Revision ID: a1d2e3f4b5c6
Revises: f0c1d2e3a4b5
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1d2e3f4b5c6"
down_revision: Union[str, Sequence[str], None] = "f0c1d2e3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contractor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_contractor_company_id", "contractor", ["company_id"])
    op.create_table(
        "contractorpayment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractor.id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("memo", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("paypal_batch_id", sa.String(), nullable=True),
        sa.Column("paypal_item_id", sa.String(), nullable=True),
        sa.Column("paypal_tx_id", sa.String(), nullable=True),
        sa.Column("fee", sa.Numeric(14, 2), nullable=True),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transaction.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_contractorpayment_company_id", "contractorpayment", ["company_id"])
    op.create_index("ix_contractorpayment_paypal_tx_id", "contractorpayment", ["paypal_tx_id"])


def downgrade() -> None:
    op.drop_index("ix_contractorpayment_paypal_tx_id", table_name="contractorpayment")
    op.drop_index("ix_contractorpayment_company_id", table_name="contractorpayment")
    op.drop_table("contractorpayment")
    op.drop_index("ix_contractor_company_id", table_name="contractor")
    op.drop_table("contractor")
