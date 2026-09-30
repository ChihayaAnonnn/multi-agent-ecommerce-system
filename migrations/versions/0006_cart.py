"""建立主体唯一购物车与单会话唯一待决动作。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME
revision='0006_cart'
down_revision='0005_commerce_sessions'
branch_labels=depends_on=None
KW={'mysql_engine':'InnoDB','mysql_charset':'utf8mb4'}


def timestamps():
    return [sa.Column(n,DATETIME(fsp=6),nullable=False,server_default=sa.text('CURRENT_TIMESTAMP(6)')) for n in ['created_at','updated_at']]


def upgrade():
    op.create_table('carts',sa.Column('id',sa.CHAR(36),primary_key=True),sa.Column('principal_key',sa.CHAR(64),nullable=False),
        sa.Column('version',sa.Integer,nullable=False),sa.UniqueConstraint('principal_key'),*timestamps(),**KW)
    op.create_table('cart_lines',sa.Column('id',sa.CHAR(36),primary_key=True),sa.Column('cart_id',sa.CHAR(36),sa.ForeignKey('carts.id'),nullable=False),
        sa.Column('sku_id',sa.CHAR(36),sa.ForeignKey('skus.id'),nullable=False),sa.Column('quantity',sa.Integer,nullable=False),
        sa.Column('product_name_snapshot',sa.String(255),nullable=False),sa.Column('sku_options_snapshot',sa.JSON,nullable=False),sa.Column('image_url_snapshot',sa.String(512)),
        sa.Column('last_seen_unit_amount_minor',sa.BigInteger),sa.Column('last_seen_price_version',sa.Integer),sa.UniqueConstraint('cart_id','sku_id'),
        sa.CheckConstraint('quantity > 0',name='ck_cart_lines_quantity'),*timestamps(),**KW)
    op.create_table('pending_actions',sa.Column('id',sa.CHAR(36),primary_key=True),
        sa.Column('session_id',sa.CHAR(36),sa.ForeignKey('commerce_sessions.id'),nullable=False),sa.Column('source_turn_id',sa.String(64)),
        sa.Column('principal_key',sa.CHAR(64),nullable=False),sa.Column('cart_id',sa.CHAR(36),sa.ForeignKey('carts.id'),nullable=False),
        sa.Column('kind',sa.String(32),nullable=False),sa.Column('payload_json',sa.JSON,nullable=False),sa.Column('proposal_hash',sa.CHAR(64),nullable=False),
        sa.Column('expected_cart_version',sa.Integer,nullable=False),sa.Column('quote_snapshot_json',sa.JSON,nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('expires_at',DATETIME(fsp=6),nullable=False),sa.Column('confirmation_key',sa.String(64)),sa.Column('confirmation_request_hash',sa.CHAR(64)),sa.Column('result_json',sa.JSON),
        sa.Column('active_session_id',sa.CHAR(36),sa.Computed("CASE WHEN status='pending' THEN session_id ELSE NULL END",persisted=True)),
        sa.UniqueConstraint('session_id','source_turn_id'),sa.UniqueConstraint('principal_key','confirmation_key'),sa.UniqueConstraint('active_session_id'),
        sa.CheckConstraint("kind IN ('add','set_quantity','remove','checkout_handoff')",name='ck_pending_actions_kind'),
        sa.CheckConstraint("status IN ('pending','declined','consumed','invalidated','expired')",name='ck_pending_actions_status'),*timestamps(),**KW)


def downgrade():
    for name in ['pending_actions','cart_lines','carts']:
        op.drop_table(name)
