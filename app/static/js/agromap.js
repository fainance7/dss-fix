// Экранирование данных внешнего источника (Agromap) перед вставкой в HTML
function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const LAND_TYPES = [
    { key: 'пашня', label: 'Пашня', color: '#e8c547' },
    { key: 'залежь', label: 'Залежь', color: '#e08a3c' },
    { key: 'пастбище', label: 'Пастбище', color: '#a8c97f' },
    { key: 'сенокос', label: 'Сенокос', color: '#5d9c59' },
    { key: 'многолетние насаждения', label: 'Многолетние насаждения', color: '#2f6b3a' },
];

let activeTypes = new Set(LAND_TYPES.map(t => t.key)); // изначально все включены
let selectedDistrictId = null;
let selectedVillageId = null;

let currentUnit = 'ga'; // 'ga' | 'km2'

function formatArea(areaHa, precise = false) {
    if (areaHa == null) return '0';
    if (currentUnit === 'km2') {
        return (areaHa / 100).toLocaleString('ru-RU', { maximumFractionDigits: precise ? 4 : 1 }) + ' км²';
    }
    return areaHa.toLocaleString('ru-RU', { maximumFractionDigits: precise ? 2 : 0 }) + ' га';
}

function loadFiltersFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const layersParam = params.get('layers');
    if (layersParam) {
        const requested = layersParam.split(',').map(s => s.trim());
        const valid = requested.filter(t => LAND_TYPES.some(lt => lt.key === t));
        if (valid.length > 0) {
            activeTypes = new Set(valid);
        }
    }
}

function updateUrlFromFilters() {
    const params = new URLSearchParams(window.location.search);
    if (activeTypes.size === LAND_TYPES.length) {
        // Все выбраны — это состояние по умолчанию, не засоряем URL
        params.delete('layers');
    } else {
        params.set('layers', Array.from(activeTypes).join(','));
    }
    const newUrl = `${window.location.pathname}?${params}`.replace(/\?$/, '');
    history.replaceState(null, '', newUrl);
}

loadFiltersFromUrl();

const map = new maplibregl.Map({
    container: 'agromap-map',
    style: {
        version: 8,
        sources: {},
        layers: [
            {
                id: 'background',
                type: 'background',
                paint: { 'background-color': '#f0ede5' }
            }
        ]
    },
    center: [68.5, 54.0],
    zoom: 7,
});

map.addControl(new maplibregl.NavigationControl(), 'top-right');

map.on('load', () => {
    console.log('Карта загружена, готова к добавлению слоя тайлов');

    map.addSource('land-polygons', {
        type: 'vector',
        tiles: [window.location.origin + '/agromap/tiles/{z}/{x}/{y}.pbf?region=sko'],
        minzoom: 6,
        maxzoom: 16,
    });

    map.addLayer({
        id: 'land-polygons-fill',
        type: 'fill',
        source: 'land-polygons',
        'source-layer': 'land_polygons',
        paint: {
            'fill-color': [
                'match',
                ['get', 'object_ru'],
                ...LAND_TYPES.flatMap(t => [t.key, t.color]),
                '#cccccc'
            ],
            'fill-opacity': [
                'interpolate', ['linear'], ['zoom'],
                6, 0.85,
                10, 0.7,
                14, 0.6
            ],
        },
    });

    map.addLayer({
        id: 'land-polygons-outline',
        type: 'line',
        source: 'land-polygons',
        'source-layer': 'land_polygons',
        paint: {
            'line-color': '#555555',
            'line-width': [
                'interpolate', ['linear'], ['zoom'],
                6, 0,
                10, 0.3,
                14, 1
            ],
        },
    });

    console.log('Слой полигонов добавлен');
    applyTypeFilter();
});

