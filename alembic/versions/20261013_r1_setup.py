"""Add durable R1 setup evidence and pending synchronization outbox."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261013_r1_setup"
down_revision: Union[str, Sequence[str], None] = "20261013_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proyecto",
        sa.Column(
            "ruta_requiere_revision",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_unique_constraint(
        "uq_registro_integracion_evento_origen_global",
        "registro_integracion",
        ["id_evento_origen"],
    )
    op.create_table(
        "setup_evento",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "registro_integracion_id",
            sa.BigInteger(),
            sa.ForeignKey("registro_integracion.id"),
            nullable=False,
        ),
        sa.Column("hash_semantico", sa.String(length=64), nullable=False),
        sa.Column("evidencia_canonica", sa.JSON(), nullable=False),
        sa.Column("actor_cuil", sa.String(length=11), nullable=False),
        sa.Column("creado_en", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("registro_integracion_id", name="uq_setup_evento_registro_integracion"),
    )
    op.create_index("ix_setup_evento_hash_semantico", "setup_evento", ["hash_semantico"])
    op.create_table(
        "sincronizacion_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "registro_integracion_id",
            sa.BigInteger(),
            sa.ForeignKey("registro_integracion.id"),
            nullable=False,
        ),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("creado_en", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("registro_integracion_id", name="uq_sincronizacion_outbox_registro_integracion"),
    )


def downgrade() -> None:
    op.drop_table("sincronizacion_outbox")
    op.drop_index("ix_setup_evento_hash_semantico", table_name="setup_evento")
    op.drop_table("setup_evento")
    op.drop_constraint(
        "uq_registro_integracion_evento_origen_global",
        "registro_integracion",
        type_="unique",
    )
    op.drop_column("proyecto", "ruta_requiere_revision")
