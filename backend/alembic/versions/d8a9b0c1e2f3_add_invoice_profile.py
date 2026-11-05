"""invoiceprofile: los datos del negocio en sus facturas (FASE 16, 4-nov)

Uno por libro (único). CASCADE con `company`.

Revision ID: d8a9b0c1e2f3
Revises: c7f8a9b0d1e2
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8a9b0c1e2f3"
down_revision: Union[str, Sequence[str], None] = "c7f8a9b0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoiceprofile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address_line", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("postal_code", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("website", sa.String(), nullable=True),
        sa.Column("tax_id", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("terms", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", name="uq_invoiceprofile_company_id"),
    )
    op.create_index("ix_invoiceprofile_company_id", "invoiceprofile", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_invoiceprofile_company_id", table_name="invoiceprofile")
    op.drop_table("invoiceprofile")
