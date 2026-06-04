from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260601_0004"
down_revision = "20260531_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "banners",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=False),
        sa.Column("link", sa.String(length=500), nullable=False),
        sa.Column("placement", sa.String(length=50), nullable=False, server_default="home"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_banners_home_active", "banners", ["placement", "is_active", "priority", "start_at", "end_at"])
    op.create_table(
        "banner_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("banner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event", sa.String(length=20), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("event IN ('impression', 'click')", name="ck_banner_events_event"),
        sa.ForeignKeyConstraint(["banner_id"], ["banners.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_banner_events_banner_time", "banner_events", ["banner_id", "timestamp"])


def downgrade() -> None:
    op.drop_index("idx_banner_events_banner_time", table_name="banner_events")
    op.drop_table("banner_events")
    op.drop_index("idx_banners_home_active", table_name="banners")
    op.drop_table("banners")
