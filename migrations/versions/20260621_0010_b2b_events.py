"""cart_items unavailable_reason and processed_events table"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260621_0010"
down_revision = "20260611_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cart_items",
        sa.Column("unavailable_reason", sa.String(length=30), nullable=True),
    )

    op.create_table(
        "processed_events",
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )
    op.create_index(
        "idx_processed_events_at",
        "processed_events",
        ["processed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_processed_events_at", table_name="processed_events")
    op.drop_table("processed_events")
    op.drop_column("cart_items", "unavailable_reason")
