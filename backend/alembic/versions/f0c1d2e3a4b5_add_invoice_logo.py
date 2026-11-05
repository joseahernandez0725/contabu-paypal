"""invoiceprofile.logo + logo_key: el logo de las facturas (FASE 16, 4-nov)

PNG de hasta 250 × 90 (límite de PayPal); la API lo sirve en una URL pública con una clave al azar.

Revision ID: f0c1d2e3a4b5
Revises: e9b0c1d2f3a4
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0c1d2e3a4b5"
down_revision: Union[str, Sequence[str], None] = "e9b0c1d2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoiceprofile", sa.Column("logo", sa.LargeBinary(), nullable=True))
    op.add_column("invoiceprofile", sa.Column("logo_key", sa.String(), nullable=True))
    op.create_index("ix_invoiceprofile_logo_key", "invoiceprofile", ["logo_key"])


def downgrade() -> None:
    op.drop_index("ix_invoiceprofile_logo_key", table_name="invoiceprofile")
    op.drop_column("invoiceprofile", "logo_key")
    op.drop_column("invoiceprofile", "logo")
