"""rename subscription event values to BACK_IN_STOCK / PRICE_DROP"""

from alembic import op

revision = "20260609_0006"
down_revision = "20260601_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE product_subscriptions "
        "SET notify_on = array_replace(notify_on, 'IN_STOCK', 'BACK_IN_STOCK')"
    )
    op.execute(
        "UPDATE product_subscriptions "
        "SET notify_on = array_replace(notify_on, 'PRICE_DOWN', 'PRICE_DROP')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE product_subscriptions "
        "SET notify_on = array_replace(notify_on, 'BACK_IN_STOCK', 'IN_STOCK')"
    )
    op.execute(
        "UPDATE product_subscriptions "
        "SET notify_on = array_replace(notify_on, 'PRICE_DROP', 'PRICE_DOWN')"
    )
