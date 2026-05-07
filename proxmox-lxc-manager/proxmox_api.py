import urllib3
from proxmoxer import ProxmoxAPI
from models import ProxmoxSettings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_settings():
    return ProxmoxSettings.query.first()


def get_connection():
    s = get_settings()
    if not s:
        raise RuntimeError("Brak konfiguracji Proxmox. Uzupełnij ustawienia.")
    return _proxmox_connection(s)


def _proxmox_connection(settings):
    """Buduje połączenie z podanych ustawień (dict lub model)."""
    if isinstance(settings, dict):
        host = settings['host']
        port = settings.get('port', 8006)
        token_id = settings['token_id']
        token_secret = settings['token_secret']
        verify_ssl = settings.get('verify_ssl', False)
    else:
        host = settings.host
        port = settings.port
        token_id = settings.token_id
        token_secret = settings.token_secret
        verify_ssl = settings.verify_ssl

    # token_id ma format: user@realm!tokenname
    parts = token_id.split('!')
    if len(parts) == 2:
        user_part, token_name = parts
    else:
        user_part = token_id
        token_name = 'api'

    return ProxmoxAPI(
        host,
        port=port,
        user=user_part,
        token_name=token_name,
        token_value=token_secret,
        verify_ssl=verify_ssl,
    )


def test_connection(settings_dict):
    """Testuje połączenie — zwraca (True, info) lub (False, error_msg).
    Używa /nodes (wymaga auth), nie /version (publiczny endpoint)."""
    try:
        px = _proxmox_connection(settings_dict)
        nodes = px.nodes.get()
        version = px.version.get()
        node_names = ', '.join(n['node'] for n in nodes)
        return True, f"Proxmox VE {version.get('version', '?')} — węzły: {node_names}"
    except Exception as e:
        return False, str(e)


def get_nodes():
    px = get_connection()
    return [n['node'] for n in px.nodes.get()]


def get_storages(node):
    px = get_connection()
    storages = px.nodes(node).storage.get()
    return [
        {'storage': s['storage'], 'type': s['type']}
        for s in storages
        if 'images' in s.get('content', '') or 'rootdir' in s.get('content', '')
    ]


def get_templates(node):
    """Zwraca listę dostępnych szablonów LXC ze wszystkich storage'ów."""
    px = get_connection()
    templates = []
    storages = px.nodes(node).storage.get()
    for s in storages:
        if 'vztmpl' not in s.get('content', ''):
            continue
        try:
            items = px.nodes(node).storage(s['storage']).content.get()
            for item in items:
                if item.get('content') == 'vztmpl':
                    templates.append({
                        'volid': item['volid'],
                        'name': item['volid'].split('/')[-1],
                        'storage': s['storage'],
                        'size': item.get('size', 0),
                    })
        except Exception:
            continue
    return templates


def get_bridges(node):
    """Zwraca listę interfejsów sieciowych (bridge) na węźle."""
    px = get_connection()
    ifaces = px.nodes(node).network.get()
    bridges = []
    for iface in ifaces:
        if iface.get('type') in ('bridge', 'bond', 'eth', 'vlan'):
            bridges.append({
                'iface': iface['iface'],
                'type': iface.get('type', ''),
                'address': iface.get('address', ''),
            })
    return bridges


def get_next_vmid():
    px = get_connection()
    return px.cluster.nextid.get()


def list_containers(node):
    px = get_connection()
    return px.nodes(node).lxc.get()


def get_container_status(node, vmid):
    px = get_connection()
    return px.nodes(node).lxc(vmid).status.current.get()


def create_lxc(node, params):
    """
    params: dict z kluczami:
        vmid, hostname, ostemplate, password,
        memory, swap, cores, rootfs (storage:size),
        net0 (name=eth0,bridge=vmbr0,ip=dhcp,...),
        start, unprivileged, nameserver
    """
    px = get_connection()
    task_id = px.nodes(node).lxc.post(**params)
    return task_id


def start_container(node, vmid):
    px = get_connection()
    return px.nodes(node).lxc(vmid).status.start.post()


def stop_container(node, vmid):
    px = get_connection()
    return px.nodes(node).lxc(vmid).status.stop.post()


def delete_container(node, vmid):
    px = get_connection()
    return px.nodes(node).lxc(vmid).delete()


def get_task_status(node, task_id):
    px = get_connection()
    return px.nodes(node).tasks(task_id).status.get()
