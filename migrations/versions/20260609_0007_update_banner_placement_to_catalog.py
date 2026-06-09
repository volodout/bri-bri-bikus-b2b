"""update banner placement from home to catalog"""

from alembic import op

revision = "20260609_0007"
down_revision = "20260609_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE banners SET placement = 'catalog' WHERE placement = 'home'")


def downgrade() -> None:
    op.execute("UPDATE banners SET placement = 'home' WHERE placement = 'catalog'")
