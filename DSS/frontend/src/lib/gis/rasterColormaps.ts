// Цветовые шкалы для растровых слоёв (совпадают с colormap_name titiler).
// Значение имени идёт в titiler как ?colormap_name=...; CSS-градиент — для легенды.

export interface RasterColormap {
  /** имя colormap в titiler (matplotlib) */
  name: string
  /** контрольные точки для CSS linear-gradient (лево→право = min→max) */
  stops: string[]
}

export const RASTER_COLORMAPS: Record<string, RasterColormap> = {
  viridis: {
    name: 'viridis',
    stops: ['#440154', '#414487', '#2a788e', '#22a884', '#7ad151', '#fde725'],
  },
  turbo: {
    name: 'turbo',
    stops: ['#30123b', '#4148e0', '#26b79f', '#a2fc3c', '#f7b731', '#7a0403'],
  },
  magma: {
    name: 'magma',
    stops: ['#000004', '#3b0f70', '#8c2981', '#de4968', '#fe9f6d', '#fcfdbf'],
  },
}

export const DEFAULT_RASTER_COLORMAP = 'viridis'

export function colormapGradient(name: string): string {
  const cm = RASTER_COLORMAPS[name] ?? RASTER_COLORMAPS[DEFAULT_RASTER_COLORMAP]
  return `linear-gradient(to right, ${cm.stops.join(', ')})`
}
