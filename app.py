from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime
from functools import wraps
import click
import pytz
import math
from sqlalchemy import func

app = Flask(__name__)

# --- CONFIGURAÇÃO ---
app.config['SECRET_KEY'] = 'uma-chave-secreta-muito-segura-e-dificil-de-adivinhar'
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:alfa%402025@localhost:5432/alfa_task_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- CONSTANTES ---
PAGINATION_ITEMS = 10

# --- EXTENSÕES ---
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'home'
login_manager.login_message = "Por favor, faça o login para acessar esta página."
login_manager.login_message_category = "info"

# --- FUNÇÕES DE UTILIDADE E FILTROS JINJA ---
def format_datetime_local(utc_dt, fmt='%d/%m/%Y %H:%M'):
    if not utc_dt:
        return ''
    local_tz = pytz.timezone('America/Sao_Paulo')
    local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(local_tz)
    return local_dt.strftime(fmt)

app.jinja_env.filters['localdatetime'] = format_datetime_local

@app.context_processor
def utility_processor():
    def get_text_color_for_bg(hex_color):
        """
        Determina se o texto deve ser preto ou branco com base no brilho da cor de fundo.
        """
        try:
            hex_color = hex_color.lstrip('#')
            r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
            brightness = (r * 299 + g * 587 + b * 114) / 1000
            return '#000000' if brightness > 149 else '#FFFFFF'
        except:
            return '#FFFFFF' # Cor padrão em caso de erro
    return dict(get_text_color_for_bg=get_text_color_for_bg)

# --- DECORATOR PARA CONTROLE DE ACESSO POR PAPEL ---
def role_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                flash("Você não tem permissão para realizar esta ação.", "danger")
                return redirect(request.referrer or url_for('dashboard'))
            return fn(*args, **kwargs)
        return decorated_view
    return wrapper

# --- MODELOS ---
task_services_association = db.Table('task_services_association',
    db.Column('commission_task_id', db.Integer, db.ForeignKey('commission_tasks.id'), primary_key=True),
    db.Column('predefined_service_id', db.Integer, db.ForeignKey('predefined_services.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(80), nullable=False)
    demands_created = db.relationship('Demand', foreign_keys='Demand.requester_id', back_populates='requester', lazy='dynamic')
    demands_assigned = db.relationship('Demand', foreign_keys='Demand.assigned_to_id', back_populates='assigned_to', lazy='dynamic')
    commission_tasks = db.relationship('CommissionTask', back_populates='technician', lazy='dynamic')
    notes = db.relationship('Note', back_populates='user', lazy='dynamic', cascade="all, delete-orphan")
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class Demand(db.Model):
    __tablename__ = 'demands'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), nullable=False, default='Normal')
    status = db.Column(db.String(50), nullable=False, default='Não Visto')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    requester_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    requester = db.relationship('User', foreign_keys=[requester_id], back_populates='demands_created')
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id], back_populates='demands_assigned')
    @property
    def demand_number(self):
        return f"D{self.id:04d}"

