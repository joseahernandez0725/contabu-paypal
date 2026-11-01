"""add paypaltransfermatch (FASE 16 Paso 4 — el retiro de PayPal emparejado con su depósito en el banco)

Tabla nueva. CASCADE con `company` y con la transacción del retiro; SET NULL con la del depósito.

Revision ID: c9d0e1f2a3b5
Revises: b8c9d0e1f2a4
Create Date: 2026-11-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b5"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paypaltransfermatch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("withdrawal_tx_id", sa.Integer(), sa.ForeignKey("transaction.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bank_tx_id", sa.Integer(), sa.ForeignKey("transaction.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("withdrawal_tx_id", name="uq_paypaltransfermatch_withdrawal"),
    )
    op.create_index("ix_paypaltransfermatch_company_id", "paypaltransfermatch", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_paypaltransfermatch_company_id", table_name="paypaltransfermatch")
    op.drop_table("paypaltransfermatch")