async function updateStats() {
    const bounds = map.getBounds();
    const params = new URLSearchParams({
        minx: bounds.getWest(),
        miny: bounds.getSouth(),
        maxx: bounds.getEast(),
        maxy: bounds.getNorth(),
        region: 'sko',
    });
    if (activeTypes.size > 0 && activeTypes.size < LAND_TYPES.length) {
        params.set('object_ru', currentTypesParam());
    }
    if (selectedDistrictId != null) params.set('district_id', selectedDistrictId);
    if (selectedVillageId != null) params.set('village_id', selectedVillageId);
    if (areaFilter.min != null) params.set('area_min', areaFilter.min);
    if (areaFilter.max != null) params.set('area_max', areaFilter.max);

    const statsEl = document.getElementById('agromap-stats');
    statsEl.textContent = 'Загрузка...';

    try {
        const resp = await fetch(`/agromap/stats?${params}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        const rows = data.by_type.map(t =>
            `<div style="display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid #eee;">
                <span>${escapeHtml(t.object_ru)}</span>
                <span>${formatArea(t.area_ha)}</span>
            </div>`
        ).join('');

        statsEl.innerHTML = `
            <div style="margin-bottom:12px;">
                <strong>Всего:</strong> ${formatArea(data.total_area_ha)}
                (${data.total_count.toLocaleString('ru-RU')} уч.)
            </div>
            ${rows}
        `;
    } catch (err) {
        statsEl.textContent = 'Ошибка загрузки статистики: ' + err.message;
        console.error('Stats fetch error:', err);
    }
}

map.on('load', () => { updateStats(); updateLayerCounts(); });
map.on('moveend', () => { updateStats(); updateLayerCounts(); });

map.on('click', 'land-polygons-fill', async (e) => {
    if (!e.features || e.features.length === 0) return;

    const polygonId = e.features[0].properties.id;

    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '300px' })
        .setLngLat(e.lngLat)
        .setHTML('Загрузка...')
        .addTo(map);

    try {
        const resp = await fetch(`/agromap/polygon/${polygonId}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        const dash = (v) => (v === null || v === undefined || v === '') ? '—' : escapeHtml(v);

        popup.setHTML(`
            <div style="font-size: 13px; line-height: 1.5;">
                <strong>Тип угодья:</strong> ${dash(data.object_ru)}<br>
                <strong>Площадь:</strong> ${data.area_ha != null ? formatArea(data.area_ha) : '—'}<br>
                <strong>Почва:</strong> ${dash(data.ground_ru)}<br>
                <strong>Растительность:</strong> ${dash(data.vegetation_ru)}<br>
                <strong>Орошение:</strong> ${dash(data.water_ru)}<br>
                <strong>Расположение:</strong> ${dash(data.location_name)}<br>
                <strong>Код:</strong> ${dash(data.code)}
            </div>
        `);
    } catch (err) {
        popup.setHTML('Ошибка загрузки данных: ' + escapeHtml(err.message));
        console.error('Popup fetch error:', err);
    }
});

// Курсор-указатель при наведении на полигон — визуальная подсказка, что можно кликнуть
map.on('mouseenter', 'land-polygons-fill', () => {
    map.getCanvas().style.cursor = 'pointer';
});
map.on('mouseleave', 'land-polygons-fill', () => {
    map.getCanvas().style.cursor = '';
});

function renderLayerCheckboxes() {
    const container = document.getElementById('agromap-layers');
    container.innerHTML = LAND_TYPES.map(t => `
        <label style="display:flex; align-items:center; gap:8px; padding:4px 0; cursor:pointer;">
            <input type="checkbox" data-type="${t.key}" ${activeTypes.has(t.key) ? 'checked' : ''}>
            <span style="width:12px; height:12px; background:${t.color}; display:inline-block; border-radius:2px;"></span>
            <span style="flex:1;">${t.label}</span>
            <span class="layer-count" data-type="${t.key}" style="color:#888; font-size:12px;">—</span>
        </label>
    `).join('') + `
        <div style="margin-top:8px; display:flex; gap:8px;">
            <button id="select-all-types" class="btn-link">Выбрать все</button>
            <button id="deselect-all-types" class="btn-link">Снять все</button>
        </div>
    `;

    container.querySelectorAll('input[type=checkbox]').forEach(cb => {
        cb.addEventListener('change', () => {
            if (cb.checked) activeTypes.add(cb.dataset.type);
            else activeTypes.delete(cb.dataset.type);
            applyTypeFilter();
        });
    });

    document.getElementById('select-all-types').addEventListener('click', () => {
        activeTypes = new Set(LAND_TYPES.map(t => t.key));
        container.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = true);
        applyTypeFilter();
    });

    document.getElementById('deselect-all-types').addEventListener('click', () => {
        activeTypes = new Set();
        container.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = false);
        applyTypeFilter();
    });
}

