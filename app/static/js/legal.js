document.addEventListener('DOMContentLoaded', function () {
    const input = document.getElementById('legal-query');
    const btn = document.getElementById('legal-search-btn');
    const statusEl = document.getElementById('legal-status');
    const resultsEl = document.getElementById('legal-results');

    const STATUS_LABELS = {
        ACTIVE: 'Действующая',
        INACTIVE: 'Бездействующая',
        LIQUIDATING: 'Ликвидируется',
        LIQUIDATED: 'Ликвидирована',
        SUSPENDED: 'Деятельность приостановлена',
    };

    async function search() {
        const q = input.value.trim();
        if (q.length < 2) {
            statusEl.textContent = 'Введите минимум 2 символа';
            return;
        }
        statusEl.textContent = 'Поиск...';
        resultsEl.innerHTML = '';
        try {
            const resp = await fetch('/legal/search?q=' + encodeURIComponent(q));
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                statusEl.textContent = 'Ошибка: ' + (err.detail || resp.status);
                return;
            }
            const data = await resp.json();
            statusEl.textContent = 'Найдено: ' + data.count;
            if (!data.count) {
                resultsEl.innerHTML = '<div style="color:var(--text-muted);padding:12px">Ничего не найдено</div>';
                return;
            }
            resultsEl.innerHTML = data.results.map(renderCard).join('');
        } catch (e) {
            statusEl.textContent = 'Ошибка сети: ' + e;
        }
    }

    function esc(v) {
        return String(v ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    function renderCard(c) {
        const status = STATUS_LABELS[c.status] || c.status || '—';
        return '<div style="border:1px solid var(--border-color,#eee);border-radius:8px;padding:14px;margin-bottom:10px">'
            + '<div style="font-weight:600;margin-bottom:6px">' + esc(c.name_ru || '—') + '</div>'
            + '<div style="font-size:13px;line-height:1.8;color:var(--text-secondary,#555)">'
            + 'БИН: <strong>' + esc(c.bin || '—') + '</strong><br>'
            + 'Статус: ' + esc(status) + '<br>'
            + 'ОКЭД: ' + esc(c.oked || '—') + (c.oked_name_ru ? ' — ' + esc(c.oked_name_ru) : '') + '<br>'
            + 'Размерность: ' + esc(c.krp_name_ru || '—') + '<br>'
            + 'Руководитель: ' + esc(c.fio || '—') + '<br>'
            + 'Адрес: ' + esc(c.address_ru || '—')
            + '</div></div>';
    }

    btn.addEventListener('click', search);
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') search();
    });
});
