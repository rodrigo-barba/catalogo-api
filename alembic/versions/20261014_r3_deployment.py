"""Add durable R3 deployment replay-evidence table."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261014_r3_deployment"
down_revision: Union[str, Sequence[str], None] = "20261013_r1_setup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "despliegue_evento",
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
        sa.UniqueConstraint("registro_integracion_id", name="uq_despliegue_evento_registro_integracion"),
    )
    op.create_index("ix_despliegue_evento_hash_semantico", "despliegue_evento", ["hash_semantico"])


def downgrade() -> None:
    op.drop_index("ix_despliegue_evento_hash_semantico", table_name="despliegue_evento")
    op.drop_table("despliegue_evento")