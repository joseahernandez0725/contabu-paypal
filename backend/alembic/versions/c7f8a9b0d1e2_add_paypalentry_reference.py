"""paypalentry.reference_id: de una devolución de PayPal, el cobro original (FASE 16, 3-nov)

Con esto Cobros sabe si la devolución de una factura ya está en el libro. Nula en lo anterior.

Revision ID: c7f8a9b0d1e2
Revises: b6e7f8a9c0d1
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7f8a9b0d1e2"
down_revision: Union[str, Sequence[str], None] = "b6e7f8a9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("paypalentry", sa.Column("reference_id", sa.String(), nullable=True))
    op.create_index("ix_paypalentry_reference_id", "paypalentry", ["reference_id"])


def downgrade() -> None:
    op.drop_index("ix_paypalentry_reference_id", table_name="paypalentry")
    op.drop_column("paypalentry", "reference_id")
