"""addresses table and order address snapshot"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260611_0009"
down_revision = "20260609_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country", sa.String(length=100), nullable=False),
        sa.Column("region", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=200), nullable=False),
        sa.Column("street", sa.String(length=200), nullable=False),
        sa.Column("building", sa.String(length=50), nullable=False),
        sa.Column("apartment", sa.String(length=50), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("recipient_name", sa.String(length=200), nullable=True),
        sa.Column("recipient_phone", sa.String(length=20), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("comment", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_addresses_user", "addresses", ["user_id"])

    op.add_column("orders", sa.Column("address", postgresql.JSONB(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("payment_method_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("orders", sa.Column("comment", sa.String(length=1000), nullable=True))
    op.drop_column("orders", "delivery_address")


def downgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("delivery_address", sa.String(length=1000), nullable=True),
    )
    op.drop_column("orders", "comment")
    op.drop_column("orders", "payment_method_id")
    op.drop_column("orders", "address")

    op.drop_index("idx_addresses_user", table_name="addresses")
    op.drop_table("addresses")
