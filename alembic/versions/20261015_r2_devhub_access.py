"""Add durable R2 DevHub access replay evidence and route review state."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261015_r2_devhub_access"
down_revision: Union[str, Sequence[str], None] = "20261014_r3_deployment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_ROUTE_CHECK = "ruta_organigrama REGEXP '^[^/]+(/[^/]+){0,4}$'"
R2_ROUTE_CHECK = "ruta_organigrama REGEXP '^/?[^/]+(/[^/]+){0,4}$'"


def upgrade() -> None:
    op.drop_constraint("ck_acceso_devhub_2", "acceso_devhub", type_="check")
    op.create_check_constraint("ck_acceso_devhub_2", "acceso_devhub", R2_ROUTE_CHECK)
    op.add_column(
        "acceso_devhub",
        sa.Column(
            "ruta_requiere_revision",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "acceso_devhub_evento",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "registro_integracion_id",
            sa.BigInteger(),
            sa.ForeignKey("registro_integracion.id"),
            nullable=False,
        ),
        sa.Column("hash_semantico", sa.String(length=64), nullable=False),
        sa.Column("evidencia_canonica", sa.JSON(), nullable=False),
        sa.Column("creado_en", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "registro_integracion_id",
            name="uq_acceso_devhub_evento_registro_integracion",
        ),
    )
    op.create_index(
        "ix_acceso_devhub_evento_hash_semantico",
        "acceso_devhub_evento",
        ["hash_semantico"],
    )


def downgrade() -> None:
    op.drop_constraint("ck_acceso_devhub_2", "acceso_devhub", type_="check")
    op.execute(
        "UPDATE acceso_devhub SET ruta_organigrama = SUBSTRING(ruta_organigrama, 2) "
        "WHERE ruta_organigrama LIKE '/%'"
    )
    op.create_check_constraint("ck_acceso_devhub_2", "acceso_devhub", LEGACY_ROUTE_CHECK)
    op.drop_index("ix_acceso_devhub_evento_hash_semantico", table_name="acceso_devhub_evento")
    op.drop_table("acceso_devhub_evento")
    op.drop_column("acceso_devhub", "ruta_requiere_revision")
