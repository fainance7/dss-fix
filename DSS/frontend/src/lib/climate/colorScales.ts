// Справочные цветовые шкалы тепловых карт климатических дашбордов.
//
// Шкалы заданы заказчиком (docs/dashboards/climate). Хранятся как фронтенд-
// константа (по решению): дашборды раскрашивают районы и рисуют легенду по этим
// бинам. Бин полуинтервальный: значение попадает в бин, если
// (min === null || value > min) && (max === null || value <= max).
// null = ±∞. Бины упорядочены от меньшего к большему.

// Именованные цвета заказчика → hex.
export const CLIMATE_COLORS = {
  green: '#22c55e', // зелёный
  lightGreen: '#84cc16', // салатовый
  yellow: '#facc15', // жёлтый
  orange: '#f97316', // оранжевый
  lightBlue: '#38bdf8', // голубой
  blue: '#2563eb', // синий
  darkBlue: '#1e3a8a', // тёмно-синий
  purple: '#9333ea', // фиолетовый
} as const

export interface ColorBin {
  /** Нижняя граница (исключительно); null = −∞. */
  min: number | null
  /** Верхняя граница (включительно); null = +∞. */
  max: number | null
  /** Цвет бина, #RRGGBB. */
  color: string
  /** Подпись для легенды. */
  label: string
}

export interface ColorScale {
  key: string
  /** Человекочитаемое название шкалы. */
  name: string
  /** Единица значений (для подписи/легенды). */
  unit: string
  bins: ColorBin[]
}

// Границы бинов, заданные заказчиком «от–до» с точностью до сотых (0,51–0,60),
// смещены на полшага (0,505): значение относится к классу по округлённому до двух
// знаков числу — так же, как оно показано на карте и в таблицах.

// Осадки за период, % от среднемноголетней нормы.
const precipitationPct: ColorScale = {
  key: 'precipitationPct',
  name: 'Сумма осадков, % от среднемноголетней нормы',
  unit: '%',
  bins: [
    { min: null, max: 50, color: CLIMATE_COLORS.orange, label: '≤ 50 % — значительно меньше нормы' },
    { min: 50, max: 80, color: CLIMATE_COLORS.yellow, label: '51–80 % — меньше нормы' },
    { min: 80, max: 120, color: CLIMATE_COLORS.green, label: '81–120 % — около нормы' },
    { min: 120, max: 150, color: CLIMATE_COLORS.lightBlue, label: '121–150 % — больше нормы' },
    { min: 150, max: 200, color: CLIMATE_COLORS.blue, label: '151–200 % — значительно больше нормы' },
    { min: 200, max: null, color: CLIMATE_COLORS.purple, label: '> 200 % — избыточно' },
  ],
}

// Отклонение суммы осадков от среднемноголетней нормы, мм (год, вегетационный и холодный периоды).
const precipDeviationNorm: ColorScale = {
  key: 'precipDeviationNorm',
  name: 'Отклонение суммы осадков от среднемноголетней нормы, мм',
  unit: 'мм',
  bins: [
    { min: null, max: -40, color: CLIMATE_COLORS.orange, label: '≤ −40 мм — значительно меньше нормы' },
    { min: -40, max: -20, color: CLIMATE_COLORS.yellow, label: 'от −39 до −20 мм — меньше нормы' },
    { min: -20, max: 20, color: CLIMATE_COLORS.green, label: 'от −19 до +20 мм — около нормы' },
    { min: 20, max: 40, color: CLIMATE_COLORS.lightBlue, label: 'от +21 до +40 мм — больше нормы' },
    { min: 40, max: null, color: CLIMATE_COLORS.blue, label: '> +40 мм — значительно больше нормы' },
  ],
}

// То же для коротких сезонов (весна, осень) — суммы в 2–4 раза меньше.
const precipDeviationNormSeason: ColorScale = {
  key: 'precipDeviationNormSeason',
  name: 'Отклонение суммы осадков от среднемноголетней нормы, мм',
  unit: 'мм',
  bins: [
    { min: null, max: -20, color: CLIMATE_COLORS.orange, label: '≤ −20 мм — значительно меньше нормы' },
    { min: -20, max: -10, color: CLIMATE_COLORS.yellow, label: 'от −19 до −10 мм — меньше нормы' },
    { min: -10, max: 10, color: CLIMATE_COLORS.green, label: 'от −9 до +10 мм — около нормы' },
    { min: 10, max: 20, color: CLIMATE_COLORS.lightBlue, label: 'от +11 до +20 мм — больше нормы' },
    { min: 20, max: null, color: CLIMATE_COLORS.blue, label: '> +20 мм — значительно больше нормы' },
  ],
}

