'use client'

import { useState, useEffect } from 'react'
import { useAuthStore } from '@/store/authStore'
import api from '@/lib/api'

interface DataSource {
  id: string
  name: string
  url: string
  api_key: string
  refresh_interval_hours: number
  enabled: boolean
  last_check?: string
  status?: 'ok' | 'error' | 'unknown'
}

interface BackupSettings {
  schedule_cron: string
  retention_days: number
  last_backup?: string
}

// Фактическое состояние резервного копирования (GET /settings/backup/status).
interface BackupStatus {
  archive_mode: string
  wal_archived_count: number | null
  wal_last_archived_at: string | null
  wal_failed_count: number | null
  wal_last_failed_at: string | null
  base_backups: number
  base_last_at: string | null
  base_age_hours: number | null
  base_ok: boolean
  schedule: string
  basebackup_dir_mounted: boolean
}

interface DataQualitySettings {
  level_break_ratio: number
  widespread_break_share: number
  unit_shift_factor: number
  magnitude_factor: number
  district_sum_tolerance: number
  incomplete_year_ratio: number
  incomplete_year_share: number
  changed_tolerance: number
  dekad_temp_min: number
  dekad_temp_max: number
  dekad_precip_max: number
  dekad_anomaly_sigma: number
}

interface SystemSettings {
  data_sources: DataSource[]
  backup: BackupSettings
  session_timeout_minutes: number
  data_quality: DataQualitySettings
}

const DEFAULT_DATA_QUALITY: DataQualitySettings = {
  level_break_ratio: 2.5,
  widespread_break_share: 0.5,
  unit_shift_factor: 8,
  magnitude_factor: 8,
  district_sum_tolerance: 0.05,
  incomplete_year_ratio: 0.6,
  incomplete_year_share: 0.3,
  changed_tolerance: 0.001,
  dekad_temp_min: -50,
  dekad_temp_max: 45,
  dekad_precip_max: 200,
  dekad_anomaly_sigma: 4,
}

// Пороги контроля качества партий приёма данных: подпись, подсказка, шаг поля.
const DATA_QUALITY_FIELDS: { key: keyof DataQualitySettings; label: string; hint: string; step: number }[] = [
  { key: 'level_break_ratio', label: 'Обрыв ряда, раз', hint: 'Скачок к предыдущему году больше этого отношения', step: 0.1 },
  { key: 'widespread_break_share', label: 'Обрыв по районам, доля', hint: 'Доля районов с обрывом в одном году — «смена методики»', step: 0.05 },
  { key: 'unit_shift_factor', label: 'Смена единиц, раз', hint: 'Медианное отношение новых значений к текущим', step: 1 },
  { key: 'magnitude_factor', label: 'Значение от ряда, раз', hint: 'Отличие от медианы ряда показателя по региону', step: 1 },
  { key: 'district_sum_tolerance', label: 'Сумма районов, допуск', hint: 'Допустимое расхождение суммы районов с областью (0,05 = 5 %)', step: 0.01 },
  { key: 'incomplete_year_ratio', label: 'Неполный год, отношение', hint: 'Последний год ниже предыдущего в такое число раз', step: 0.05 },
  { key: 'incomplete_year_share', label: 'Неполный год, доля показателей', hint: 'Доля показателей с таким снижением', step: 0.05 },
  { key: 'changed_tolerance', label: 'Изменение значения, допуск', hint: 'Относительное отличие, которое считается изменением', step: 0.001 },
  { key: 'dekad_temp_min', label: 'Температура декады, минимум, °C', hint: 'Ниже — блокер (ошибка ввода или единиц)', step: 1 },
  { key: 'dekad_temp_max', label: 'Температура декады, максимум, °C', hint: 'Выше — блокер', step: 1 },
  { key: 'dekad_precip_max', label: 'Осадки за декаду, максимум, мм', hint: 'Больше — предупреждение', step: 10 },
  { key: 'dekad_anomaly_sigma', label: 'Аномалия температуры декады, σ', hint: 'Отклонение от многолетнего среднего той же декады станции', step: 0.5 },
]

