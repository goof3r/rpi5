import os
import bcrypt
from flask import (
    Flask, render_template, redirect, url_for,
    request, flash, jsonify, session
)
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from flask_wtf.csrf import CSRFProtect

from config import Config
from models import db, User, ProxmoxSettings, LxcContainer
import proxmox_api as pve

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Zaloguj się, aby uzyskać dostęp.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').encode('utf-8')
        user = User.query.filter_by(username=username).first()

        if user and bcrypt.checkpw(password, user.password_hash.encode('utf-8')):
            login_user(user)
            return redirect(url_for('dashboard'))

        flash('Nieprawidłowy login lub hasło.', 'danger')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
@login_required
def index():
    return redirect(url_for('dashboard'))


@app.route('/dashboard')
@login_required
def dashboard():
    settings = ProxmoxSettings.query.first()
    containers = []
    error = None

    if settings:
        try:
            containers = pve.list_containers(settings.node)
            db_containers = {c.vmid: c for c in LxcContainer.query.all()}
            for c in containers:
                vmid = c.get('vmid')
                if vmid in db_containers:
                    c['db_info'] = db_containers[vmid]
        except Exception as e:
            error = str(e)

    db_history = LxcContainer.query.order_by(LxcContainer.created_at.desc()).limit(50).all()
    return render_template('dashboard.html',
                           containers=containers,
                           db_history=db_history,
                           settings=settings,
                           error=error)


# ---------------------------------------------------------------------------
# Tworzenie LXC
# ---------------------------------------------------------------------------

@app.route('/create', methods=['GET', 'POST'])
@login_required
def create_lxc():
    settings = ProxmoxSettings.query.first()
    if not settings:
        flash('Najpierw skonfiguruj połączenie z Proxmox w ustawieniach.', 'warning')
        return redirect(url_for('settings'))

    if request.method == 'POST':
        f = request.form

        vmid = f.get('vmid') or None
        if not vmid:
            try:
                vmid = pve.get_next_vmid()
            except Exception as e:
                flash(f'Nie można pobrać VMID: {e}', 'danger')
                return redirect(url_for('create_lxc'))

        hostname = f.get('hostname', '').strip()
        ostemplate = f.get('ostemplate', '')
        root_password = f.get('root_password', '')
        memory = int(f.get('memory', 512))
        swap = int(f.get('swap', 0))
        cores = int(f.get('cores', 1))
        disk_gb = float(f.get('disk_gb', 8))
        storage = f.get('storage', 'local')
        bridge = f.get('bridge', 'vmbr0')
        nic_model = f.get('nic_model', 'virtio')
        ip_type = f.get('ip_type', 'dhcp')
        ip_address = f.get('ip_address', '')
        gateway = f.get('gateway', '')
        nameserver = f.get('nameserver', '')
        start_after = 1 if f.get('start_after') else 0
        unprivileged = 1 if f.get('unprivileged') else 0

        if ip_type == 'dhcp':
            ip_config = 'dhcp'
            net0 = f'name=eth0,bridge={bridge},ip=dhcp,type={nic_model}'
        else:
            ip_config = ip_address
            net0 = f'name=eth0,bridge={bridge},ip={ip_address},gw={gateway},type={nic_model}'

        params = {
            'vmid': vmid,
            'hostname': hostname,
            'ostemplate': ostemplate,
            'password': root_password,
            'memory': memory,
            'swap': swap,
            'cores': cores,
            'rootfs': f'{storage}:{disk_gb}',
            'net0': net0,
            'start': start_after,
            'unprivileged': unprivileged,
        }
        if nameserver:
            params['nameserver'] = nameserver

        try:
            task_id = pve.create_lxc(settings.node, params)

            record = LxcContainer(
                vmid=int(vmid),
                hostname=hostname,
                ram_mb=memory,
                disk_gb=disk_gb,
                cores=cores,
                network_bridge=bridge,
                template=ostemplate.split('/')[-1],
                ip_config=ip_config,
                status='creating',
                created_by=current_user.id,
            )
            db.session.add(record)
            db.session.commit()

            flash(f'Kontener {hostname} (VMID {vmid}) tworzony. Task: {task_id}', 'success')
            return redirect(url_for('dashboard'))

        except Exception as e:
            flash(f'Błąd tworzenia kontenera: {e}', 'danger')

    return render_template('create_lxc.html', settings=settings)


# ---------------------------------------------------------------------------
# Ustawienia
# ---------------------------------------------------------------------------

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    proxmox_cfg = ProxmoxSettings.query.first()
    return render_template('settings.html', proxmox_cfg=proxmox_cfg)