// Аномалия средней годовой температуры воздуха, °C.
const tempDeviation: ColorScale = {
  key: 'tempDeviation',
  name: 'Отклонение средней годовой температуры воздуха от нормы, °C',
  unit: '°C',
  bins: [
    { min: null, max: -2.05, color: CLIMATE_COLORS.blue, label: '≤ −2,1 °C — значительно меньше нормы' },
    { min: -2.05, max: -1.05, color: CLIMATE_COLORS.lightBlue, label: 'от −2,0 до −1,1 °C — меньше нормы' },
    { min: -1.05, max: 1.05, color: CLIMATE_COLORS.green, label: 'от −1,0 до +1,0 °C — около нормы' },
    { min: 1.05, max: 2.05, color: CLIMATE_COLORS.yellow, label: 'от +1,1 до +2,0 °C — больше нормы' },
    { min: 2.05, max: null, color: CLIMATE_COLORS.orange, label: '≥ +2,1 °C — значительно больше нормы' },
  ],
}

// Отклонение ΣT(5-8) > 10 °C от средних многолетних значений, % (шкала заказчика).
const sumTDeviationPct: ColorScale = {
  key: 'sumTDeviationPct',
  name: 'Отклонение суммы активных температур ΣT(5-8) от нормы, %',
  unit: '%',
  bins: [
    { min: null, max: -30.5, color: CLIMATE_COLORS.darkBlue, label: '≤ −31 % — сильный недобор термических ресурсов' },
    { min: -30.5, max: -20.5, color: CLIMATE_COLORS.blue, label: 'от −30 до −21 % — значительный недобор' },
    { min: -20.5, max: -10.5, color: CLIMATE_COLORS.lightBlue, label: 'от −20 до −11 % — умеренный недобор' },
    { min: -10.5, max: -5.5, color: '#bae6fd', label: 'от −10 до −6 % — слабый недобор' },
    { min: -5.5, max: 5.5, color: CLIMATE_COLORS.green, label: 'от −5 до +5 % — около средних многолетних значений' },
    { min: 5.5, max: 10.5, color: '#fde68a', label: 'от +6 до +10 % — слабое превышение' },
    { min: 10.5, max: 20.5, color: CLIMATE_COLORS.yellow, label: 'от +11 до +20 % — умеренное превышение' },
    { min: 20.5, max: 30.5, color: CLIMATE_COLORS.orange, label: 'от +21 до +30 % — значительное превышение' },
    { min: 30.5, max: null, color: '#b91c1c', label: '≥ +31 % — сильное превышение термических ресурсов' },
  ],
}

// Коэффициент увлажнения K(5-8): критерии влагообеспеченности вегетационного периода.
const kMoisture: ColorScale = {
  key: 'kMoisture',
  name: 'Влагообеспеченность по коэффициенту увлажнения K(5-8), безразмерная величина',
  unit: 'безразмерная величина',
  bins: [
    { min: null, max: 0.195, color: '#b91c1c', label: '< 0,20 — сухо' },
    { min: 0.195, max: 0.395, color: CLIMATE_COLORS.orange, label: '0,20–0,39 — сильный дефицит влаги' },
    { min: 0.395, max: 0.595, color: CLIMATE_COLORS.yellow, label: '0,40–0,59 — умеренный дефицит влаги' },
    { min: 0.595, max: 0.795, color: CLIMATE_COLORS.lightGreen, label: '0,60–0,79 — недостаточная влагообеспеченность' },
    { min: 0.795, max: 0.995, color: CLIMATE_COLORS.green, label: '0,80–0,99 — достаточная, но неустойчивая влагообеспеченность' },
    { min: 0.995, max: 1.395, color: CLIMATE_COLORS.lightBlue, label: '1,00–1,39 — оптимальная и устойчивая влагообеспеченность' },
    { min: 1.395, max: null, color: CLIMATE_COLORS.blue, label: '≥ 1,40 — избыток влаги' },
  ],
}

// Гидротермический коэффициент Селянинова ГТК(5-8): тепловая шкала по отдельным годам
// (таблица 1 предложений заказчика).
const gtk: ColorScale = {
  key: 'gtk',
  name: 'Засушливость по гидротермическому коэффициенту ГТК(5-8), безразмерная величина',
  unit: 'безразмерная величина',
  bins: [
    { min: null, max: 0.505, color: CLIMATE_COLORS.orange, label: '≤ 0,50 — сильно засушливо' },
    { min: 0.505, max: 0.605, color: CLIMATE_COLORS.yellow, label: '0,51–0,60 — умеренно засушливо' },
    { min: 0.605, max: 0.805, color: CLIMATE_COLORS.lightGreen, label: '0,61–0,80 — умеренно засушливо' },
    { min: 0.805, max: 1.005, color: CLIMATE_COLORS.green, label: '0,81–1,00 — слабо засушливо' },
    { min: 1.005, max: 1.505, color: CLIMATE_COLORS.lightBlue, label: '1,01–1,50 — достаточное увлажнение' },
    { min: 1.505, max: 2.005, color: CLIMATE_COLORS.blue, label: '1,51–2,00 — достаточное увлажнение' },
    { min: 2.005, max: null, color: CLIMATE_COLORS.purple, label: '> 2,00 — избыточное увлажнение' },
  ],
}

