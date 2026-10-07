"""Model registry tables.

Revision ID: 0006_model_registry
Revises: 0005_feature_store_indexes
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006_model_registry"
down_revision: str | None = "0005_feature_store_indexes"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "sg_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("algorithm", sa.String(length=64), nullable=False),
        sa.Column("feature_version", sa.String(length=32), nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("artifact_path", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sg_models")),
        sa.UniqueConstraint("name", name=op.f("uq_sg_models_name")),
    )
    op.create_index(op.f("ix_sg_models_algorithm"), "sg_models", ["algorithm"], unique=False)
    op.create_table(
        "sg_model_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("train_end", sa.Date(), nullable=False),
        sa.Column("n_train", sa.Integer(), nullable=False),
        sa.Column("n_features", sa.Integer(), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["sg_models.id"],
            name=op.f("fk_sg_model_runs_model_id_sg_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sg_model_runs")),
    )
    op.create_index(
        op.f("ix_sg_model_runs_model_id"), "sg_model_runs", ["model_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sg_model_runs_model_id"), table_name="sg_model_runs")
    op.drop_table("sg_model_runs")
    op.drop_index(op.f("ix_sg_models_algorithm"), table_name="sg_models")
    op.drop_table("sg_models")