@app.route('/settings/password', methods=['POST'])
@login_required
def change_password():
    old_pw = request.form.get('old_password', '').encode('utf-8')
    new_pw = request.form.get('new_password', '').encode('utf-8')
    confirm = request.form.get('confirm_password', '').encode('utf-8')

    if not bcrypt.checkpw(old_pw, current_user.password_hash.encode('utf-8')):
        flash('Stare hasło jest nieprawidłowe.', 'danger')
        return redirect(url_for('settings'))

    if new_pw != confirm:
        flash('Nowe hasła nie są identyczne.', 'danger')
        return redirect(url_for('settings'))

    if len(new_pw) < 4:
        flash('Hasło musi mieć co najmniej 4 znaki.', 'danger')
        return redirect(url_for('settings'))

    hashed = bcrypt.hashpw(new_pw, bcrypt.gensalt()).decode('utf-8')
    current_user.password_hash = hashed
    db.session.commit()
    flash('Hasło zostało zmienione.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/proxmox', methods=['POST'])
@login_required
def save_proxmox_settings():
    cfg = ProxmoxSettings.query.first()
    if not cfg:
        cfg = ProxmoxSettings()
        db.session.add(cfg)

    cfg.host = request.form.get('host', '').strip()
    cfg.port = int(request.form.get('port', 8006))
    cfg.node = request.form.get('node', '').strip()
    cfg.token_id = request.form.get('token_id', '').strip()
    cfg.token_secret = request.form.get('token_secret', '').strip()
    cfg.verify_ssl = bool(request.form.get('verify_ssl'))
    db.session.commit()
    flash('Konfiguracja Proxmox zapisana.', 'success')
    return redirect(url_for('settings'))


# ---------------------------------------------------------------------------
# API (AJAX)
# ---------------------------------------------------------------------------

@app.route('/api/templates')
@login_required
def api_templates():
    settings = ProxmoxSettings.query.first()
    if not settings:
        return jsonify({'error': 'Brak konfiguracji Proxmox'}), 400
    try:
        templates = pve.get_templates(settings.node)
        return jsonify(templates)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bridges')
@login_required
def api_bridges():
    settings = ProxmoxSettings.query.first()
    if not settings:
        return jsonify({'error': 'Brak konfiguracji Proxmox'}), 400
    try:
        bridges = pve.get_bridges(settings.node)
        return jsonify(bridges)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/storages')
@login_required
def api_storages():
    settings = ProxmoxSettings.query.first()
    if not settings:
        return jsonify({'error': 'Brak konfiguracji Proxmox'}), 400
    try:
        storages = pve.get_storages(settings.node)
        return jsonify(storages)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/next-vmid')
@login_required
def api_next_vmid():
    try:
        vmid = pve.get_next_vmid()
        return jsonify({'vmid': vmid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/test-connection', methods=['POST'])
@login_required
def api_test_connection():
    data = request.get_json()
    ok, msg = pve.test_connection(data)
    return jsonify({'ok': ok, 'message': msg})


@app.route('/api/containers/<int:vmid>/start', methods=['POST'])
@login_required
def api_start(vmid):
    settings = ProxmoxSettings.query.first()
    if not settings:
        return jsonify({'error': 'Brak konfiguracji'}), 400
    try:
        task = pve.start_container(settings.node, vmid)
        _update_container_status(vmid, 'running')
        return jsonify({'task': task})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/containers/<int:vmid>/stop', methods=['POST'])
@login_required
def api_stop(vmid):
    settings = ProxmoxSettings.query.first()
    if not settings:
        return jsonify({'error': 'Brak konfiguracji'}), 400
    try:
        task = pve.stop_container(settings.node, vmid)
        _update_container_status(vmid, 'stopped')
        return jsonify({'task': task})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/containers/<int:vmid>', methods=['DELETE'])
@login_required
def api_delete(vmid):
    settings = ProxmoxSettings.query.first()
    if not settings:
        return jsonify({'error': 'Brak konfiguracji'}), 400
    try:
        task = pve.delete_container(settings.node, vmid)
        record = LxcContainer.query.filter_by(vmid=vmid).first()
        if record:
            record.status = 'deleted'
            db.session.commit()
        return jsonify({'task': task})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/containers/<int:vmid>/status')
@login_required
def api_container_status(vmid):
    settings = ProxmoxSettings.query.first()
    if not settings:
        return jsonify({'error': 'Brak konfiguracji'}), 400
    try:
        status = pve.get_container_status(settings.node, vmid)
        _update_container_status(vmid, status.get('status', 'unknown'))
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _update_container_status(vmid, status):
    record = LxcContainer.query.filter_by(vmid=vmid).first()
    if record:
        record.status = status
        db.session.commit()


# ---------------------------------------------------------------------------
# Uruchomienie
# ---------------------------------------------------------------------------

def create_default_admin():
    if not User.query.filter_by(username='admin').first():
        hashed = bcrypt.hashpw(b'admin', bcrypt.gensalt()).decode('utf-8')
        admin = User(username='admin', password_hash=hashed)
        db.session.add(admin)
        db.session.commit()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_default_admin()
    port = int(os.environ.get('FLASK_PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