const DEFAULT_SETTINGS: SystemSettings = {
  data_sources: [
    { id: 'ais_gzk', name: 'АИС ГЗК (SmartBridge)', url: '', api_key: '', refresh_interval_hours: 24, enabled: false },
    { id: 'taldau', name: 'БНС (Taldau)', url: 'https://taldau.stat.gov.kz/api', api_key: '', refresh_interval_hours: 168, enabled: false },
    { id: 'kazhydromet', name: 'Казгидромет', url: '', api_key: '', refresh_interval_hours: 6, enabled: false },
    { id: 'kazvodkhoz', name: 'Казводхоз', url: '', api_key: '', refresh_interval_hours: 24, enabled: false },
  ],
  backup: {
    schedule_cron: '0 3 * * *',
    retention_days: 30,
  },
  session_timeout_minutes: 60,
  data_quality: DEFAULT_DATA_QUALITY,
}

export default function SettingsPage() {
  const { hasRole } = useAuthStore()
  const [settings, setSettings] = useState<SystemSettings>(DEFAULT_SETTINGS)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, 'ok' | 'error'>>({})
  const [backup, setBackup] = useState<BackupStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // baseURL клиента уже оканчивается на /api/v1
    api.get<SystemSettings>('/settings')
      .then(({ data }) => setSettings({ ...data, data_quality: { ...DEFAULT_DATA_QUALITY, ...(data.data_quality ?? {}) } }))
      .catch(() => setError('Не удалось загрузить настройки — показаны значения по умолчанию, сохранение перезапишет их.'))
    api.get<BackupStatus>('/settings/backup/status')
      .then(({ data }) => setBackup(data))
      .catch(() => setBackup(null))
  }, [])

  if (!hasRole('admin')) {
    return (
      <div className="flex h-64 items-center justify-center text-gray-400">
        Доступ только для администраторов
      </div>
    )
  }

  function updateSource(id: string, field: keyof DataSource, value: string | number | boolean) {
    setSettings((prev) => ({
      ...prev,
      data_sources: prev.data_sources.map((s) => s.id === id ? { ...s, [field]: value } : s),
    }))
  }

  async function handleSave() {
    setSaving(true)
    setSaved(false)
    try {
      await api.put('/settings', settings)
      setError(null)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      setError('Не удалось сохранить настройки.')
    } finally {
      setSaving(false)
    }
  }

  async function handleTestSource(id: string) {
    setTesting(id)
    try {
      await api.post(`/settings/test-source/${id}`)
      setTestResults((prev) => ({ ...prev, [id]: 'ok' }))
    } catch {
      setTestResults((prev) => ({ ...prev, [id]: 'error' }))
    } finally {
      setTesting(null)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Системные настройки</h1>
        <p className="mt-0.5 text-sm text-gray-500">Конфигурация интеграций, резервного копирования и параметров сессии</p>
        {error && (
          <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
        )}
      </div>

      {/* External data sources */}
      <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
        <div className="border-b border-gray-100 px-6 py-4">
          <h2 className="font-medium text-gray-900">Реестр источников данных</h2>
          <p className="text-xs text-gray-500 mt-0.5">Внешние API для автоматической загрузки данных</p>
        </div>
        <div className="divide-y divide-gray-50 px-6">
          {settings.data_sources.map((src) => {
            const testResult = testResults[src.id]
            return (
              <div key={src.id} className="py-5 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <label className="relative inline-flex cursor-pointer items-center">
                      <input
                        type="checkbox"
                        checked={src.enabled}
                        onChange={(e) => updateSource(src.id, 'enabled', e.target.checked)}
                        className="sr-only peer"
                      />
                      <div className="h-5 w-9 rounded-full bg-gray-200 peer-checked:bg-primary-500 transition-colors after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow after:transition-transform peer-checked:after:translate-x-4" />
                    </label>
                    <span className="font-medium text-gray-800 text-sm">{src.name}</span>
                  </div>
                  <button
                    onClick={() => handleTestSource(src.id)}
                    disabled={testing === src.id || !src.enabled}
                    className="rounded-lg border border-gray-200 px-3 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {testing === src.id ? 'Проверяю...' : 'Проверить соединение'}
                  </button>
                </div>

                {testResult && (
                  <div className={`rounded-md px-3 py-1.5 text-xs ${testResult === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                    {testResult === 'ok' ? '✓ Соединение установлено' : '✗ Источник недоступен'}
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1 block text-xs font-medium text-gray-600">URL</label>
                    <input
                      type="url"
                      value={src.url}
                      onChange={(e) => updateSource(src.id, 'url', e.target.value)}
                      placeholder="https://..."
                      className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium text-gray-600">Ключ доступа (API key)</label>
                    <input
                      type="password"
                      value={src.api_key}
                      onChange={(e) => updateSource(src.id, 'api_key', e.target.value)}
                      placeholder="••••••••"
                      className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                    />
                  </div>
                </div>

                <div>
                  <label className="mb-1 block text-xs font-medium text-gray-600">
                    Интервал обновления: {src.refresh_interval_hours} ч
                  </label>
                  <input
                    type="range"
                    min={1}
                    max={168}
                    step={1}
                    value={src.refresh_interval_hours}
                    onChange={(e) => updateSource(src.id, 'refresh_interval_hours', Number(e.target.value))}
                    className="w-full accent-primary-500"
                  />
                  <div className="flex justify-between text-xs text-gray-400 mt-0.5">
                    <span>1 ч</span>
                    <span>168 ч (неделя)</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Backup settings */}
      <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
        <div className="border-b border-gray-100 px-6 py-4">
          <h2 className="font-medium text-gray-900">Резервное копирование</h2>
        </div>
        <div className="px-6 py-5 space-y-3 text-sm">
          {!backup ? (
            <p className="text-gray-500">Состояние резервного копирования недоступно.</p>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <span className="text-gray-700">Базовая копия базы данных</span>
                <span className={backup.base_ok ? 'text-green-700' : 'text-red-700'}>
                  {backup.base_last_at
                    ? `${new Date(backup.base_last_at).toLocaleString('ru-RU')} (${backup.base_age_hours} ч назад)`
                    : backup.basebackup_dir_mounted ? 'копий нет' : 'каталог копий не подключён'}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-700">Архив журналов транзакций (восстановление на момент времени)</span>
                <span className={backup.archive_mode === 'on' && !backup.wal_failed_count ? 'text-green-700' : 'text-red-700'}>
                  {backup.archive_mode === 'on'
                    ? `включён, последний файл ${backup.wal_last_archived_at ? new Date(backup.wal_last_archived_at).toLocaleString('ru-RU') : '—'}, ошибок ${backup.wal_failed_count ?? 0}`
                    : 'выключен'}
                </span>
              </div>
              <p className="text-xs text-gray-500">
                Копирование выполняет сервер: {backup.schedule}. Хранится копий: {backup.base_backups}.
              </p>
            </>
          )}
        </div>
      </section>

      {/* Session settings */}
      <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
        <div className="border-b border-gray-100 px-6 py-4">
          <h2 className="font-medium text-gray-900">Сессия</h2>
        </div>
        <div className="px-6 py-5">
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Таймаут сессии: {settings.session_timeout_minutes} мин
          </label>
          <input
            type="range"
            min={15}
            max={480}
            step={15}
            value={settings.session_timeout_minutes}
            onChange={(e) => setSettings((prev) => ({ ...prev, session_timeout_minutes: Number(e.target.value) }))}
            className="w-full accent-primary-500"
          />
          <div className="flex justify-between text-xs text-gray-400 mt-0.5">
            <span>15 мин</span>
            <span>8 часов</span>
          </div>
        </div>
      </section>

      {/* Data quality thresholds */}
      <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
        <div className="border-b border-gray-100 px-6 py-4">
          <h2 className="font-medium text-gray-900">Контроль качества данных</h2>
          <p className="mt-0.5 text-xs text-gray-500">
            Пороги проверок партий приёма данных перед импортом. Изменения применяются к следующей проверке партии.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-4 px-6 py-5 md:grid-cols-2">
          {DATA_QUALITY_FIELDS.map((f) => (
            <div key={f.key}>
              <label className="mb-1 block text-sm font-medium text-gray-700">{f.label}</label>
              <input
                type="number"
                min={0}
                step={f.step}
                value={settings.data_quality[f.key]}
                onChange={(e) =>
                  setSettings((prev) => ({
                    ...prev,
                    data_quality: { ...prev.data_quality, [f.key]: Number(e.target.value) },
                  }))
                }
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
              <p className="mt-1 text-xs text-gray-400">{f.hint}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Save */}
      <div className="flex items-center justify-end gap-3">
        {saved && (
          <span className="text-sm text-green-600">Настройки сохранены</span>
        )}
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-lg bg-primary-600 px-6 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-60"
        >
          {saving ? 'Сохраняю...' : 'Сохранить настройки'}
        </button>
      </div>
    </div>
  )
}