// Отклонение ГТК(5-8) от нормы (шкала заказчика).
const gtkDeviation: ColorScale = {
  key: 'gtkDeviation',
  name: 'Отклонение ГТК(5-8) от среднемноголетней нормы, безразмерная величина',
  unit: 'безразмерная величина',
  bins: [
    { min: null, max: -0.305, color: '#c2410c', label: '≤ −0,31 — намного меньше нормы' },
    { min: -0.305, max: -0.205, color: CLIMATE_COLORS.orange, label: 'от −0,30 до −0,21 — существенно меньше нормы' },
    { min: -0.205, max: -0.105, color: CLIMATE_COLORS.yellow, label: 'от −0,20 до −0,11 — меньше нормы' },
    { min: -0.105, max: -0.005, color: '#fef08a', label: 'от −0,10 до −0,01 — незначительно меньше нормы' },
    { min: -0.005, max: 0.005, color: CLIMATE_COLORS.green, label: '0,00 — соответствует норме' },
    { min: 0.005, max: 0.105, color: '#bae6fd', label: 'от +0,01 до +0,10 — незначительно больше нормы' },
    { min: 0.105, max: 0.205, color: CLIMATE_COLORS.lightBlue, label: 'от +0,11 до +0,20 — больше нормы' },
    { min: 0.205, max: 0.305, color: CLIMATE_COLORS.blue, label: 'от +0,21 до +0,30 — существенно больше нормы' },
    { min: 0.305, max: null, color: CLIMATE_COLORS.darkBlue, label: '≥ +0,31 — намного больше нормы' },
  ],
}

// Отклонение K(5-8) от нормы — те же градации, что у ГТК(5-8).
const kDeviation: ColorScale = {
  ...gtkDeviation,
  key: 'kDeviation',
  name: 'Отклонение K(5-8) от среднемноголетней нормы, безразмерная величина',
}

// Классификация обеспеченности осадками за вегетационный период (май–август), мм.
const precipVegClass: ColorScale = {
  key: 'precipVegClass',
  name: 'Сумма осадков за вегетационный период (май–август), мм',
  unit: 'мм',
  bins: [
    { min: null, max: 160, color: CLIMATE_COLORS.orange, label: '< 160 мм — мало' },
    { min: 160, max: 180, color: CLIMATE_COLORS.yellow, label: '160–180 мм' },
    { min: 180, max: 200, color: CLIMATE_COLORS.green, label: '180–200 мм' },
    { min: 200, max: 220, color: CLIMATE_COLORS.lightBlue, label: '200–220 мм' },
    { min: 220, max: null, color: CLIMATE_COLORS.blue, label: '> 220 мм — много' },
  ],
}

export const CLIMATE_COLOR_SCALES = {
  precipitationPct,
  precipDeviationNorm,
  precipDeviationNormSeason,
  tempDeviation,
  sumTDeviationPct,
  kMoisture,
  gtk,
  gtkDeviation,
  kDeviation,
  precipVegClass,
} as const

export type ClimateScaleKey = keyof typeof CLIMATE_COLOR_SCALES

/** Нейтральный цвет для значений вне шкалы / отсутствия данных. */
export const CLIMATE_NO_DATA_COLOR = '#e2e8f0'

/** Найти бин для значения в заданной шкале. */
export function binForValue(scaleKey: ClimateScaleKey, value: number): ColorBin | null {
  const scale = CLIMATE_COLOR_SCALES[scaleKey]
  for (const bin of scale.bins) {
    const aboveMin = bin.min === null || value > bin.min
    const belowMax = bin.max === null || value <= bin.max
    if (aboveMin && belowMax) return bin
  }
  return null
}

/** Цвет и подпись для значения; для пустого значения — нейтральный цвет. */
export function colorForValue(
  scaleKey: ClimateScaleKey,
  value: number | null | undefined,
): { color: string; label: string | null } {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return { color: CLIMATE_NO_DATA_COLOR, label: null }
  }
  const bin = binForValue(scaleKey, value)
  return bin ? { color: bin.color, label: bin.label } : { color: CLIMATE_NO_DATA_COLOR, label: null }
}
