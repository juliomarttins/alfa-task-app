## app.py
```python
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
```


## migrations\env.py
```python
import logging
from logging.config import fileConfig

from flask import current_app

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    connectable = get_engine()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

```


## migrations\versions\2960731a0325_adiciona_servicos_personalizados_e_.py
```python
"""Adiciona servicos personalizados e acoes de edicao

Revision ID: 2960731a0325
Revises: a9fac98d5ccd
Create Date: 2025-08-16 20:24:59.982348

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2960731a0325'
down_revision = 'a9fac98d5ccd'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('custom_service_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.Column('commission_task_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['commission_task_id'], ['commission_tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('custom_service_items')
    # ### end Alembic commands ###

```


## migrations\versions\30576132a560_cria_tabela_de_notas_com_titulo_e_cor.py
```python
"""Cria tabela de notas com titulo e cor

Revision ID: 30576132a560
Revises: 2960731a0325
Create Date: 2025-08-17 00:04:44.271597

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '30576132a560'
down_revision = '2960731a0325'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('color', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('notes')
    # ### end Alembic commands ###

```


## migrations\versions\41032d055e98_reestrutura_para_modelo_de_comissoes.py
```python
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
```


## migrations\versions\68efec225e2e_adiciona_service_type_a_commission_tasks.py
```python
"""Adiciona service_type a commission_tasks

Revision ID: 68efec225e2e
Revises: 41032d055e98
Create Date: 2025-08-16 19:35:32.724551

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '68efec225e2e'
down_revision = '41032d055e98'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('commission_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('service_type', sa.String(length=50), server_default='Serviço', nullable=False))

    with op.batch_alter_table('demand_logs', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('demand_logs_demand_id_fkey'), type_='foreignkey')
        batch_op.create_foreign_key(None, 'demands', ['demand_id'], ['id'], ondelete='CASCADE')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('demand_logs', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.create_foreign_key(batch_op.f('demand_logs_demand_id_fkey'), 'demands', ['demand_id'], ['id'])

    with op.batch_alter_table('commission_tasks', schema=None) as batch_op:
        batch_op.drop_column('service_type')

    # ### end Alembic commands ###

```


## migrations\versions\a9fac98d5ccd_adiciona_servicos_pre_definidos_com_.py
```python
"""Adiciona servicos pre-definidos com pesos

Revision ID: a9fac98d5ccd
Revises: 68efec225e2e
Create Date: 2025-08-16 20:01:23.775861

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9fac98d5ccd'
down_revision = '68efec225e2e'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('predefined_services',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('task_services_association',
    sa.Column('commission_task_id', sa.Integer(), nullable=False),
    sa.Column('predefined_service_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['commission_task_id'], ['commission_tasks.id'], ),
    sa.ForeignKeyConstraint(['predefined_service_id'], ['predefined_services.id'], ),
    sa.PrimaryKeyConstraint('commission_task_id', 'predefined_service_id')
    )
    with op.batch_alter_table('commission_tasks', schema=None) as batch_op:
        batch_op.alter_column('description',
               existing_type=sa.TEXT(),
               nullable=True)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('commission_tasks', schema=None) as batch_op:
        batch_op.alter_column('description',
               existing_type=sa.TEXT(),
               nullable=False)

    op.drop_table('task_services_association')
    op.drop_table('predefined_services')
    # ### end Alembic commands ###

```


## migrationsd\env.py
```python
import logging
from logging.config import fileConfig

from flask import current_app

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    connectable = get_engine()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

```


## migrationsd\versions\392dcbc9ed5a_cria_a_tabela_de_demandas.py
```python
"""Cria a tabela de demandas

Revision ID: 392dcbc9ed5a
Revises: a5d5f60754c4
Create Date: 2025-08-15 16:42:04.545593

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '392dcbc9ed5a'
down_revision = 'a5d5f60754c4'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('demands',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('priority', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('requester_id', sa.Integer(), nullable=False),
    sa.Column('assigned_to_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['assigned_to_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['requester_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('demands')
    # ### end Alembic commands ###

```


## migrationsd\versions\a5d5f60754c4_cria_a_tabela_de_usuários.py
```python
"""Cria a tabela de usuários

Revision ID: a5d5f60754c4
Revises: 
Create Date: 2025-08-15 16:28:15.215384

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a5d5f60754c4'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=80), nullable=False),
    sa.Column('password_hash', sa.String(length=256), nullable=False),
    sa.Column('role', sa.String(length=80), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('users')
    # ### end Alembic commands ###

```


## migrationsd\versions\d2ae9b3a08c7_adiciona_formularios_de_criacao_de_os_e_.py
```python
"""Adiciona formularios de criacao de OS e Demanda

Revision ID: d2ae9b3a08c7
Revises: d6153c6d18df
Create Date: 2025-08-16 17:06:58.940857

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2ae9b3a08c7'
down_revision = 'd6153c6d18df'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('service_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('os_number', sa.String(length=50), nullable=False),
    sa.Column('client_name', sa.String(length=120), nullable=False),
    sa.Column('equipment', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('os_type', sa.String(length=20), nullable=False),
    sa.Column('initial_notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('os_number')
    )
    with op.batch_alter_table('demands', schema=None) as batch_op:
        batch_op.add_column(sa.Column('service_order_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('parts_link', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('internal_notes', sa.Text(), nullable=True))
        batch_op.alter_column('status',
               existing_type=sa.VARCHAR(length=30),
               type_=sa.String(length=50),
               existing_nullable=False)
        batch_op.create_foreign_key(None, 'service_orders', ['service_order_id'], ['id'])

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('demands', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.alter_column('status',
               existing_type=sa.String(length=50),
               type_=sa.VARCHAR(length=30),
               existing_nullable=False)
        batch_op.drop_column('internal_notes')
        batch_op.drop_column('parts_link')
        batch_op.drop_column('service_order_id')

    op.drop_table('service_orders')
    # ### end Alembic commands ###

```


## migrationsd\versions\d6153c6d18df_cria_a_tabela_de_logs_de_demanda.py
```python
"""Cria a tabela de logs de demanda

Revision ID: d6153c6d18df
Revises: 392dcbc9ed5a
Create Date: 2025-08-15 17:13:59.515250

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd6153c6d18df'
down_revision = '392dcbc9ed5a'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('demand_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('demand_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['demand_id'], ['demands.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('demand_logs')
    # ### end Alembic commands ###

```


## static\css\login.css
```css
.login-container {
    height: 100vh;
}

.login-box {
    background-color: var(--cor-card);
    border: 1px solid var(--cor-borda);
    border-radius: 8px;
    padding: 40px;
    box-shadow: 0 4px 20px var(--sombra-forte);
    width: 100%;
    max-width: 400px;
    text-align: center;
}

.login-title {
    font-size: 2.5em;
    margin-bottom: 5px;
    color: var(--cor-principal);
    font-weight: 700;
}

.login-subtitle {
    font-size: 1em;
    color: #aaa;
    margin-bottom: 25px;
}

.form-label {
    margin-bottom: 8px;
    color: var(--cor-texto);
    font-size: 0.9em;
}

.form-control {
    background-color: var(--cor-fundo);
    color: var(--cor-texto);
    font-size: 1em;
    padding: 12px;
}

.form-control:focus {
    background-color: var(--cor-fundo);
    color: var(--cor-texto);
    border-color: var(--cor-principal);
    box-shadow: 0 0 0 0.25rem rgba(136, 0, 240, 0.25); /* Sombra de foco atualizada */
}

.login-button {
    padding: 12px;
    font-size: 1.1em;
    font-weight: 600;
    
    /* Cores do botão atualizadas para o tom violeta */
    --bs-btn-bg: var(--cor-principal);
    --bs-btn-border-color: var(--cor-principal);
    --bs-btn-color: white; /* Alterado para branco para melhor contraste */
    --bs-btn-hover-bg: #951cf0;
    --bs-btn-hover-border-color: #951cf0;
    --bs-btn-active-bg: #7a00da;
    --bs-btn-active-border-color: #7a00da;
}

/* ESTILO DO RODAPÉ ADICIONADO */
.footer-text {
    position: fixed;
    bottom: 0;
    width: 100%;
    text-align: center;
    padding: 10px 0;
    color: #777;
    font-size: 0.8em;
}
```


## static\css\style.css
```css
:root {
    --cor-principal: #8800F0; /* Violeta */
    --cor-texto: #f5f5f5;
    --cor-fundo: #121212;
    --cor-card: #1e1e1e;
    --cor-borda: #333;
    --sombra-forte: rgba(0, 0, 0, 0.5);

    /* Sobrescrevendo variáveis do Bootstrap com a nova cor */
    --bs-body-bg: var(--cor-fundo);
    --bs-body-color: var(--cor-texto);
    --bs-primary: var(--cor-principal);
    --bs-primary-rgb: 136, 0, 240;
    --bs-border-color: var(--cor-borda);
}

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

html, body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.6;
    height: 100%;
}
```