class CommissionTask(db.Model):
    __tablename__ = 'commission_tasks'
    id = db.Column(db.Integer, primary_key=True)
    external_os_number = db.Column(db.String(50), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    service_type = db.Column(db.String(50), nullable=False, server_default='Serviço')
    technician_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    commission_value = db.Column(db.Numeric(10, 2), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='A Pagar')
    date_completed = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    technician = db.relationship('User', back_populates='commission_tasks')
    services = db.relationship('PredefinedService', secondary=task_services_association, backref='commission_tasks')
    custom_services = db.relationship('CustomServiceItem', back_populates='commission_task', lazy='dynamic', cascade="all, delete-orphan")
    
    @property
    def total_weight(self):
        if self.service_type == 'Serviço':
            predefined_weight = sum(service.weight for service in self.services)
            custom_weight = sum(service.weight for service in self.custom_services)
            return predefined_weight + custom_weight
        elif self.service_type == 'Orçamento':
            high_weight_items = ['Impressora G', 'PC Gamer', 'Notebook', 'Servidor', 'All in one', 'Nobreak']
            total_weight = 0
            if self.description:
                equipments_line = self.description.split('\n\n')[0].replace('Equipamentos Orçados: ', '').strip()
                equipments = [eq.strip() for eq in equipments_line.split(',')]
                for eq in equipments:
                    if eq in high_weight_items:
                        total_weight += 2
                    else:
                        total_weight += 1
            return total_weight
        elif self.service_type == 'Venda':
            if self.commission_value:
                return int(math.ceil(self.commission_value / 500))
        return 0

    @property
    def service_type_slug(self):
        return self.service_type.lower().replace('ç', 'c')

    @property
    def display_service_name(self):
        return self.service_type.upper()

    @property
    def display_description(self):
        if self.service_type == 'Serviço':
            services_names = [s.name for s in self.services]
            custom_services_names = [s.name for s in self.custom_services]
            return ', '.join(services_names + custom_services_names)
        elif self.service_type == 'Venda':
            return self.description.split('\n\n')[0].replace('Itens Vendidos: ', '').strip() if self.description else ''
        elif self.service_type == 'Orçamento':
            return self.description.split('\n\n')[0].replace('Equipamentos Orçados: ', '').strip() if self.description else ''
        else:
            return self.description

class PredefinedService(db.Model):
    __tablename__ = 'predefined_services'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    weight = db.Column(db.Integer, nullable=False)

class CustomServiceItem(db.Model):
    __tablename__ = 'custom_service_items'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    weight = db.Column(db.Integer, nullable=False)
    commission_task_id = db.Column(db.Integer, db.ForeignKey('commission_tasks.id'), nullable=False)
    commission_task = db.relationship('CommissionTask', back_populates='custom_services')

class DemandLog(db.Model):
    __tablename__ = 'demand_logs'
    id = db.Column(db.Integer, primary_key=True)
    demand_id = db.Column(db.Integer, db.ForeignKey('demands.id', ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user = db.relationship('User', backref='logs')
    demand = db.relationship('Demand', backref=db.backref('logs', cascade="all, delete-orphan"))

class Note(db.Model):
    __tablename__ = 'notes'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(20), nullable=False, default='#212529')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', back_populates='notes')

# --- ROTAS ---
@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('home_page'))
    return render_template('index.html')

@app.route('/home')
@login_required
def home_page():
    if current_user.role in ['Gerente', 'Supervisor']:
        # --- Dados Globais ---
        all_demand_statuses = ['Não Visto', 'Em Andamento', 'AG. ADM', 'AG. EVANDRO', 'AG. COMERCIAL', 'PARADO', 'CONCLUIDO']
        all_task_types = ['Serviço', 'Orçamento', 'Venda']

        demand_status_query = dict(db.session.query(Demand.status, func.count(Demand.status)).group_by(Demand.status).all())
        demand_status_counts = {status: demand_status_query.get(status, 0) for status in all_demand_statuses}
        total_demands = sum(demand_status_counts.values())

        task_type_query = dict(db.session.query(CommissionTask.service_type, func.count(CommissionTask.service_type)).group_by(CommissionTask.service_type).all())
        task_type_counts = {task_type: task_type_query.get(task_type, 0) for task_type in all_task_types}

        # --- Dados por Usuário (se selecionado) ---
        user_id = request.args.get('user_id', type=int)
        all_users = User.query.order_by(User.username).all()
        selected_user = None
        user_demand_counts = {status: 0 for status in all_demand_statuses}
        user_total_demands = 0
        user_task_counts = {task_type: 0 for task_type in all_task_types}
        user_total_difficulty = 0

        if user_id:
            selected_user = db.session.get(User, user_id)
            if selected_user:
                user_demand_query = dict(db.session.query(Demand.status, func.count(Demand.status))
                                          .filter(Demand.assigned_to_id == user_id)
                                          .group_by(Demand.status).all())
                user_demand_counts = {status: user_demand_query.get(status, 0) for status in all_demand_statuses}
                user_total_demands = sum(user_demand_counts.values())

                user_tasks = CommissionTask.query.filter_by(technician_id=user_id).all()
                user_task_query = {}
                for task in user_tasks:
                    user_task_query[task.service_type] = user_task_query.get(task.service_type, 0) + 1
                user_task_counts = {task_type: user_task_query.get(task_type, 0) for task_type in all_task_types}
                user_total_difficulty = sum(task.total_weight for task in user_tasks)

        return render_template('home_supervisor.html',
                               demand_status_counts=demand_status_counts,
                               total_demands=total_demands,
                               task_type_counts=task_type_counts,
                               all_users=all_users,
                               selected_user_id=user_id,
                               selected_user=selected_user,
                               user_demand_counts=user_demand_counts,
                               user_total_demands=user_total_demands,
                               user_task_counts=user_task_counts,
                               user_total_difficulty=user_total_difficulty)
    else:
        pending_demands = Demand.query.filter(
            Demand.assigned_to_id == current_user.id,
            Demand.status != 'CONCLUIDO'
        ).order_by(Demand.created_at.desc()).all()
        return render_template('home.html', pending_demands=pending_demands)


@app.route('/notes', methods=['GET', 'POST'])
@login_required
def notes():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        color = request.form.get('color', '#212529')
        if not title:
            flash('O título da anotação é obrigatório.', 'warning')
        else:
            new_note = Note(title=title, content=content, color=color, user_id=current_user.id)
            db.session.add(new_note)
            db.session.commit()
            flash('Anotação salva com sucesso!', 'success')
        return redirect(url_for('notes'))
    
    user_notes = Note.query.filter_by(user_id=current_user.id).order_by(Note.created_at.desc()).all()
    return render_template('notes.html', notes=user_notes)

@app.route('/notes/data/<int:note_id>')
@login_required
def get_note_data(note_id):
    note = db.get_or_404(Note, note_id)
    if note.user_id != current_user.id:
        return jsonify({'error': 'Acesso negado'}), 403
    return jsonify({
        'id': note.id,
        'title': note.title,
        'content': note.content,
        'color': note.color
    })

@app.route('/notes/<int:note_id>/edit', methods=['POST'])
@login_required
def edit_note(note_id):
    note = db.get_or_404(Note, note_id)
    if note.user_id != current_user.id:
        flash('Você não tem permissão para editar esta anotação.', 'danger')
        return redirect(url_for('notes'))
    
    title = request.form.get('title')
    content = request.form.get('content')
    color = request.form.get('color', '#212529')
    
    if not title:
        flash('O título da anotação é obrigatório.', 'warning')
    else:
        note.title = title
        note.content = content
        note.color = color
        db.session.commit()
        flash('Anotação atualizada com sucesso!', 'success')
    return redirect(url_for('notes'))

@app.route('/notes/<int:note_id>/delete', methods=['POST'])
@login_required
def delete_note(note_id):
    note = db.get_or_404(Note, note_id)
    if note.user_id != current_user.id:
        flash('Você não tem permissão para apagar esta anotação.', 'danger')
        return redirect(url_for('notes'))
    
    db.session.delete(note)
    db.session.commit()
    flash('Anotação apagada.', 'info')
    return redirect(url_for('notes'))

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        login_user(user)
        return redirect(url_for('home_page'))
    flash('Usuário ou senha inválidos.', 'danger')
    return redirect(url_for('home'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    user_filter = request.args.get('assigned_to_id', '')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = Demand.query.filter(Demand.status != 'CONCLUIDO')
    if current_user.role not in ['Gerente', 'Supervisor']:
        query = query.filter(Demand.assigned_to_id == current_user.id)
    if status_filter:
        query = query.filter(Demand.status == status_filter)
    if user_filter:
        if user_filter == 'unassigned':
            query = query.filter(Demand.assigned_to_id == None)
        else:
            query = query.filter(Demand.assigned_to_id == int(user_filter))
    if start_date:
        query = query.filter(Demand.created_at >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        query = query.filter(Demand.created_at <= datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59))

    demands_list = query.order_by(Demand.created_at.desc()).paginate(page=page, per_page=PAGINATION_ITEMS)
    all_users = User.query.order_by(User.username).all()
    active_statuses = sorted(['Não Visto', 'Em Andamento', 'AG. ADM', 'AG. EVANDRO', 'AG. COMERCIAL', 'PARADO'])
    return render_template('dashboard.html', demands=demands_list, users=all_users, statuses=active_statuses, status_filter=status_filter, user_filter=user_filter, start_date=start_date, end_date=end_date)

@app.route('/completed-demands')
@login_required
def completed_demands():
    page = request.args.get('page', 1, type=int)
    user_filter = request.args.get('assigned_to_id', '')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = Demand.query.filter_by(status='CONCLUIDO')
    if current_user.role not in ['Gerente', 'Supervisor']:
        query = query.filter(Demand.assigned_to_id == current_user.id)
    if user_filter:
        if user_filter == 'unassigned':
            query = query.filter(Demand.assigned_to_id == None)
        else:
            query = query.filter(Demand.assigned_to_id == int(user_filter))
    if start_date:
        query = query.filter(Demand.created_at >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        query = query.filter(Demand.created_at <= datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59))

    completed_list = query.order_by(Demand.created_at.desc()).paginate(page=page, per_page=PAGINATION_ITEMS)
    all_users = User.query.order_by(User.username).all()
    return render_template('completed_demands.html', demands=completed_list, users=all_users, user_filter=user_filter, start_date=start_date, end_date=end_date)

@app.route('/commission-tasks')
@login_required
def commission_tasks():
    page = request.args.get('page', 1, type=int)
    technician_filter = request.args.get('technician_id', '')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    service_type_filter = request.args.get('service_type', '')

    query = CommissionTask.query
    if current_user.role not in ['Gerente', 'Supervisor']:
        query = query.filter(CommissionTask.technician_id == current_user.id)
    if technician_filter:
        query = query.filter(CommissionTask.technician_id == int(technician_filter))
    if service_type_filter:
        query = query.filter(CommissionTask.service_type == service_type_filter)
    if start_date:
        query = query.filter(CommissionTask.date_completed >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        query = query.filter(CommissionTask.date_completed <= datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59))

    tasks = query.order_by(CommissionTask.date_completed.desc()).paginate(page=page, per_page=PAGINATION_ITEMS)
    all_technicians = User.query.order_by(User.username).all()
    service_types = ['Serviço', 'Orçamento', 'Venda']
    return render_template('commission_tasks.html', tasks=tasks, technicians=all_technicians, technician_filter=technician_filter, start_date=start_date, end_date=end_date, service_types=service_types, service_type_filter=service_type_filter)

@app.route('/commission-tasks/<int:task_id>')
@login_required
def commission_task_detail(task_id):
    task = db.get_or_404(CommissionTask, task_id)
    return render_template('commission_task_detail.html', task=task)

@app.route('/commission-tasks/create', methods=['GET', 'POST'])
@login_required
def create_commission_task():
    if request.method == 'POST':
        os_number = request.form.get('external_os_number')
        technician_id = request.form.get('technician_id')
        service_type = request.form.get('service_type')
        description = None
        commission_value = None
        
        if service_type == 'Serviço':
            selected_service_ids = request.form.getlist('predefined_services')
            if not selected_service_ids:
                flash('Pelo menos um serviço pré-definido é obrigatório para o tipo Serviço.', 'warning')
                return redirect(url_for('create_commission_task'))
            selected_services = PredefinedService.query.filter(PredefinedService.id.in_(selected_service_ids)).all()
            description = request.form.get('description')
        elif service_type == 'Orçamento':
            budget_equipments = request.form.getlist('budget_equipment')
            if not budget_equipments:
                flash('Pelo menos um equipamento é obrigatório para o tipo Orçamento.', 'warning')
                return redirect(url_for('create_commission_task'))
            notes = request.form.get('budget_notes')
            description = f"Equipamentos Orçados: {', '.join(budget_equipments)}\n\nNotas: {notes}"
        elif service_type == 'Venda':
            sale_items = [item for item in request.form.getlist('sale_items') if item]
            sale_value = request.form.get('commission_value')
            if not sale_items or not sale_value:
                flash('Pelo menos um item e o valor da venda são obrigatórios para o tipo Venda.', 'warning')
                return redirect(url_for('create_commission_task'))
            notes = request.form.get('sale_notes')
            description = f"Itens Vendidos: {', '.join(sale_items)}\n\nNotas: {notes}"
            commission_value = float(sale_value)

        if not all([os_number, technician_id, service_type]):
            flash('Nº OS, Responsável e Tipo de Lançamento são obrigatórios.', 'warning')
            return redirect(url_for('create_commission_task'))

        task = CommissionTask(external_os_number=os_number, description=description, technician_id=int(technician_id), service_type=service_type, commission_value=commission_value)
        if service_type == 'Serviço':
            task.services = selected_services
        db.session.add(task)
        db.session.commit()
        flash('Serviço de comissão lançado com sucesso!', 'success')
        return redirect(url_for('commission_tasks'))
    
    if current_user.role in ['Gerente', 'Supervisor']:
        assignable_users = User.query.order_by(User.username).all()
    else:
        assignable_users = [current_user]
    predefined_services = PredefinedService.query.order_by(PredefinedService.name).all()
    return render_template('new_commission_task.html', technicians=assignable_users, predefined_services=predefined_services)

@app.route('/demand/create', methods=['GET', 'POST'])
@login_required
@role_required('Gerente', 'Supervisor')
def create_demand():
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        priority = request.form.get('priority')
        if not all([title, description, priority]):
            flash('Todos os campos são obrigatórios.', 'warning')
            return render_template('new_demand.html')
        demand = Demand(title=title, description=description, priority=priority, requester_id=current_user.id)
        db.session.add(demand)
        db.session.commit()
        log = DemandLog(demand_id=demand.id, user_id=current_user.id, action="Demanda criada.")
        db.session.add(log)
        db.session.commit()
        flash(f'Demanda interna {demand.demand_number} registrada com sucesso!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('new_demand.html')
    
@app.route('/demand/<int:demand_id>')
@login_required
def demand_detail(demand_id):
    demand = db.get_or_404(Demand, demand_id)
    assignable_users = User.query.order_by(User.username).all()
    logs = DemandLog.query.filter_by(demand_id=demand_id).order_by(DemandLog.timestamp.desc()).all()
    return render_template('demand_detail.html', demand=demand, users=assignable_users, logs=logs, total_duration=None, durations={})

@app.route('/demand/<int:demand_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('Gerente', 'Supervisor')
def edit_demand(demand_id):
    demand = db.get_or_404(Demand, demand_id)
    if request.method == 'POST':
        demand.title = request.form['title']
        demand.description = request.form['description']
        demand.priority = request.form['priority']
        log = DemandLog(demand_id=demand.id, user_id=current_user.id, action="Demanda editada: Título, descrição ou prioridade foram alterados.")
        db.session.add(log)
        db.session.commit()
        flash('Demanda atualizada com sucesso!', 'success')
        return redirect(url_for('demand_detail', demand_id=demand.id))
    return render_template('edit_demand.html', demand=demand)

@app.route('/demand/<int:demand_id>/delete', methods=['POST'])
@login_required
@role_required('Gerente', 'Supervisor')
def delete_demand(demand_id):
    demand = db.get_or_404(Demand, demand_id)
    db.session.delete(demand)
    db.session.commit()
    flash(f'Demanda "{demand.title}" foi apagada com sucesso.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/demand/<int:demand_id>/status', methods=['POST'])
@login_required
def update_demand_status(demand_id):
    demand = db.get_or_404(Demand, demand_id)
    old_status = demand.status
    new_status = request.form.get('status')
    note = request.form.get('note')
    if old_status != new_status:
        demand.status = new_status
        action_log = f"Status alterado de '{old_status}' para '{new_status}'."
        if note:
            action_log += f" Nota: {note}"
        log = DemandLog(demand_id=demand.id, user_id=current_user.id, action=action_log)
        db.session.add(log)
        db.session.commit()
        flash('Status da demanda atualizado.', 'success')
    return redirect(url_for('demand_detail', demand_id=demand_id))

@app.route('/demand/<int:demand_id>/assign', methods=['POST'])
@login_required
@role_required('Gerente', 'Supervisor')
def assign_demand(demand_id):
    demand = db.get_or_404(Demand, demand_id)
    user_id = request.form.get('user_id')
    assignee = db.session.get(User, int(user_id)) if user_id else None
    if assignee:
        demand.assigned_to_id = assignee.id
        log_action = f"Demanda atribuída a {assignee.username.capitalize()}."
        log = DemandLog(demand_id=demand.id, user_id=current_user.id, action=log_action)
        db.session.add(log)
        db.session.commit()
        flash(f'Demanda atribuída a {assignee.username.capitalize()}.', 'success')
    else:
        flash('Usuário para atribuição não encontrado.', 'warning')
    return redirect(url_for('demand_detail', demand_id=demand_id))

@app.route('/log/<int:log_id>/delete', methods=['POST'])
@login_required
@role_required('Gerente', 'Supervisor')
def delete_log(log_id):
    log_to_delete = db.get_or_404(DemandLog, log_id)
    demand_id = log_to_delete.demand_id
    db.session.delete(log_to_delete)
    db.session.commit()
    flash('Entrada de histórico removida.', 'info')
    return redirect(url_for('demand_detail', demand_id=demand_id))
    
@app.route('/commission-tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('Gerente', 'Supervisor')
def edit_commission_task(task_id):
    task = db.get_or_404(CommissionTask, task_id)
    if request.method == 'POST':
        task.external_os_number = request.form.get('external_os_number')
        task.technician_id = int(request.form.get('technician_id'))
        task.service_type = request.form.get('service_type')
        task.description = request.form.get('description')
        task.services = PredefinedService.query.filter(PredefinedService.id.in_(request.form.getlist('predefined_services'))).all()
        
        for custom_service in task.custom_services:
            db.session.delete(custom_service)
        
        for name, weight in zip(request.form.getlist('custom_service_name'), request.form.getlist('custom_service_weight')):
            if name and weight:
                db.session.add(CustomServiceItem(name=name, weight=int(weight), commission_task_id=task.id))

        db.session.commit()
        flash('Serviço atualizado com sucesso!', 'success')
        return redirect(url_for('commission_tasks'))
        
    technicians = User.query.order_by(User.username).all()
    predefined_services = PredefinedService.query.order_by(PredefinedService.name).all()
    return render_template('edit_commission_task.html', task=task, technicians=technicians, predefined_services=predefined_services)

@app.route('/commission-tasks/<int:task_id>/delete', methods=['POST'])
@login_required
@role_required('Gerente', 'Supervisor')
def delete_commission_task(task_id):
    task = db.get_or_404(CommissionTask, task_id)
    db.session.delete(task)
    db.session.commit()
    flash('Serviço de comissão excluído com sucesso.', 'success')
    return redirect(url_for('commission_tasks'))

# --- COMANDOS DE CLI ---
@app.cli.command("seed-services")
def seed_services_command():
    services = [
        {'name': 'Limpeza interna notebook', 'weight': 4}, {'name': 'Limpeza interna PC', 'weight': 2},
        {'name': 'Formatação', 'weight': 3}, {'name': 'Troca de teclado notebook', 'weight': 4},
        {'name': 'Troca de SSD', 'weight': 2}, {'name': 'Troca de tela notebook', 'weight': 4},
        {'name': 'Troca de bateria de nobreak', 'weight': 3}, {'name': 'Acesso remoto', 'weight': 1},
        {'name': 'Limpeza via software impressora', 'weight': 2}, {'name': 'Pressurização impressora', 'weight': 3},
        {'name': 'Troca de esponja impressora', 'weight': 2}, {'name': 'Reparo na placa', 'weight': 5},
        {'name': 'Banho químico', 'weight': 5}, {'name': 'SERVIÇO SIMPLES', 'weight': 1},
        {'name': 'ATIVAÇÃO PROGRAMA', 'weight': 1}, {'name': 'INSTALAÇÃO PROGRAMA', 'weight': 1},
        {'name': 'INSTALAÇÃO PEÇA SIMPLES', 'weight': 1}
    ]
    for service_data in services:
        if not PredefinedService.query.filter_by(name=service_data['name']).first():
            db.session.add(PredefinedService(name=service_data['name'], weight=service_data['weight']))
            print(f"Adicionado: {service_data['name']}")
    db.session.commit()
    print("População de serviços concluída.")

@app.cli.command("create-user")
@click.argument("username")
@click.argument("password")
@click.argument("role")
def create_user_command(username, password, role):
    if User.query.filter_by(username=username).first():
        print(f"Erro: Usuário '{username}' já existe.")
        return
    new_user = User(username=username, role=role)
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    print(f"Usuário '{username}' com o papel '{role}' criado com sucesso.")

if __name__ == '__main__':
    app.run(debug=True)