function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.content;
    const input = document.querySelector('input[name="csrf_token"]');
    return input ? input.value : '';
}

function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer') || createToastContainer();
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0 show mb-2`;
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" onclick="this.closest('.toast').remove()"></button>
        </div>`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

function createToastContainer() {
    const el = document.createElement('div');
    el.id = 'toastContainer';
    el.style.cssText = 'position:fixed;top:1rem;right:1rem;z-index:9999;min-width:280px';
    document.body.appendChild(el);
    return el;
}

// ---------------------------------------------------------------------------
// Ładowanie danych z Proxmox (AJAX)
// ---------------------------------------------------------------------------

function loadTemplates() {
    const select = document.getElementById('templateSelect');
    if (!select) return;

    fetch('/api/templates')
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                select.innerHTML = `<option value="">Błąd: ${data.error}</option>`;
                return;
            }
            if (data.length === 0) {
                select.innerHTML = '<option value="">Brak dostępnych szablonów</option>';
                return;
            }
            select.innerHTML = '<option value="">— Wybierz szablon —</option>' +
                data.map(t => `<option value="${t.volid}">${t.name}</option>`).join('');
        })
        .catch(err => {
            select.innerHTML = `<option value="">Błąd pobierania szablonów</option>`;
            console.error(err);
        });
}

function loadBridges() {
    const select = document.getElementById('bridgeSelect');
    if (!select) return;

    fetch('/api/bridges')
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                select.innerHTML = `<option value="">Błąd: ${data.error}</option>`;
                return;
            }
            if (data.length === 0) {
                select.innerHTML = '<option value="vmbr0">vmbr0 (domyślny)</option>';
                return;
            }
            select.innerHTML = data.map(b =>
                `<option value="${b.iface}">${b.iface} (${b.type}${b.address ? ' — ' + b.address : ''})</option>`
            ).join('');
        })
        .catch(err => {
            select.innerHTML = '<option value="vmbr0">vmbr0 (domyślny)</option>';
            console.error(err);
        });
}

function loadStorages() {
    const select = document.getElementById('storageSelect');
    if (!select) return;

    fetch('/api/storages')
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                select.innerHTML = `<option value="local">local (domyślny)</option>`;
                return;
            }
            if (data.length === 0) {
                select.innerHTML = '<option value="local">local (domyślny)</option>';
                return;
            }
            select.innerHTML = data.map(s =>
                `<option value="${s.storage}">${s.storage} (${s.type})</option>`
            ).join('');
        })
        .catch(err => {
            select.innerHTML = '<option value="local">local (domyślny)</option>';
            console.error(err);
        });
}

function loadNextVmid() {
    const input = document.getElementById('vmidInput');
    if (!input) return;

    fetch('/api/next-vmid')
        .then(r => r.json())
        .then(data => {
            if (!data.error) {
                input.placeholder = `auto (${data.vmid})`;
                input.dataset.nextVmid = data.vmid;
            }
        })
        .catch(() => {});
}

// ---------------------------------------------------------------------------
// Akcje na kontenerach (dashboard)
// ---------------------------------------------------------------------------

function containerAction(vmid, action) {
    const label = action === 'start' ? 'Uruchamianie' : 'Zatrzymywanie';
    showToast(`${label} kontenera ${vmid}...`, 'primary');

    fetch(`/api/containers/${vmid}/${action}`, {
        method: 'POST',
        headers: {'X-CSRFToken': getCsrfToken(), 'Content-Type': 'application/json'},
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            showToast(`Błąd: ${data.error}`, 'danger');
        } else {
            showToast(`Operacja wykonana. Task: ${data.task}`, 'success');
            setTimeout(() => window.location.reload(), 2500);
        }
    })
    .catch(err => showToast(`Błąd sieci: ${err}`, 'danger'));
}

let pendingDeleteVmid = null;

function deleteContainer(vmid, name) {
    pendingDeleteVmid = vmid;
    document.getElementById('deleteContainerName').textContent = `${name} (VMID ${vmid})`;
    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();

    document.getElementById('confirmDeleteBtn').onclick = function () {
        modal.hide();
        fetch(`/api/containers/${pendingDeleteVmid}`, {
            method: 'DELETE',
            headers: {'X-CSRFToken': getCsrfToken(), 'Content-Type': 'application/json'},
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                showToast(`Błąd usuwania: ${data.error}`, 'danger');
            } else {
                showToast(`Kontener ${name} usunięty.`, 'success');
                const row = document.getElementById(`row-${pendingDeleteVmid}`);
                if (row) row.remove();
            }
        })
        .catch(err => showToast(`Błąd sieci: ${err}`, 'danger'));
    };
}
