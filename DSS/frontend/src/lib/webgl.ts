// Утилиты проверки доступности WebGL для карт (MapLibre GL требует WebGL).
// Если в браузере отключено аппаратное ускорение/WebGL, MapLibre выбрасывает
// «webglcontextcreationerror» с техническим JSON — вместо него показываем
// понятное пользователю сообщение с инструкцией.

export const WEBGL_ERROR_MESSAGE =
  'Карта недоступна: в браузере не удалось инициализировать WebGL. Обычно это значит, ' +
  'что отключено аппаратное ускорение. Включите его (Chrome: Настройки → Система → ' +
  '«Использовать аппаратное ускорение, если доступно», затем перезапустите браузер; ' +
  'проверка состояния — на странице chrome://gpu) и обновите страницу. ' +
  'Также помогает обновление драйверов видеокарты или включение WebGL в настройках браузера.'

/** Доступен ли в текущем окружении контекст WebGL. На сервере (SSR) — true (не блокируем). */
export function isWebGLAvailable(): boolean {
  if (typeof document === 'undefined') return true
  try {
    const canvas = document.createElement('canvas')
    const gl =
      canvas.getContext('webgl2') ||
      canvas.getContext('webgl') ||
      canvas.getContext('experimental-webgl')
    return Boolean(gl)
  } catch {
    return false
  }
}

/** Похоже ли сообщение об ошибке на сбой создания WebGL-контекста. */
export function isWebGLError(message: string | null | undefined): boolean {
  if (!message) return false
  const m = message.toLowerCase()
  return (
    m.includes('webgl') ||
    m.includes('webglcontextcreationerror') ||
    m.includes('could not create') ||
    m.includes('gl_renderer')
  )
}

/** Преобразует техническое сообщение карты в понятное: WebGL-сбои → подсказка. */
export function toFriendlyMapError(message: string | null | undefined): string {
  return isWebGLError(message) ? WEBGL_ERROR_MESSAGE : (message ?? 'Карта не загрузилась')
}
