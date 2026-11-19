// upload.js — Общие утилиты для ETL Upload Interface

function formatDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU') + ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function formatNumber(n) {
    if (!n && n !== 0) return '—';
    return n.toLocaleString('ru-RU');
}

function showToast(message, type = 'info') {
    const colors = { info: 'var(--accent-blue)', success: 'var(--accent-green)', error: 'var(--accent-red)', warning: 'var(--accent-amber)' };
    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed; bottom: 24px; right: 24px; z-index: 9999;
        background: var(--bg-card); border: 1px solid ${colors[type]};
        border-left: 3px solid ${colors[type]};
        color: var(--text-primary); padding: 12px 18px;
        border-radius: 8px; font-size: 13px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        max-width: 320px; line-height: 1.4;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; }, 3000);
    setTimeout(() => toast.remove(), 3400);
}
