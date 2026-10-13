"""Stamp the audited legacy catalog schema without applying DDL."""

from typing import Sequence, Union


revision: str = "20261013_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Represent the existing audited schema without changing it."""


def downgrade() -> None:
    """A stamped legacy baseline has no DDL to reverse."""
