"""paypalconnection: las credenciales propias de una conexión + su webhook (FASE 16 Paso 7)

`client_secret_enc` va CIFRADO (Fernet, la misma clave de los bancos). Vacíos en una conexión `first_party`.

Revision ID: e1a2b3c4d5f7
Revises: d0e1f2a3b4c6
Create Date: 2026-11-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1a2b3c4d5f7"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("paypalconnection", sa.Column("client_id", sa.String(), nullable=True))
    op.add_column("paypalconnection", sa.Column("client_secret_enc", sa.String(), nullable=True))
    op.add_column("paypalconnection", sa.Column("webhook_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("paypalconnection", "webhook_id")
    op.drop_column("paypalconnection", "client_secret_enc")
    op.drop_column("paypalconnection", "client_id")