## templates\commission_task_detail.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Detalhes do Serviço - OS {{ task.external_os_number }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .navbar-brand img { height: 40px; width: auto; }
        .navbar-brand, .nav-link.active { color: var(--cor-principal) !important; font-weight: 600; }
        .detail-item .detail-label {
            font-size: 0.8rem;
            color: #8a9198;
            text-transform: uppercase;
            margin-bottom: 0.35rem;
            font-weight: 500;
        }
        .detail-item .detail-value {
            font-size: 1.1rem;
            font-weight: 600;
            margin: 0;
        }
    </style>
</head>
<body>
   <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        <div class="card">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h3 class="mb-0">Serviço da OS: {{ task.external_os_number }}</h3>
                <div>
                    {% if current_user.role in ['Gerente', 'Supervisor'] %}
                        <a href="{{ url_for('edit_commission_task', task_id=task.id) }}" class="btn btn-sm btn-secondary">Editar</a>
                        <form action="{{ url_for('delete_commission_task', task_id=task.id) }}" method="POST" class="d-inline" onsubmit="return confirm('Tem certeza que deseja excluir este serviço?');">
                            <button type="submit" class="btn btn-sm btn-danger">Apagar</button>
                        </form>
                    {% endif %}
                </div>
            </div>
            <div class="card-body">
                <div class="row gy-4">
                    <div class="col-md-4">
                        <div class="detail-item">
                            <h6 class="detail-label">Responsável</h6>
                            <p class="detail-value">{{ task.technician.username.capitalize() }}</p>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="detail-item">
                            <h6 class="detail-label">Data de Conclusão</h6>
                            <p class="detail-value">{{ task.date_completed | localdatetime }}</p>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="detail-item">
                            <h6 class="detail-label">Dificuldade Total do Serviço</h6>
                            <p class="detail-value"><span class="badge bg-primary fs-6">{{ task.total_weight }}</span></p>
                        </div>
                    </div>
                    <div class="col-12">
                         <div class="detail-item">
                            <h6 class="detail-label">Serviços Realizados</h6>
                            <div class="row mt-2">
                                {% for service in task.services %}
                                <div class="col-md-6">
                                    <p class="detail-value mb-2">
                                        <i class="bi bi-check-lg text-success me-1"></i>{{ service.name }} (Peso: {{ service.weight }})
                                    </p>
                                </div>
                                {% endfor %}
                                {% for custom_service in task.custom_services %}
                                <div class="col-md-6">
                                    <p class="detail-value mb-2">
                                        <i class="bi bi-plus-circle text-info me-1"></i>{{ custom_service.name }} (Peso: {{ custom_service.weight }})
                                    </p>
                                </div>
                                {% endfor %}
                            </div>
                        </div>
                    </div>
                    {% if task.description %}
                    <div class="col-12">
                         <div class="detail-item">
                            <h6 class="detail-label">Observações</h6>
                            <p class="detail-value fst-italic" style="white-space: pre-wrap;">{{ task.description }}</p>
                        </div>
                    </div>
                    {% endif %}
                </div>
            </div>
            <div class="card-footer text-end">
                <a href="{{ url_for('commission_tasks') }}" class="btn btn-secondary btn-sm">Voltar para a lista</a>
            </div>
        </div>
    </main>
</body>
</html>
```


## templates\commission_tasks.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Serviços Feitos</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .table { 
            --bs-table-border-color: var(--cor-borda); 
        }
        .table th {
            color: var(--cor-principal);
            text-transform: uppercase;
            font-size: 0.8em;
            letter-spacing: 0.5px;
            text-align: center;
            vertical-align: middle;
        }
        .table td {
            text-align: center;
            vertical-align: middle;
            cursor: pointer;
        }
        .table td.actions-cell {
            cursor: default;
        }
        .table th:nth-child(1), .table td:nth-child(1),
        .table th:nth-child(3), .table td:nth-child(3) {
            text-align: left;
        }
        .os-number {
            color: var(--cor-texto);
            font-weight: 700;
        }
        /* Estilos de célula para preenchimento total com seletores mais específicos */
        .table td.cell-servico {
            background-color: #28a745;
            color: #fff;
            font-weight: 600;
            white-space: nowrap;
            font-size: 0.9em;
            min-width: 150px;
            text-align: center;
        }
        .table td.cell-orcamento {
            background-color: #343a40;
            color: #fff;
            font-weight: 600;
            white-space: nowrap;
            font-size: 0.9em;
            min-width: 150px;
            text-align: center;
        }
        .table td.cell-venda {
            background-color: #007bff;
            color: #fff;
            font-weight: 600;
            white-space: nowrap;
            font-size: 0.9em;
            min-width: 150px;
            text-align: center;
        }
        .description-text {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 300px;
            display: inline-block;
        }
        .badge-dificuldade-baixa { background-color: #ffc107; color: #000; }
        .badge-dificuldade-media { background-color: #0d6efd; }
        .badge-dificuldade-alta { background-color: #198754; }
        .nav-link.active { color: var(--cor-principal) !important; font-weight: 600; }
        .filters-panel { background-color: rgba(0,0,0,0.15); border: 1px solid var(--cor-borda); border-radius: 8px; padding: 1.25rem; }
        .pagination .page-item.active .page-link { background-color: var(--cor-principal); border-color: var(--cor-principal); }
        .pagination .page-link { color: var(--cor-principal); }
        .pagination .page-link:hover { background-color: #951cf0; color: #fff; }
    </style>
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
         <div class="card">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h2 class="h4 mb-0">Painel de Serviços para Comissão</h2>
                    <a href="{{ url_for('create_commission_task') }}" class="btn btn-primary btn-sm"><i class="bi bi-plus-lg me-1"></i> Lançar Serviço</a>
                </div>

                <div class="filters-panel mb-4">
                    <form method="GET" action="{{ url_for('commission_tasks') }}" class="row g-3 align-items-center">
                        <div class="col-md-3">
                            <label for="technician_id" class="form-label visually-hidden">Responsável</label>
                            <select name="technician_id" id="technician_id" class="form-select">
                                <option value="">Todos os Responsáveis</option>
                                {% for tech in technicians %}
                                    <option value="{{ tech.id }}" {% if tech.id|string == technician_filter %}selected{% endif %}>{{ tech.username.capitalize() }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-3">
                            <label for="service_type" class="form-label visually-hidden">Tipo de Serviço</label>
                            <select name="service_type" id="service_type" class="form-select">
                                <option value="">Todos os Tipos</option>
                                {% for type in service_types %}
                                    <option value="{{ type }}" {% if type == service_type_filter %}selected{% endif %}>{{ type }}</option>
                                {% endfor %}
                            </select>
                        </div>
                         <div class="col-md-2">
                            <label for="start_date" class="form-label visually-hidden">Data Início</label>
                            <input type="date" class="form-control" name="start_date" value="{{ start_date or '' }}" placeholder="Data Início">
                        </div>
                        <div class="col-md-2">
                            <label for="end_date" class="form-label visually-hidden">Data Fim</label>
                            <input type="date" class="form-control" name="end_date" value="{{ end_date or '' }}" placeholder="Data Fim">
                        </div>
                        <div class="col-md-2 d-grid d-md-flex gap-2">
                            <button type="submit" class="btn btn-info w-100">Filtrar</button>
                            <a href="{{ url_for('commission_tasks') }}" class="btn btn-secondary w-100" title="Limpar Filtros"><i class="bi bi-x-lg"></i></a>
                        </div>
                    </form>
                </div>

                <div class="table-responsive">
                    <table class="table table-hover table-bordered">
                        <thead>
                            <tr>
                                <th>Nº OS Externa</th>
                                <th>Serviço</th>
                                <th>Descrição</th>
                                <th>Dificuldade</th>
                                <th>Responsável</th>
                                <th>Data Conclusão</th>
                                <th>Ações</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for task in tasks.items %}
                            <tr>
                                <td onclick="window.location='{{ url_for('commission_task_detail', task_id=task.id) }}';"><span class="os-number">{{ task.external_os_number }}</span></td>
                                <td class="cell-{{ task.service_type_slug }}" onclick="window.location='{{ url_for('commission_task_detail', task_id=task.id) }}';">
                                    {{ task.display_service_name }}
                                </td>
                                <td onclick="window.location='{{ url_for('commission_task_detail', task_id=task.id) }}';">
                                    <span class="description-text">{{ task.display_description }}</span>
                                </td>
                                <td onclick="window.location='{{ url_for('commission_task_detail', task_id=task.id) }}';">
                                    {% set weight = task.total_weight %}
                                    {% if weight <= 3 %}{% set badge_class = 'badge-dificuldade-baixa' %}
                                    {% elif weight <= 8 %}{% set badge_class = 'badge-dificuldade-media' %}
                                    {% else %}{% set badge_class = 'badge-dificuldade-alta' %}{% endif %}
                                    <span class="badge {{ badge_class }}">{{ weight }}</span>
                                </td>
                                <td onclick="window.location='{{ url_for('commission_task_detail', task_id=task.id) }}';">{{ task.technician.username.capitalize() }}</td>
                                <td onclick="window.location='{{ url_for('commission_task_detail', task_id=task.id) }}';">{{ task.date_completed | localdatetime }}</td>
                                
                                <td class="actions-cell">
                                    {% if current_user.role in ['Gerente', 'Supervisor'] %}
                                    <a href="{{ url_for('edit_commission_task', task_id=task.id) }}" class="btn btn-sm btn-outline-primary py-0 px-1" title="Editar"><i class="bi bi-pencil-square"></i></a>
                                    <form action="{{ url_for('delete_commission_task', task_id=task.id) }}" method="POST" class="d-inline" onsubmit="return confirm('Tem certeza que deseja excluir este serviço?');">
                                        <button type="submit" class="btn btn-sm btn-outline-danger py-0 px-1" title="Excluir"><i class="bi bi-trash"></i></button>
                                    </form>
                                    {% endif %}
                                </td>
                            </tr>
                            {% else %}
                            <tr><td colspan="7" class="text-center py-5">Nenhum serviço de comissão encontrado com os filtros aplicados.</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                {% if tasks.pages > 1 %}
                <nav>
                    <ul class="pagination justify-content-center mt-4">
                        <li class="page-item {% if not tasks.has_prev %}disabled{% endif %}">
                            <a class="page-link" href="{{ url_for('commission_tasks', page=tasks.prev_num, technician_id=technician_filter, service_type=service_type_filter, start_date=start_date, end_date=end_date) }}">Anterior</a>
                        </li>
                        {% for page_num in tasks.iter_pages() %}
                            {% if page_num %}
                                {% if tasks.page == page_num %}
                                    <li class="page-item active"><a class="page-link" href="#">{{ page_num }}</a></li>
                                {% else %}
                                    <li class="page-item"><a class="page-link" href="{{ url_for('commission_tasks', page=page_num, technician_id=technician_filter, service_type=service_type_filter, start_date=start_date, end_date=end_date) }}">{{ page_num }}</a></li>
                                {% endif %}
                            {% else %}
                                <li class="page-item disabled"><a class="page-link" href="#">...</a></li>
                            {% endif %}
                        {% endfor %}
                        <li class="page-item {% if not tasks.has_next %}disabled{% endif %}">
                            <a class="page-link" href="{{ url_for('commission_tasks', page=tasks.next_num, technician_id=technician_filter, service_type=service_type_filter, start_date=start_date, end_date=end_date) }}">Próximo</a>
                        </li>
                    </ul>
                </nav>
                {% endif %}
            </div>
        </div>
    </main>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
```