async function updateLayerCounts() {
    // Отдельный запрос БЕЗ object_ru — нужны площади по ВСЕМ типам в текущем
    // вьюпорте, включая снятые с показа галочкой, иначе непонятно, сколько
    // скрыто при снятой галочке.
    const bounds = map.getBounds();
    const params = new URLSearchParams({
        minx: bounds.getWest(),
        miny: bounds.getSouth(),
        maxx: bounds.getEast(),
        maxy: bounds.getNorth(),
        region: 'sko',
    });

    try {
        const resp = await fetch(`/agromap/stats?${params}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        const byType = {};
        data.by_type.forEach(t => { byType[t.object_ru] = t.area_ha; });

        document.querySelectorAll('.layer-count').forEach(el => {
            const ha = byType[el.dataset.type];
            el.textContent = formatArea(ha);
        });
    } catch (err) {
        console.error('Layer counts fetch error:', err);
    }
}

function currentTypesParam() {
    // Пустой список типов = "показать все" на бэкенде (object_ru не передаётся),
    // поэтому при полностью снятых галочках передаём заведомо несуществующий тип,
    // чтобы карта корректно показала "ничего", а не "всё" по умолчанию.
    if (activeTypes.size === 0) return '__none__';
    return Array.from(activeTypes).join(',');
}

function buildMapFilter() {
    const types = Array.from(activeTypes);
    const conditions = ['all'];

    if (types.length === 0) {
        conditions.push(['==', ['get', 'object_ru'], '__none__']);
    } else {
        conditions.push(['in', ['get', 'object_ru'], ['literal', types]]);
    }

    if (selectedDistrictId != null) {
        conditions.push(['==', ['get', 'district_id'], selectedDistrictId]);
    }

    if (selectedVillageId != null) {
        conditions.push(['==', ['get', 'village_id'], selectedVillageId]);
    }

    if (selectedVillageId != null) {
        conditions.push(['==', ['get', 'village_id'], selectedVillageId]);
    }
    if (areaFilter.min != null) {
        conditions.push(['>=', ['to-number', ['get', 'area_ha']], areaFilter.min]);
    }
    if (areaFilter.max != null) {
        conditions.push(['<=', ['to-number', ['get', 'area_ha']], areaFilter.max]);
    }

    return conditions;
}

function applyTypeFilter() {
    const filter = buildMapFilter();
    map.setFilter('land-polygons-fill', filter);
    map.setFilter('land-polygons-outline', filter);
    updateStats();
}

renderLayerCheckboxes();

function buildExportUrl(format) {
    const bounds = map.getBounds();
    const params = new URLSearchParams({
        format: format,
        region: 'sko',
        bbox: `${bounds.getWest()},${bounds.getSouth()},${bounds.getEast()},${bounds.getNorth()}`,
    });
    if (activeTypes.size > 0 && activeTypes.size < LAND_TYPES.length) {
        params.set('object_ru', currentTypesParam());
    }
    return `/agromap/export?${params}`;
}

function renderExportButtons() {
    const container = document.getElementById('agromap-layers');
    container.insertAdjacentHTML('beforeend', `
        <div style="margin-top:12px; padding-top:12px; border-top:1px solid #eee;">
            <div style="font-size:12px; color:#888; margin-bottom:6px;">Скачать видимую область:</div>
            <div style="display:flex; gap:6px;">
                <a href="#" data-format="geojson" class="btn-link export-link">GeoJSON</a>
                <a href="#" data-format="csv" class="btn-link export-link">CSV</a>
                <a href="#" data-format="xlsx" class="btn-link export-link">Excel</a>
            </div>
        </div>
    `);

    container.querySelectorAll('.export-link').forEach(link => {
    link.addEventListener('click', async (e) => {
        e.preventDefault();
        const url = buildExportUrl(link.dataset.format);

        try {
            const resp = await fetch(url);
            if (!resp.ok) {
                const err = await resp.json();
                alert(err.detail || `Ошибка экспорта (HTTP ${resp.status})`);
                return;
            }
            // Успех — превращаем ответ в скачиваемый файл через blob
            const blob = await resp.blob();
            const disposition = resp.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="(.+?)"/);
            const filename = match ? match[1] : `agromap_export.${link.dataset.format}`;

            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(blobUrl);
        } catch (err) {
            alert('Не удалось выполнить экспорт: ' + err.message);
            console.error('Export error:', err);
        }
    });
});
}

renderExportButtons();

document.getElementById('unit-toggle').addEventListener('change', (e) => {
    currentUnit = e.target.checked ? 'km2' : 'ga';
    updateStats();
    updateLayerCounts();
});

let locationFilter = { district: '', village: '' };
let areaFilter = { min: null, max: null };

async function initLocationFilters() {
    try {
        const resp = await fetch('/agromap/locations?region=sko');
        const data = await resp.json();

        const districtSelect = document.getElementById('district-select');
        data.districts.forEach(d => {
            const opt = document.createElement('option');
            opt.value = d.id;
            opt.textContent = d.name;
            districtSelect.appendChild(opt);
        });

        districtSelect.addEventListener('change', () => {
            const villageSelect = document.getElementById('village-select');
            villageSelect.innerHTML = '<option value="">Все округа</option>';

            const selected = data.districts.find(d => String(d.id) === districtSelect.value);
            if (selected) {
                selected.villages.forEach(v => {
                    const opt = document.createElement('option');
                    opt.value = v.id;
                    opt.textContent = v.name;
                    villageSelect.appendChild(opt);
                });
                villageSelect.disabled = false;
            } else {
                villageSelect.disabled = true;
            }
        });
    } catch (err) {
        console.error('Locations fetch error:', err);
    }
}

document.getElementById('area-min-slider').addEventListener('input', (e) => {
    document.getElementById('area-min-label').textContent = e.target.value;
});
document.getElementById('area-max-slider').addEventListener('input', (e) => {
    const val = e.target.value;
    document.getElementById('area-max-label').textContent = val >= 1000 ? '1000+' : val;
});

document.getElementById('apply-location-filter').addEventListener('click', () => {
    const districtVal = document.getElementById('district-select').value;
    selectedDistrictId = districtVal ? parseInt(districtVal) : null;
    const villageVal = document.getElementById('village-select').value;
    selectedVillageId = villageVal ? parseInt(villageVal) : null;
    const minVal = parseInt(document.getElementById('area-min-slider').value);
    const maxVal = parseInt(document.getElementById('area-max-slider').value);
    areaFilter.min = minVal > 0 ? minVal : null;
    areaFilter.max = maxVal < 1000 ? maxVal : null;

    applyTypeFilter();
});

document.getElementById('reset-location-filter').addEventListener('click', () => {
    document.getElementById('district-select').value = '';
    document.getElementById('village-select').value = '';
    document.getElementById('village-select').disabled = true;
    document.getElementById('area-min-slider').value = 0;
    document.getElementById('area-max-slider').value = 1000;
    document.getElementById('area-min-label').textContent = '0';
    document.getElementById('area-max-label').textContent = '1000+';
    selectedDistrictId = null;
    selectedVillageId = null;
    areaFilter = { min: null, max: null };
    applyTypeFilter();
});

initLocationFilters();
