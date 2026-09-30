"""建立不可变交接快照及恢复状态。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME
revision='0007_checkout'
down_revision='0006_cart'
branch_labels=depends_on=None


def upgrade():
    op.create_table('checkout_handoffs',sa.Column('id',sa.CHAR(36),primary_key=True),
        sa.Column('principal_key',sa.CHAR(64),nullable=False),sa.Column('cart_id',sa.CHAR(36),sa.ForeignKey('carts.id'),nullable=False),
        sa.Column('cart_version',sa.Integer,nullable=False),sa.Column('action_id',sa.CHAR(36),sa.ForeignKey('pending_actions.id'),nullable=False),
        sa.Column('snapshot_json',sa.JSON,nullable=False),sa.Column('snapshot_hash',sa.CHAR(64),nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('destination_kind',sa.String(16)),sa.Column('destination_ref',sa.String(512)),sa.Column('destination_demo',sa.Boolean,nullable=False),
        sa.Column('expires_at',DATETIME(fsp=6),nullable=False),
        sa.Column('created_at',DATETIME(fsp=6),nullable=False,server_default=sa.text('CURRENT_TIMESTAMP(6)')),
        sa.Column('updated_at',DATETIME(fsp=6),nullable=False,server_default=sa.text('CURRENT_TIMESTAMP(6)')),
        sa.UniqueConstraint('action_id'),sa.CheckConstraint("status IN ('preparing','ready','failed','expired')",name='ck_checkout_handoffs_status'),mysql_engine='InnoDB',mysql_charset='utf8mb4')


def downgrade():
    op.drop_table('checkout_handoffs')
