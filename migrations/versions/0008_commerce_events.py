"""建立去重页面事件表。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME
revision='0008_commerce_events'
down_revision='0007_checkout'
branch_labels=depends_on=None


def upgrade():
    op.create_table('commerce_events',sa.Column('id',sa.CHAR(36),primary_key=True),sa.Column('principal_key',sa.CHAR(64),nullable=False),
        sa.Column('session_id',sa.CHAR(36),sa.ForeignKey('commerce_sessions.id'),nullable=False),sa.Column('request_id',sa.String(64),nullable=False),
        sa.Column('request_hash',sa.CHAR(64),nullable=False),sa.Column('kind',sa.String(32),nullable=False),sa.Column('payload_json',sa.JSON,nullable=False),
        sa.Column('created_at',DATETIME(fsp=6),nullable=False),mysql_engine='InnoDB',mysql_charset='utf8mb4')
    op.create_index('ix_commerce_events_principal_session','commerce_events',['principal_key','session_id'])


def downgrade():
    op.drop_table('commerce_events')
