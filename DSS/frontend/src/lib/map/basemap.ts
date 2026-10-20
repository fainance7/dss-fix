// Общая базовая подложка карт.
//
// Растровая OSM-подложка «впечатывает» госграницу РК в тайлы, и она расходится с
// официальной казахстанской оцифровкой районов (слой районов из шейпфайла) — на
// границе РК видно рассогласование. Растр границу скрыть не даёт, поэтому берём
// векторную подложку OpenFreeMap (бесплатно, без API-ключа, схема OpenMapTiles) и
// удаляем слои административных границ — официальная граница районов остаётся
// единственной линией. Бонус: векторный стиль содержит glyphs, поэтому подписи
// районов (symbol-слой) корректно рендерятся.

import type { Map as MapType } from 'maplibre-gl'

/** Стиль векторной подложки (без ключа). Изолировано здесь — замена тривиальна. */
export const BASEMAP_STYLE = 'https://tiles.openfreemap.org/styles/positron'

/**
 * Шрифт подписей наших symbol-слоёв. Без явного text-font MapLibre запрашивает
 * «Open Sans Regular, Arial Unicode MS Regular», которых на сервере глифов
 * подложки нет (404) — подписи районов не рисуются. У OpenFreeMap — Noto Sans.
 */
export const LABEL_FONT = ['Noto Sans Regular']

/**
 * Удаляет со стиля подложки слои гос-/админграниц, чтобы они не конфликтовали с
 * официальной границей районов. Вызывать в обработчике map 'load' (стиль уже
 * загружен). Идемпотентно и безопасно при отсутствии таких слоёв.
 */
export function removeBasemapBoundaries(map: MapType): void {
  const style = map.getStyle()
  if (!style?.layers) return
  for (const layer of style.layers) {
    const sourceLayer = (layer as { 'source-layer'?: string })['source-layer']
    const isBoundary = sourceLayer === 'boundary' || /boundary|admin/i.test(layer.id)
    if (isBoundary && map.getLayer(layer.id)) {
      map.removeLayer(layer.id)
    }
  }
}
