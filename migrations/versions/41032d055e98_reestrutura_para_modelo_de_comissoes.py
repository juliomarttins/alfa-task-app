"""Reestrutura para modelo de comissoes

Revision ID: 41032d055e98
Revises: 
Create Date: 2025-08-16 17:42:26.756089

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '41032d055e98'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### Início dos comandos corrigidos manualmente ###

    # PASSO 1: Criar a nova tabela para as tarefas de comissão.
    op.create_table('commission_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('external_os_number', sa.String(length=50), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('technician_id', sa.Integer(), nullable=False),
    sa.Column('commission_value', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('date_completed', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['technician_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_commission_tasks_external_os_number'), 'commission_tasks', ['external_os_number'], unique=False)

    # PASSO 2: Remover as colunas e a "viga" (foreign key) da tabela 'demands' que dependiam da tabela antiga.
    # Usando batch_alter_table para compatibilidade com SQLite, embora seja explícito.
    with op.batch_alter_table('demands', schema=None) as batch_op:
        batch_op.drop_constraint('demands_service_order_id_fkey', type_='foreignkey')
        batch_op.drop_column('internal_notes')
        batch_op.drop_column('service_order_id')
        batch_op.drop_column('parts_link')

    # PASSO 3: Agora que nada mais depende da tabela 'service_orders', podemos removê-la com segurança.
    op.drop_table('service_orders')

    # A TABELA 'demand_logs' NÃO É REMOVIDA, POIS AINDA É NECESSÁRIA.

    # ### Fim dos comandos corrigidos ###


def downgrade():
    # ### Início dos comandos de downgrade corrigidos ###
    # A ordem aqui é o inverso exato da função upgrade()

    # PASSO 1: Recriar a tabela 'service_orders' que foi apagada.
    op.create_table('service_orders',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('os_number', sa.VARCHAR(length=50), autoincrement=False, nullable=False),
    sa.Column('client_name', sa.VARCHAR(length=120), autoincrement=False, nullable=False),
    sa.Column('equipment', sa.VARCHAR(length=200), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=50), autoincrement=False, nullable=False),
    sa.Column('os_type', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('initial_notes', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='service_orders_pkey'),
    sa.UniqueConstraint('os_number', name='service_orders_os_number_key')
    )

    # PASSO 2: Readicionar as colunas e a "viga" (foreign key) na tabela 'demands'.
    with op.batch_alter_table('demands', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parts_link', sa.VARCHAR(length=500), autoincrement=False, nullable=True))
        batch_op.add_column(sa.Column('service_order_id', sa.INTEGER(), autoincrement=False, nullable=True))
        batch_op.add_column(sa.Column('internal_notes', sa.TEXT(), autoincrement=False, nullable=True))
        batch_op.create_foreign_key('demands_service_order_id_fkey', 'service_orders', ['service_order_id'], ['id'])

    # PASSO 3: Remover a tabela 'commission_tasks' que foi criada.
    op.drop_index(op.f('ix_commission_tasks_external_os_number'), table_name='commission_tasks')
    op.drop_table('commission_tasks')

    # ### Fim dos comandos de downgrade corrigidos ###