## templates\completed_demands.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Demandas Concluídas</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .navbar-brand img { height: 40px; width: auto; }
        .navbar-brand, .nav-link { color: var(--cor-texto) !important; }
        .nav-link.active { color: var(--cor-principal) !important; font-weight: 600; }
        
        .table {
            --bs-table-bg: transparent;
            --bs-table-border-color: var(--cor-borda);
            --bs-table-hover-bg: rgba(255, 255, 255, 0.07);
        }
        .table th {
            font-weight: 600;
            color: var(--cor-texto);
            text-transform: uppercase;
            font-size: 0.8em;
            letter-spacing: 0.5px;
            border-bottom-width: 2px;
        }
        .table td { vertical-align: middle; padding: 1rem; }
        .table tbody tr { border-bottom: 1px solid var(--cor-borda); }
        .table tbody tr:last-child { border-bottom: none; }
        .table tbody tr:hover { cursor: pointer; }

        .demand-title a { text-decoration: none; color: var(--cor-texto); font-weight: 500; }
        .demand-title a:hover { color: var(--cor-principal); }
        .status-concluido { background-color: #198754; }
        .status-badge { padding: 0.4em 0.85em; font-size: 0.8em; font-weight: 600; color: #fff !important; border-radius: 6px; }
        .filters-panel { background-color: rgba(0,0,0,0.15); border: 1px solid var(--cor-borda); border-radius: 8px; padding: 1.25rem; }
        .pagination .page-item.active .page-link { background-color: var(--cor-principal); border-color: var(--cor-principal); }
        .pagination .page-link { color: var(--cor-principal); }
        .pagination .page-link:hover { background-color: #951cf0; color: #fff; }
    </style>
</head>
<body>
 <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        <div class="card">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h2 class="h4 mb-0">Painel de Demandas Concluídas</h2>
                </div>

                <div class="filters-panel mb-4">
                    <form method="GET" action="{{ url_for('completed_demands') }}" class="row g-3 align-items-center">
                        <div class="col-md-5">
                            <label for="assigned_to_id" class="form-label visually-hidden">Responsável</label>
                            <select name="assigned_to_id" id="assigned_to_id" class="form-select">
                                <option value="">Todos os Responsáveis</option>
                                {% for user in users %}
                                    <option value="{{ user.id }}" {% if user.id|string == user_filter %}selected{% endif %}>{{ user.username.capitalize() }}</option>
                                {% endfor %}
                                <option value="unassigned" {% if 'unassigned' == user_filter %}selected{% endif %}>Ninguém (N/A)</option>
                            </select>
                        </div>
                        <div class="col-md-2">
                            <label for="start_date" class="form-label visually-hidden">Data Início</label>
                            <input type="date" class="form-control" name="start_date" value="{{ start_date or '' }}" placeholder="Data Início">
                        </div>
                        <div class="col-md-2">
                            <label for="end_date" class="form-label visually-hidden">Data Fim</label>
                            <input type="date" class="form-control" name="end_date" value="{{ end_date or '' }}" placeholder="Data Fim">
                        </div>
                        <div class="col-md-3 d-grid d-md-flex gap-2">
                            <button type="submit" class="btn btn-info w-100">Filtrar</button>
                            <a href="{{ url_for('completed_demands') }}" class="btn btn-secondary w-100" title="Limpar Filtros"><i class="bi bi-x-lg"></i></a>
                        </div>
                    </form>
                </div>

                <div class="table-responsive">
                    <table class="table table-hover">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Título</th>
                                <th>Status</th>
                                <th>Prioridade</th>
                                <th>Responsável</th>
                                <th>Data</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for demand in demands.items %}
                            <tr onclick="window.location='{{ url_for('demand_detail', demand_id=demand.id) }}';">
                                <td><strong>{{ demand.demand_number }}</strong></td>
                                <td class="demand-title"><a href="{{ url_for('demand_detail', demand_id=demand.id) }}">{{ demand.title }}</a></td>
                                <td>
                                    <span class="status-badge status-concluido">{{ demand.status }}</span>
                                </td>
                                <td><span>{{ demand.priority }}</span></td>
                                <td>{{ demand.assigned_to.username.capitalize() if demand.assigned_to else 'N/A' }}</td>
                                <td>{{ demand.created_at | localdatetime('%d/%m/%Y') }}</td>
                            </tr>
                            {% else %}
                            <tr><td colspan="6" class="text-center py-5">Nenhuma demanda concluída encontrada com os filtros aplicados.</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                {% if demands.pages > 1 %}
                <nav>
                    <ul class="pagination justify-content-center mt-4">
                        <li class="page-item {% if not demands.has_prev %}disabled{% endif %}">
                            <a class="page-link" href="{{ url_for('completed_demands', page=demands.prev_num, assigned_to_id=user_filter, start_date=start_date, end_date=end_date) }}">Anterior</a>
                        </li>
                        {% for page_num in demands.iter_pages() %}
                            {% if page_num %}
                                {% if demands.page == page_num %}
                                    <li class="page-item active"><a class="page-link" href="#">{{ page_num }}</a></li>
                                {% else %}
                                    <li class="page-item"><a class="page-link" href="{{ url_for('completed_demands', page=page_num, assigned_to_id=user_filter, start_date=start_date, end_date=end_date) }}">{{ page_num }}</a></li>
                                {% endif %}
                            {% else %}
                                <li class="page-item disabled"><a class="page-link" href="#">...</a></li>
                            {% endif %}
                        {% endfor %}
                        <li class="page-item {% if not demands.has_next %}disabled{% endif %}">
                            <a class="page-link" href="{{ url_for('completed_demands', page=demands.next_num, assigned_to_id=user_filter, start_date=start_date, end_date=end_date) }}">Próximo</a>
                        </li>
                    </ul>
                </nav>
                {% endif %}
            </div>
        </div>
    </main>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
```


## templates\dashboard.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Demandas Internas</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .navbar-brand img { height: 40px; width: auto; }
        .navbar-brand, .nav-link { color: var(--cor-texto) !important; }
        .nav-link.active { color: var(--cor-principal) !important; font-weight: 600; }
        .btn-primary { background-color: var(--cor-principal); border-color: var(--cor-principal); color: #121212; font-weight: 600; }
        .btn-primary:hover { background-color: #951cf0; border-color: #951cf0; }
        
        .table {
            --bs-table-bg: transparent;
            --bs-table-border-color: var(--cor-borda);
            --bs-table-hover-bg: rgba(255, 255, 255, 0.07);
        }
        .table th {
            font-weight: 600;
            color: var(--cor-texto);
            text-transform: uppercase;
            font-size: 0.8em;
            letter-spacing: 0.5px;
            border-bottom-width: 2px;
        }
        .table td { vertical-align: middle; padding: 1rem; }
        .table tbody tr { border-bottom: 1px solid var(--cor-borda); }
        .table tbody td:not(.actions-cell) { cursor: pointer; }
        .table td.actions-cell { text-align: center; }

        .demand-title a { text-decoration: none; color: var(--cor-texto); font-weight: 500; }
        .demand-title a:hover { color: var(--cor-principal); }

        .priority-baixa { color: #6c757d; }
        .priority-normal { color: var(--cor-texto); }
        .priority-alta { color: #ffc107; font-weight: 500; }
        .priority-urgente { color: #dc3545; font-weight: 700; }
        
        /* Estilos de célula de status com preenchimento total e seletores reforçados */
        .table td.cell-nao-visto { background-color: #6c757d; color: #fff; font-weight: 600; text-align: center; }
        .table td.cell-em-andamento { background-color: #0d6efd; color: #fff; font-weight: 600; text-align: center; }
        .table td.cell-ag-adm, .table td.cell-ag-evandro, .table td.cell-ag-comercial { background-color: #fd7e14; color: #fff; font-weight: 600; text-align: center; }
        .table td.cell-parado { background-color: #dc3545; color: #fff; font-weight: 600; text-align: center; }
        .table td.cell-concluido { background-color: #198754; color: #fff; font-weight: 600; text-align: center; }

        .filters-panel { background-color: rgba(0,0,0,0.15); border: 1px solid var(--cor-borda); border-radius: 8px; padding: 1.25rem; }
        .pagination .page-item.active .page-link { background-color: var(--cor-principal); border-color: var(--cor-principal); }
        .pagination .page-link { color: var(--cor-principal); }
        .pagination .page-link:hover { background-color: #951cf0; color: #fff; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                    {{ message }}
                    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <div class="card">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h2 class="h4 mb-0">Painel de Demandas Ativas</h2>
                    {% if current_user.role in ['Gerente', 'Supervisor'] %}
                    <a href="{{ url_for('create_demand') }}" class="btn btn-primary btn-sm"><i class="bi bi-plus-lg me-1"></i> Nova Demanda Interna</a>
                    {% endif %}
                </div>

                <div class="filters-panel mb-4">
                    <form method="GET" action="{{ url_for('dashboard') }}" class="row g-3 align-items-center">
                        <div class="col-md-3">
                            <label for="status" class="form-label visually-hidden">Status</label>
                            <select name="status" id="status" class="form-select">
                                <option value="">Todos os Status</option>
                                {% for status in statuses %}
                                    <option value="{{ status }}" {% if status == status_filter %}selected{% endif %}>{{ status }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-3">
                            <label for="assigned_to_id" class="form-label visually-hidden">Responsável</label>
                            <select name="assigned_to_id" id="assigned_to_id" class="form-select">
                                <option value="">Todos os Responsáveis</option>
                                {% for user in users %}
                                    <option value="{{ user.id }}" {% if user.id|string == user_filter %}selected{% endif %}>{{ user.username.capitalize() }}</option>
                                {% endfor %}
                                <option value="unassigned" {% if 'unassigned' == user_filter %}selected{% endif %}>Ninguém (N/A)</option>
                            </select>
                        </div>
                        <div class="col-md-2">
                            <label for="start_date" class="form-label visually-hidden">Data Início</label>
                            <input type="date" class="form-control" name="start_date" value="{{ start_date or '' }}" placeholder="Data Início">
                        </div>
                        <div class="col-md-2">
                            <label for="end_date" class="form-label visually-hidden">Data Fim</label>
                            <input type="date" class="form-control" name="end_date" value="{{ end_date or '' }}" placeholder="Data Fim">
                        </div>
                        <div class="col-md-2 d-grid d-md-flex gap-2">
                            <button type="submit" class="btn btn-info w-100">Filtrar</button>
                            <a href="{{ url_for('dashboard') }}" class="btn btn-secondary w-100" title="Limpar Filtros"><i class="bi bi-x-lg"></i></a>
                        </div>
                    </form>
                </div>

                <div class="table-responsive">
                    <table class="table table-hover table-bordered">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Título</th>
                                <th>Status</th>
                                <th>Prioridade</th>
                                <th>Responsável</th>
                                <th>Data</th>
                                <th>Ações</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for demand in demands.items %}
                            <tr>
                                <td onclick="window.location='{{ url_for('demand_detail', demand_id=demand.id) }}';"><strong>{{ demand.demand_number }}</strong></td>
                                <td class="demand-title" onclick="window.location='{{ url_for('demand_detail', demand_id=demand.id) }}';"><a href="{{ url_for('demand_detail', demand_id=demand.id) }}">{{ demand.title }}</a></td>
                                
                                {% set status_slug = demand.status.lower().replace(' ', '-').replace('.', '') %}
                                <td class="cell-{{ status_slug }}" onclick="window.location='{{ url_for('demand_detail', demand_id=demand.id) }}';">
                                    {{ demand.status }}
                                </td>
                                
                                <td onclick="window.location='{{ url_for('demand_detail', demand_id=demand.id) }}';"><span class="{{ 'priority-' + demand.priority.lower() }}">{{ demand.priority }}</span></td>
                                <td onclick="window.location='{{ url_for('demand_detail', demand_id=demand.id) }}';">{{ demand.assigned_to.username.capitalize() if demand.assigned_to else 'N/A' }}</td>
                                <td onclick="window.location='{{ url_for('demand_detail', demand_id=demand.id) }}';">{{ demand.created_at | localdatetime('%d/%m/%Y - %H:%M') }}</td>
                                
                                <td class="actions-cell">
                                    {% if current_user.role in ['Gerente', 'Supervisor'] %}
                                    <a href="{{ url_for('edit_demand', demand_id=demand.id) }}" class="btn btn-sm btn-outline-primary py-0 px-1" title="Editar"><i class="bi bi-pencil-square"></i></a>
                                    <form action="{{ url_for('delete_demand', demand_id=demand.id) }}" method="POST" class="d-inline" onsubmit="return confirm('Tem certeza que deseja apagar esta demanda?');">
                                        <button type="submit" class="btn btn-sm btn-outline-danger py-0 px-1" title="Apagar"><i class="bi bi-trash"></i></button>
                                    </form>
                                    {% endif %}
                                </td>
                            </tr>
                            {% else %}
                            <tr><td colspan="7" class="text-center py-5">Nenhuma demanda ativa encontrada com os filtros aplicados.</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                
                {% if demands.pages > 1 %}
                <nav>
                    <ul class="pagination justify-content-center mt-4">
                        <li class="page-item {% if not demands.has_prev %}disabled{% endif %}">
                            <a class="page-link" href="{{ url_for('dashboard', page=demands.prev_num, status=status_filter, assigned_to_id=user_filter, start_date=start_date, end_date=end_date) }}">Anterior</a>
                        </li>
                        {% for page_num in demands.iter_pages() %}
                            {% if page_num %}
                                {% if demands.page == page_num %}
                                    <li class="page-item active"><a class="page-link" href="#">{{ page_num }}</a></li>
                                {% else %}
                                    <li class="page-item"><a class="page-link" href="{{ url_for('dashboard', page=page_num, status=status_filter, assigned_to_id=user_filter, start_date=start_date, end_date=end_date) }}">{{ page_num }}</a></li>
                                {% endif %}
                            {% else %}
                                <li class="page-item disabled"><a class="page-link" href="#">...</a></li>
                            {% endif %}
                        {% endfor %}
                        <li class="page-item {% if not demands.has_next %}disabled{% endif %}">
                            <a class="page-link" href="{{ url_for('dashboard', page=demands.next_num, status=status_filter, assigned_to_id=user_filter, start_date=start_date, end_date=end_date) }}">Próximo</a>
                        </li>
                    </ul>
                </nav>
                {% endif %}
            </div>
        </div>
    </main>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
```


## templates\demand_detail.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Detalhes da Demanda - {{ demand.title }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .navbar-brand img { height: 40px; width: auto; }
        .navbar-brand, .nav-link { color: var(--cor-texto) !important; }
        .nav-link.active { color: var(--cor-principal) !important; font-weight: 600; }
        .btn-primary { background-color: var(--cor-principal); border-color: var(--cor-principal); color: #121212; font-weight: 600; }
        
        .status-text-nao-visto { color: #6c757d !important; font-weight: bold; }
        .status-text-em-andamento { color: #0d6efd !important; font-weight: bold; }
        .status-text-aguardando { color: #fd7e14 !important; font-weight: bold; }
        .status-text-parado { color: #dc3545 !important; font-weight: bold; }
        .status-text-concluido { color: #198574 !important; font-weight: bold; }
        
        .priority-normal { color: var(--cor-texto); }
        .priority-alta { color: #ffc107; }
        .priority-urgente { color: #dc3545; }

        .demand-details-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 2.5rem; padding: 1.5rem 0; }
        .detail-item .detail-label { font-size: 0.8rem; color: #8a9198; text-transform: uppercase; margin-bottom: 0.35rem; font-weight: 500; }
        .detail-item .detail-value { font-size: 1.1rem; font-weight: 600; margin: 0; }
        .demand-metadata { border-top: 1px solid var(--cor-borda); padding-top: 1rem; }

        .time-tracking-panel { background-color: rgba(0,0,0,0.15); border: 1px solid var(--cor-borda); border-radius: 8px; padding: 1.25rem; }
        .total-duration-summary .total-label { color: #aaa; font-size: 0.9rem; }
        .total-duration-summary .total-value { font-size: 1.2rem; font-weight: bold; color: var(--bs-success); }
        .duration-list li { display: flex; justify-content: space-between; padding: 0.5rem 0; font-size: 0.9rem; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .duration-list li:last-child { border-bottom: none; }

        .actions-panel { background-color: rgba(255, 255, 255, 0.03); border: 1px solid var(--cor-borda); border-radius: 0.5rem; padding: 1.5rem; }
    </style>
</head>
<body>
    {% macro colorize_status(status_text) %}
        {% set status_class = 'status-text-nao-visto' %}
        {% if status_text == 'Em Andamento' %}{% set status_class = 'status-text-em-andamento' %}
        {% elif status_text.startswith('AG.') %}{% set status_class = 'status-text-aguardando' %}
        {% elif status_text == 'PARADO' %}{% set status_class = 'status-text-parado' %}
        {% elif status_text == 'CONCLUIDO' %}{% set status_class = 'status-text-concluido' %}
        {% endif %}
        <span class="{{ status_class }}">{{ status_text }}</span>
    {% endmacro %}

    <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                    {{ message }}
                    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <div class="card mb-4">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h3 class="mb-0">{{ demand.title }} ({{ demand.demand_number }})</h3>
                <div>
                    {% if current_user.role in ['Gerente', 'Supervisor'] %}
                        <a href="{{ url_for('edit_demand', demand_id=demand.id) }}" class="btn btn-secondary btn-sm">Editar</a>
                        <form action="{{ url_for('delete_demand', demand_id=demand.id) }}" method="POST" class="d-inline" onsubmit="return confirm('Tem certeza?');">
                            <button type="submit" class="btn btn-danger btn-sm">Apagar</button>
                        </form>
                    {% endif %}
                </div>
            </div>
            <div class="card-body">
                <p class="card-text mb-4" style="white-space: pre-wrap;">{{ demand.description }}</p>

                <div class="demand-details-grid">
                    <div class="detail-item">
                        <h6 class="detail-label">Status</h6>
                        <p class="detail-value">{{ demand.status }}</p>
                    </div>
                    <div class="detail-item">
                        <h6 class="detail-label">Atribuído a</h6>
                        <p class="detail-value">{{ demand.assigned_to.username.capitalize() if demand.assigned_to else 'Ninguém' }}</p>
                    </div>
                    <div class="detail-item">
                        <h6 class="detail-label">Prioridade</h6>
                        <p class="detail-value priority-{{ demand.priority.lower() }}">{{ demand.priority }}</p>
                    </div>
                </div>
                <div class="demand-metadata text-muted small mt-2">
                    Criado por <strong>{{ demand.requester.username.capitalize() }}</strong> em {{ demand.created_at | localdatetime('%d/%m/%Y às %H:%M') }}
                </div>
                
                <div class="time-tracking-panel mt-4">
                    <div class="panel-header d-flex justify-content-between align-items-center">
                        <h5 class="mb-0">Acompanhamento de Tempo</h5>
                        {% if total_duration %}
                        <div class="total-duration-summary">
                            <span class="total-label">Tempo Total: </span>
                            <span class="total-value">{{ total_duration }}</span>
                        </div>
                        {% endif %}
                    </div>
                    <hr class="mt-2 mb-3" style="border-color: rgba(255,255,255,0.1);">
                    <ul class="list-unstyled duration-list mb-0">
                    {% for status, time in durations.items() %}
                        <li><span>{{ status }}:</span> <span>{{ time }}</span></li>
                    {% else %}
                        <li>Nenhum tempo registrado.</li>
                    {% endfor %}
                    </ul>
                </div>
            </div>
        </div>

        <div class="mb-4 actions-panel">
            <h4 class="mb-4">Painel de Ações</h4>
            <div class="row g-4">
                <div class="col-lg-6">
                    <h6 class="mb-3">Alterar Status da Demanda</h6>
                    <form action="{{ url_for('update_demand_status', demand_id=demand.id) }}" method="POST">
                        <div class="mb-3">
                            <label for="status" class="form-label">Novo Status</label>
                            <select class="form-select" id="status" name="status">
                              <option value="Não Visto" {% if demand.status == 'Não Visto' %}selected{% endif %}>Não visto</option>
                              <option value="Em Andamento" {% if demand.status == 'Em Andamento' %}selected{% endif %}>Em andamento</option>
                              <option value="AG. ADM" {% if demand.status == 'AG. ADM' %}selected{% endif %}>AG. ADM</option>
                              <option value="AG. EVANDRO" {% if demand.status == 'AG. EVANDRO' %}selected{% endif %}>AG. EVANDRO</option>
                              <option value="AG. COMERCIAL" {% if demand.status == 'AG. COMERCIAL' %}selected{% endif %}>AG. COMERCIAL</option>
                              <option value="PARADO" {% if demand.status == 'PARADO' %}selected{% endif %}>Parado</option>
                              <option value="CONCLUIDO" {% if demand.status == 'CONCLUIDO' %}selected{% endif %}>Concluido</option>
                            </select>
                        </div>
                        <div class="mb-3">
                            <label for="note" class="form-label">Nota de Atualização (Opcional)</label>
                            <textarea class="form-control" id="note" name="note" rows="3"></textarea>
                        </div>
                        <div class="d-grid">
                            <button type="submit" class="btn btn-primary">Atualizar Status</button>
                        </div>
                    </form>
                </div>
                {% if current_user.role in ['Gerente', 'Supervisor'] %}
                <div class="col-lg-6">
                    <h6 class="mb-3">Atribuir Responsável</h6>
                    <form action="{{ url_for('assign_demand', demand_id=demand.id) }}" method="POST">
                        <div class="mb-3">
                            <label for="user_id" class="form-label">Atribuir para</label>
                            <select class="form-select" id="user_id" name="user_id">
                                <option value="">Selecione um usuário...</option>
                                {% for user in users %}
                                    <option value="{{ user.id }}" {% if demand.assigned_to_id == user.id %}selected{% endif %}>
                                        {{ user.username.capitalize() }}
                                    </option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="d-grid">
                            <button type="submit" class="btn btn-primary mt-4">Atribuir Usuário</button>
                        </div>
                    </form>
                </div>
                {% endif %}
            </div>
        </div>
        <div class="card">
            <div class="card-header"><h4>Histórico de Atividades</h4></div>
            <div class="card-body p-0">
                <ul class="list-group list-group-flush">
                    {% for log in logs %}
                    <li class="list-group-item bg-transparent d-flex justify-content-between align-items-center">
                        <div class="w-100">
                            <p class="mb-1">
                                {% if log.action.startswith("Status alterado de") %}
                                    {% set parts = log.action.split("'") %}
                                    {{ parts[0] }}
                                    '{{ colorize_status(parts[1]) | safe }}'
                                    {{ parts[2] }}
                                    '{{ colorize_status(parts[3]) | safe }}'{% if parts|length > 4 %}{{ parts[4] }}{% endif %}
                                {% else %}
                                    {{ log.action }}
                                {% endif %}
                            </p>
                            <small class="text-muted">Por: <strong>{{ log.user.username.capitalize() }}</strong> em {{ log.timestamp | localdatetime('%d/%m/%Y às %H:%M') }}</small>
                        </div>
                        {% if current_user.role in ['Gerente', 'Supervisor'] %}
                        <form action="{{ url_for('delete_log', log_id=log.id) }}" method="POST" class="d-inline ms-3" onsubmit="return confirm('Tem certeza?');">
                            <button type="submit" class="btn btn-outline-danger btn-sm py-0 px-1">&times;</button>
                        </form>
                        {% endif %}
                    </li>
                    {% else %}
                    <li class="list-group-item bg-transparent">Nenhum histórico para esta demanda.</li>
                    {% endfor %}
                </ul>
            </div>
        </div>
    </main>
</body>
</html>
```


## templates\edit_commission_task.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Editar Serviço</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
     <style>
        .nav-link.active { color: var(--cor-principal) !important; font-weight: 600; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        <div class="card">
            <div class="card-header">
                <h4 class="mb-0">Editar Serviço da OS: {{ task.external_os_number }}</h4>
            </div>
            <div class="card-body">
                <form method="POST" action="{{ url_for('edit_commission_task', task_id=task.id) }}">
                    <div class="row">
                        <div class="col-md-6 mb-3">
                            <label for="external_os_number" class="form-label">Nº OS Externa</label>
                            <input type="text" class="form-control" id="external_os_number" name="external_os_number" value="{{ task.external_os_number }}" required>
                        </div>
                        <div class="col-md-6 mb-3">
                            <label for="technician_id" class="form-label">Responsável</label>
                            <select class="form-select" id="technician_id" name="technician_id" required>
                                {% for tech in technicians %}
                                <option value="{{ tech.id }}" {% if tech.id == task.technician_id %}selected{% endif %}>{{ tech.username.capitalize() }}</option>
                                {% endfor %}
                            </select>
                        </div>
                    </div>
                    
                    <div class="mb-3">
                        <label for="service_type" class="form-label">Tipo de Lançamento (Não pode ser alterado)</label>
                        <input type="text" class="form-control" id="service_type" name="service_type" value="{{ task.service_type }}" readonly>
                    </div>

                    {% if task.service_type == 'Serviço' %}
                    <div class="mb-3">
                        <label class="form-label">Serviços Pré-definidos</label>
                        <div class="border rounded p-2" style="max-height: 200px; overflow-y: auto;">
                            {% set selected_service_ids = task.services|map(attribute='id')|list %}
                            {% for service in predefined_services %}
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" name="predefined_services" value="{{ service.id }}" id="service-{{ service.id }}" {% if service.id in selected_service_ids %}checked{% endif %}>
                                <label class="form-check-label" for="service-{{ service.id }}">{{ service.name }}</label>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                     <div class="mb-3">
                        <label for="description" class="form-label">Observações</label>
                        <textarea class="form-control" id="description" name="description" rows="3">{{ task.description or '' }}</textarea>
                    </div>
                    {% else %}
                    <div class="mb-3">
                        <label for="description" class="form-label">Descrição/Notas</label>
                        <textarea class="form-control" id="description" name="description" rows="5" readonly>{{ task.description or '' }}</textarea>
                        <small class="form-text text-muted">A edição detalhada de Orçamentos e Vendas não está disponível.</small>
                    </div>
                    {% endif %}

                    <div class="d-flex justify-content-end mt-4">
                        <a href="{{ url_for('commission_tasks') }}" class="btn btn-secondary me-2">Cancelar</a>
                        <button type="submit" class="btn btn-primary">Salvar Alterações</button>
                    </div>
                </form>
            </div>
        </div>
    </main>
</body>
</html>
```


## templates\edit_demand.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Editar Demanda - {{ demand.title }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .navbar-brand img { height: 40px; width: auto; }
        .navbar-brand, .nav-link.active { color: var(--cor-principal) !important; font-weight: 600; }
        .btn-primary { background-color: var(--cor-principal); border-color: var(--cor-principal); color: #121212; font-weight: 600; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        <div class="card">
            <div class="card-header">
                <h4 class="mb-0">Editar Demanda: {{ demand.demand_number }}</h4>
            </div>
            <div class="card-body">
                <form method="POST">
                    <div class="mb-3">
                        <label for="title" class="form-label">Título</label>
                        <input type="text" class="form-control" id="title" name="title" value="{{ demand.title }}" required>
                    </div>
                    <div class="mb-3">
                        <label for="description" class="form-label">Descrição</label>
                        <textarea class="form-control" id="description" name="description" rows="5" required>{{ demand.description }}</textarea>
                    </div>
                    <div class="mb-3">
                        <label for="priority" class="form-label">Prioridade</label>
                        <select class="form-select" id="priority" name="priority">
                          <option value="Baixa" {% if demand.priority == 'Baixa' %}selected{% endif %}>Baixa</option>
                          <option value="Normal" {% if demand.priority == 'Normal' %}selected{% endif %}>Normal</option>
                          <option value="Alta" {% if demand.priority == 'Alta' %}selected{% endif %}>Alta</option>
                          <option value="Urgente" {% if demand.priority == 'Urgente' %}selected{% endif %}>Urgente</option>
                        </select>
                    </div>
                    <div class="d-flex justify-content-end">
                        <a href="{{ url_for('demand_detail', demand_id=demand.id) }}" class="btn btn-secondary me-2">Cancelar</a>
                        <button type="submit" class="btn btn-primary">Salvar Alterações</button>
                    </div>
                </form>
            </div>
        </div>
    </main>
</body>
</html>
```


## templates\home.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Home</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .nav-link.active, .nav-link.active i {
            color: var(--cor-principal) !important;
            font-weight: 600;
        }
        .welcome-header {
            color: var(--cor-principal);
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        <h2 class="mb-4">Olá, <span class="welcome-header">{{ current_user.username.capitalize() }}</span>.</h2>
        <p class="lead">Aqui está um resumo de suas atividades pendentes.</p>
        
        <div class="row mt-4">
            <div class="col-lg-12">
                <div class="card">
                    <div class="card-header">
                        <h5 class="mb-0">Minhas Demandas Pendentes</h5>
                    </div>
                    <div class="card-body p-0">
                        <ul class="list-group list-group-flush">
                            {% for demand in pending_demands %}
                                <a href="{{ url_for('demand_detail', demand_id=demand.id) }}" class="list-group-item list-group-item-action bg-transparent">
                                    <div class="d-flex w-100 justify-content-between">
                                        <h6 class="mb-1">{{ demand.title }} ({{ demand.demand_number }})</h6>
                                        <small class="text-muted">{{ demand.created_at | localdatetime('%d/%m/%Y') }}</small>
                                    </div>
                                    <p class="mb-1">Status: {{ demand.status }} | Prioridade: {{ demand.priority }}</p>
                                </a>
                            {% else %}
                                <li class="list-group-item bg-transparent">Você não possui nenhuma demanda pendente.</li>
                            {% endfor %}
                        </ul>
                    </div>
                </div>
            </div>
            </div>
    </main>
</body>
</html>
```


## templates\index.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/login.css') }}">
</head>
<body>

    <div class="login-container d-flex justify-content-center align-items-center">
        <div class="login-box">
            <h1 class="login-title">ALFA-TASK</h1>
            <p class="login-subtitle">Acesso ao Sistema de Gestão de Demandas</p>
            
            {% for category, message in get_flashed_messages(with_categories=true) %}
                <div class="alert alert-{{ category }} mb-3" role="alert">
                    {{ message }}
                </div>
            {% endfor %}
            
            <form class="login-form" action="{{ url_for('login') }}" method="POST">
                <div class="mb-3 text-start">
                    <label for="username" class="form-label">Usuário</label>
                    <input type="text" class="form-control" id="username" name="username" required>
                </div>
                <div class="mb-4 text-start">
                    <label for="password" class="form-label">Senha</label>
                    <input type="password" class="form-control" id="password" name="password" required>
                </div>
                <div class="d-grid">
                    <button type="submit" class="btn btn-primary login-button">Entrar</button>
                </div>
            </form>
        </div>
    </div>

    <footer class="footer-text">
        <p class="mb-0"><strong>ALFA-TASK</strong></p>
        <p>&copy; 2025 Direitos autorais a Júlio Martins</p>
    </footer>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz" crossorigin="anonymous"></script>
</body>
</html>
```


## templates\new_commission_task.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Lançar Serviço</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .nav-link.active { color: var(--cor-principal) !important; font-weight: 600; }
        .form-section {
            border-left: 3px solid var(--cor-borda);
            padding-left: 1.5rem;
            margin-top: 1.5rem;
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        <div class="card">
            <div class="card-header">
                <h4 class="mb-0">Lançar Novo Serviço para Comissão</h4>
            </div>
            <div class="card-body">
                {% with messages = get_flashed_messages(with_categories=true) %}
                    {% if messages %}
                        {% for category, message in messages %}
                        <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                        </div>
                        {% endfor %}
                    {% endif %}
                {% endwith %}

                <form method="POST" action="{{ url_for('create_commission_task') }}">
                    <div class="row">
                        <div class="col-md-4 mb-3">
                            <label for="external_os_number" class="form-label">Nº OS Externa</label>
                            <input type="text" class="form-control" id="external_os_number" name="external_os_number" required>
                        </div>
                        <div class="col-md-4 mb-3">
                            <label for="technician_id" class="form-label">Responsável</label>
                            {% if technicians|length > 1 %}
                                <select class="form-select" id="technician_id" name="technician_id" required>
                                    <option value="" disabled selected>Selecione um responsável...</option>
                                    {% for tech in technicians %}
                                    <option value="{{ tech.id }}">{{ tech.username.capitalize() }}</option>
                                    {% endfor %}
                                </select>
                            {% else %}
                                <input type="text" class="form-control" value="{{ technicians[0].username.capitalize() }}" readonly>
                                <input type="hidden" name="technician_id" value="{{ technicians[0].id }}">
                            {% endif %}
                        </div>
                        <div class="col-md-4 mb-3">
                            <label for="service_type" class="form-label">Tipo de Lançamento</label>
                            <select class="form-select" id="service_type" name="service_type" required>
                                <option value="" disabled selected>Selecione um tipo...</option>
                                <option value="Serviço">Serviço</option>
                                <option value="Orçamento">Orçamento</option>
                                <option value="Venda">Venda</option>
                            </select>
                        </div>
                    </div>

                    <div id="service-section" class="form-section d-none">
                        <h5>Detalhes do Serviço</h5>
                        <div class="mb-3">
                            <label class="form-label">Serviços Pré-definidos (selecione um ou mais)</label>
                            <div class="border rounded p-2" style="max-height: 200px; overflow-y: auto;">
                                {% for service in predefined_services %}
                                <div class="form-check">
                                    <input class="form-check-input" type="checkbox" name="predefined_services" value="{{ service.id }}" id="service-{{ service.id }}">
                                    <label class="form-check-label" for="service-{{ service.id }}">{{ service.name }} (Peso: {{ service.weight }})</label>
                                </div>
                                {% endfor %}
                            </div>
                        </div>
                        <div class="mb-3">
                            <label for="description" class="form-label">Observações (Opcional)</label>
                            <textarea class="form-control" id="description" name="description" rows="3"></textarea>
                        </div>
                    </div>
                    
                    <div id="budget-section" class="form-section d-none">
                        <h5>Detalhes do Orçamento</h5>
                        <div class="mb-3">
                            <label class="form-label">Equipamentos Orçados (selecione um ou mais)</label>
                            {% set equipments = ['Impressora G', 'PC Gamer', 'Notebook', 'Servidor', 'All in one', 'Nobreak', 'PC Comum', 'Impressora P'] %}
                            <div class="border rounded p-2">
                                {% for item in equipments %}
                                <div class="form-check form-check-inline">
                                    <input class="form-check-input" type="checkbox" name="budget_equipment" value="{{ item }}" id="equip-{{ loop.index }}">
                                    <label class="form-check-label" for="equip-{{ loop.index }}">{{ item }}</label>
                                </div>
                                {% endfor %}
                            </div>
                        </div>
                         <div class="mb-3">
                            <label for="budget_notes" class="form-label">Notas do Orçamento (Opcional)</label>
                            <textarea class="form-control" id="budget_notes" name="budget_notes" rows="3"></textarea>
                        </div>
                    </div>

                    <div id="sale-section" class="form-section d-none">
                        <h5>Detalhes da Venda</h5>
                        <div class="mb-3">
                            <label class="form-label">Itens Vendidos (selecione um ou mais)</label>
                            {% set sale_item_options = ['Notebook Usado', 'PC Usado', 'Impressora', 'Monitor', 'Teclado', 'Mouse', 'SSD', 'Memória RAM', 'Fonte'] %}
                            <div class="border rounded p-3">
                                <div class="row">
                                    {% for item in sale_item_options %}
                                    <div class="col-md-4 col-sm-6">
                                        <div class="form-check">
                                            <input class="form-check-input" type="checkbox" name="sale_items" value="{{ item }}" id="sale-{{ loop.index }}">
                                            <label class="form-check-label" for="sale-{{ loop.index }}">{{ item }}</label>
                                        </div>
                                    </div>
                                    {% endfor %}
                                </div>
                                <hr>
                                <label for="other_sale_items" class="form-label mt-2">Outros itens (separados por vírgula):</label>
                                <input type="text" class="form-control" id="other_sale_items" name="sale_items" placeholder="Cabo HDMI, Adaptador Wifi...">
                            </div>
                        </div>
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="commission_value" class="form-label">Valor Total da Venda (R$)</label>
                                <input type="number" step="0.01" class="form-control" id="commission_value" name="commission_value" placeholder="Ex: 2500.00">
                            </div>
                        </div>
                        <div class="mb-3">
                            <label for="sale_notes" class="form-label">Notas da Venda (Opcional)</label>
                            <textarea class="form-control" id="sale_notes" name="sale_notes" rows="3"></textarea>
                        </div>
                    </div>

                    <div class="d-flex justify-content-end mt-4">
                        <a href="{{ url_for('commission_tasks') }}" class="btn btn-secondary me-2">Cancelar</a>
                        <button type="submit" class="btn btn-primary">Lançar Serviço</button>
                    </div>
                </form>
            </div>
        </div>
    </main>

    <script>
        document.getElementById('service_type').addEventListener('change', function() {
            const serviceSection = document.getElementById('service-section');
            const budgetSection = document.getElementById('budget-section');
            const saleSection = document.getElementById('sale-section');

            // Esconde todas as seções
            serviceSection.classList.add('d-none');
            budgetSection.classList.add('d-none');
            saleSection.classList.add('d-none');

            // Mostra a seção correspondente
            if (this.value === 'Serviço') {
                serviceSection.classList.remove('d-none');
            } else if (this.value === 'Orçamento') {
                budgetSection.classList.remove('d-none');
            } else if (this.value === 'Venda') {
                saleSection.classList.remove('d-none');
            }
        });
    </script>
</body>
</html>
```


## templates\new_demand.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Nova Demanda Interna</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
</head>
<body>
   <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
    <div class="container-fluid">
        <a class="navbar-brand" href="{{ url_for('home_page') }}">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
        </a>

        <div class="d-flex">
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
            <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
            <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
        </div>

        <div class="d-flex align-items-center ms-auto">
            <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
            <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
            <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
        </div>
    </div>
</nav>
    <main class="container mt-4">
        <div class="row justify-content-center">
            <div class="col-lg-8">
                <div class="card">
                    <div class="card-header">
                        <h4 class="mb-0">Criar Nova Demanda Interna</h4>
                    </div>
                    <div class="card-body">
                        <form method="POST">
                            <div class="mb-3">
                                <label for="title" class="form-label">Título</label>
                                <input type="text" class="form-control" id="title" name="title" required>
                            </div>
                            <div class="mb-3">
                                <label for="description" class="form-label">Descrição</label>
                                <textarea class="form-control" id="description" name="description" rows="5" required></textarea>
                            </div>
                            <div class="mb-3">
                                <label for="priority" class="form-label">Prioridade</label>
                                <select class="form-select" id="priority" name="priority">
                                  <option value="Baixa">Baixa</option>
                                  <option value="Normal" selected>Normal</option>
                                  <option value="Alta">Alta</option>
                                  <option value="Urgente">Urgente</option>
                                </select>
                            </div>
                            <div class="d-flex justify-content-end">
                                <a href="{{ url_for('dashboard') }}" class="btn btn-secondary me-2">Cancelar</a>
                                <button type="submit" class="btn btn-primary">Criar Demanda</button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </main>
</body>
</html>
```


## templates\notes.html
```html
<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALFA-TASK | Anotações</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <script src="https://cdn.tiny.cloud/1/9uyxem1kujegj7pw2jnlbve49do5cv2dt9psf4auxuxcapao/tinymce/6/tinymce.min.js" referrerpolicy="origin"></script>
    <style>
        .note-card {
            background-color: var(--cor-card);
            border: 1px solid var(--cor-borda);
            display: flex;
            flex-direction: column;
            height: 280px; /* Altura fixa para os cards */
        }
        .note-card .card-header {
            font-weight: 600;
        }
        .note-card .card-body {
            flex-grow: 1;
            overflow: hidden;
        }
        .note-content {
            height: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            display: -webkit-box;
            -webkit-line-clamp: 8; /* Limite de linhas para o texto */
            -webkit-box-orient: vertical;
        }
        /* Ajuste para o seletor de cor */
        input[type="color"] {
            -webkit-appearance: none;
            -moz-appearance: none;
            appearance: none;
            width: 40px;
            height: 40px;
            background-color: transparent;
            border: none;
            cursor: pointer;
        }
        input[type="color"]::-webkit-color-swatch {
            border-radius: 50%;
            border: 1px solid var(--cor-borda);
        }
        input[type="color"]::-moz-color-swatch {
            border-radius: 50%;
            border: 1px solid var(--cor-borda);
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom" data-bs-theme="dark">
        <div class="container-fluid">
            <a class="navbar-brand" href="{{ url_for('home_page') }}">
                <img src="{{ url_for('static', filename='logo.png') }}" alt="ALFA-TASK Logo" style="height: 40px;">
            </a>
            <div class="d-flex">
                <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'home_page' %}active{% endif %}" href="{{ url_for('home_page') }}">Home</a>
                <a class="btn btn-outline-secondary me-2 {% if request.endpoint in ['dashboard', 'demand_detail', 'new_demand', 'edit_demand'] %}active{% endif %}" href="{{ url_for('dashboard') }}">Demandas Ativas</a>
                <a class="btn btn-outline-secondary me-2 {% if request.endpoint == 'completed_demands' %}active{% endif %}" href="{{ url_for('completed_demands') }}">Demandas Concluídas</a>
                <a class="btn btn-outline-secondary {% if request.endpoint in ['commission_tasks', 'commission_task_detail', 'new_commission_task', 'edit_commission_task'] %}active{% endif %}" href="{{ url_for('commission_tasks') }}">Serviços Feitos</a>
            </div>
            <div class="d-flex align-items-center ms-auto">
                <a href="{{ url_for('notes') }}" class="btn btn-outline-secondary me-3 {% if request.endpoint == 'notes' %}active{% endif %}">Anotações</a>
                <span class="navbar-text me-3">Bem-vindo, {{ current_user.username.capitalize() }}!</span>
                <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm d-flex align-items-center" title="Sair"><i class="bi bi-box-arrow-right me-1"></i>Sair</a>
            </div>
        </div>
    </nav>
    <main class="container mt-4">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h2 class="h4 mb-0">Minhas Anotações</h2>
            <button type="button" class="btn btn-primary" onclick="openAddModal()">
                <i class="bi bi-plus-lg me-1"></i> Adicionar Nova Nota
            </button>
        </div>
        
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                    {{ message }}
                    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <div class="row">
            {% for note in notes %}
            <div class="col-md-6 col-lg-4 mb-4">
                <div class="card note-card h-100">
                    <div class="card-header" style="background-color: {{ note.color }}; color: {{ get_text_color_for_bg(note.color) }}; border-bottom: 1px solid {{ note.color }};">
                        <h5 class="card-title mb-0">{{ note.title }}</h5>
                    </div>
                    <div class="card-body d-flex flex-column">
                        <div class="note-content">
                            {{ note.content | safe }}
                        </div>
                         <div class="d-flex justify-content-between align-items-center mt-auto pt-2">
                            <small class="text-muted">{{ note.created_at | localdatetime('%d/%m/%Y') }}</small>
                            <div>
                                <button class="btn btn-sm btn-outline-secondary py-0 px-1" title="Copiar" id="copy-btn-{{ note.id }}" onclick="copyNoteContent({{ note.id }})">
                                    <i class="bi bi-clipboard" id="copy-icon-{{ note.id }}"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-primary py-0 px-1" title="Editar" onclick="openEditModal({{ note.id }})">
                                    <i class="bi bi-pencil-square"></i>
                                </button>
                                <form action="{{ url_for('delete_note', note_id=note.id) }}" method="POST" class="d-inline" onsubmit="return confirm('Tem certeza?');">
                                    <button type="submit" class="btn btn-sm btn-outline-danger py-0 px-1" title="Apagar"><i class="bi bi-trash"></i></button>
                                </form>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            {% else %}
            <div class="col-12">
                <p>Nenhuma anotação encontrada. Clique em "Adicionar Nova Nota" para começar.</p>
            </div>
            {% endfor %}
        </div>
    </main>

    <div class="modal fade" id="noteModal" tabindex="-1" aria-labelledby="noteModalLabel" aria-hidden="true">
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="noteModalLabel">Nova Anotação</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <form id="noteForm" method="POST">
                    <div class="modal-body">
                        <div class="mb-3">
                            <label for="note-title" class="form-label">Título da Nota</label>
                            <input type="text" class="form-control" id="note-title" name="title" required>
                        </div>
                        <div class="mb-3">
                            <label for="note-color" class="form-label">Cor do Cabeçalho</label>
                            <input type="color" class="form-control form-control-color" id="note-color" name="color" value="#343a40" title="Escolha uma cor">
                        </div>
                        <div class="mb-3">
                            <label for="note-content" class="form-label">Descrição</label>
                            <textarea id="note-content" name="content"></textarea>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
                        <button type="submit" class="btn btn-primary">Salvar</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>

    <script>
        // Inicialização do Editor de Texto
        tinymce.init({
            selector: 'textarea#note-content',
            plugins: 'autolink lists link image charmap preview anchor searchreplace visualblocks code fullscreen insertdatetime media table help wordcount',
            toolbar: 'undo redo | blocks | bold italic underline strikethrough | alignleft aligncenter alignright alignjustify | bullist numlist outdent indent | link image | forecolor backcolor removeformat',
            skin: 'oxide-dark',
            content_css: 'dark',
            height: 300,
            menubar: false,
        });

        const noteModal = new bootstrap.Modal(document.getElementById('noteModal'));
        const modalTitle = document.getElementById('noteModalLabel');
        const noteForm = document.getElementById('noteForm');
        const noteTitleInput = document.getElementById('note-title');
        const noteColorInput = document.getElementById('note-color');

        function openAddModal() {
            noteForm.action = "{{ url_for('notes') }}";
            modalTitle.textContent = 'Nova Anotação';
            noteTitleInput.value = '';
            noteColorInput.value = '#343a40'; // Cor padrão
            tinymce.get('note-content').setContent('');
            noteModal.show();
        }

        async function openEditModal(noteId) {
            try {
                const response = await fetch(`/notes/data/${noteId}`);
                if (!response.ok) throw new Error('Erro ao buscar dados da nota.');
                const data = await response.json();

                noteForm.action = `/notes/${noteId}/edit`;
                modalTitle.textContent = 'Editar Anotação';
                noteTitleInput.value = data.title;
                noteColorInput.value = data.color;
                tinymce.get('note-content').setContent(data.content || '');
                noteModal.show();

            } catch (error) {
                console.error(error);
                alert(error.message);
            }
        }

        async function copyNoteContent(noteId) {
            const icon = document.getElementById(`copy-icon-${noteId}`);
            try {
                const response = await fetch(`/notes/data/${noteId}`);
                if (!response.ok) throw new Error('Erro ao buscar dados da nota para copiar.');
                const data = await response.json();
                
                const htmlContent = data.content || '';

                // Cria uma versão em texto puro para colar em locais sem formatação
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = htmlContent;
                const plainText = tempDiv.textContent || tempDiv.innerText || "";

                // Prepara os dados para a área de transferência com ambos os formatos
                const htmlBlob = new Blob([htmlContent], { type: 'text/html' });
                const textBlob = new Blob([plainText], { type: 'text/plain' });
                const clipboardItem = new ClipboardItem({
                    'text/html': htmlBlob,
                    'text/plain': textBlob,
                });

                await navigator.clipboard.write([clipboardItem]);

                // Feedback visual para o usuário
                icon.classList.remove('bi-clipboard');
                icon.classList.add('bi-clipboard-check-fill', 'text-success');
                setTimeout(() => {
                    icon.classList.remove('bi-clipboard-check-fill', 'text-success');
                    icon.classList.add('bi-clipboard');
                }, 2000);

            } catch (error) {
                console.error('Falha ao copiar:', error);
                // Feedback de erro (opcional)
                icon.classList.remove('bi-clipboard');
                icon.classList.add('bi-clipboard-x', 'text-danger');
                 setTimeout(() => {
                    icon.classList.remove('bi-clipboard-x', 'text-danger');
                    icon.classList.add('bi-clipboard');
                }, 2000);
            }
        }
    </script>
</body>
</html>
```
