function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta) return meta.getAttribute('content');
  const input = document.querySelector('input[name="csrf_token"]');
  return input ? input.value : '';
}

function showToast(message, type = 'info') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position:fixed;top:1rem;right:1rem;z-index:9999;min-width:260px;';
    document.body.appendChild(container);
  }

  const icons = {
    success: 'bi-check-circle-fill',
    danger:  'bi-exclamation-circle-fill',
    warning: 'bi-exclamation-triangle-fill',
    info:    'bi-info-circle-fill',
  };

  const toast = document.createElement('div');
  toast.className = `alert alert-${type} alert-dismissible shadow-sm d-flex align-items-center gap-2 mb-2`;
  toast.style.fontSize = '0.87rem';
  toast.innerHTML = `
    <i class="bi ${icons[type] || icons.info}"></i>
    <span>${message}</span>
    <button type="button" class="btn-close ms-auto" onclick="this.parentElement.remove()"></button>
  `;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}
