"""建立购物会话、已见来源及轮次。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME
revision = '0005_commerce_sessions'
down_revision = '0004_identity'
branch_labels = depends_on = None
KW = {'mysql_engine':'InnoDB', 'mysql_charset':'utf8mb4'}


def upgrade():
    op.create_table('commerce_sessions',
        sa.Column('id', sa.CHAR(36), primary_key=True),
        sa.Column('principal_key', sa.CHAR(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('expires_at', DATETIME(fsp=6), nullable=False),
        sa.Column('state_json', sa.JSON, nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('active_turn_id', sa.String(64)),
        sa.Column('turn_lease_token', sa.CHAR(36)),
        sa.Column('turn_lease_expires_at', DATETIME(fsp=6)),
        sa.Column('created_at', DATETIME(fsp=6), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP(6)')),
        sa.Column('updated_at', DATETIME(fsp=6), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP(6)')), **KW)
    op.create_index('ix_commerce_sessions_principal_expiry', 'commerce_sessions', ['principal_key','expires_at'])
    op.create_table('commerce_turns',
        sa.Column('session_id', sa.CHAR(36), sa.ForeignKey('commerce_sessions.id'), primary_key=True),
        sa.Column('turn_id', sa.String(64), primary_key=True),
        sa.Column('request_hash', sa.CHAR(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('response_json', sa.JSON),
        sa.Column('created_at', DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint("status IN ('processing','completed')", name='ck_commerce_turns_status'), **KW)
    for name, field, target in [('session_seen_products','product_id','products.id'), ('session_seen_skus','sku_id','skus.id')]:
        op.create_table(name,
            sa.Column('session_id', sa.CHAR(36), sa.ForeignKey('commerce_sessions.id'), primary_key=True),
            sa.Column(field, sa.CHAR(36), sa.ForeignKey(target), primary_key=True),
            sa.Column('first_seen_at', DATETIME(fsp=6), nullable=False), **KW)


def downgrade():
    for name in ['session_seen_skus', 'session_seen_products', 'commerce_turns', 'commerce_sessions']:
        op.drop_table(name)
