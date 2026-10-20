'use client'

/**
 * Формула и критерии оценки агроклиматического коэффициента (замечания заказчика):
 * коэффициент увлажнения K(5-8) и гидротермический коэффициент Селянинова ГТК(5-8).
 */

import type { ReactNode } from 'react'
import { CLIMATE_COLOR_SCALES } from '@/lib/climate/colorScales'

function Fraction({ num, den }: { num: ReactNode; den: ReactNode }) {
  return (
    <span className="inline-flex flex-col items-center align-middle mx-1">
      <span className="px-1 border-b border-gray-700 leading-tight">{num}</span>
      <span className="px-1 leading-tight">{den}</span>
    </span>
  )
}

const Sub = ({ children }: { children: ReactNode }) => <sub className="text-[0.7em]">{children}</sub>

export default function ClimateFormula({ kind }: { kind: 'k' | 'gtk' }) {
  const scale = CLIMATE_COLOR_SCALES[kind === 'k' ? 'kMoisture' : 'gtk']
  return (
    <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <h3 className="mb-3 text-base font-semibold text-gray-900">Формула расчёта</h3>
          <div className="text-lg text-gray-900">
            {kind === 'k' ? (
              <>
                K<Sub>(5-8)</Sub> ={' '}
                <Fraction
                  num={
                    <>
                      0,5 · ΣR<Sub>(11–4)</Sub> + ΣR<Sub>(5-8)</Sub>
                    </>
                  }
                  den={
                    <>
                      0,12 · ΣT<Sub>(5-8)</Sub>
                    </>
                  }
                />
              </>
            ) : (
              <>
                ГТК<Sub>(5-8)</Sub> ={' '}
                <Fraction
                  num={
                    <>
                      ΣR<Sub>(5-8)</Sub>
                    </>
                  }
                  den={
                    <>
                      0,1 · ΣT<Sub>(5-8)</Sub>
                    </>
                  }
                />
              </>
            )}
          </div>
          <ul className="mt-3 space-y-1 text-base text-gray-600">
            {kind === 'k' && (
              <li>ΣR(11–4) — сумма осадков за ноябрь–апрель (ноябрь–декабрь предыдущего года), мм;</li>
            )}
            <li>ΣR(5-8) — сумма осадков за май–август, мм;</li>
            <li>ΣT(5-8) — сумма активных температур воздуха выше 10 °C за май–август, °C·сут.</li>
          </ul>
          <p className="mt-3 text-sm text-gray-500">
            {kind === 'k'
              ? 'Коэффициент увлажнения — показатель влагообеспеченности вегетационного периода (май–август) с учётом осенне-зимних запасов влаги. Безразмерная величина.'
              : 'Гидротермический коэффициент Селянинова — интегральный показатель влагообеспеченности периода активной вегетации сельскохозяйственных культур (май–август) по сумме осадков и сумме температур. Безразмерная величина.'}{' '}
            Значение района — среднее по метеостанциям района.
          </p>
        </div>
        <div>
          <h3 className="mb-3 text-base font-semibold text-gray-900">
            {kind === 'k'
              ? 'Критерии оценки влагообеспеченности вегетационного периода'
              : 'Градации засушливости по ГТК(5-8)'}
          </h3>
          <ul className="space-y-1 text-base text-gray-700">
            {[...scale.bins].reverse().map((b) => (
              <li key={b.label} className="flex items-center gap-2">
                <span
                  className="inline-block h-3 w-3 flex-shrink-0 rounded-sm ring-1 ring-black/10"
                  style={{ backgroundColor: b.color }}
                />
                {b.label}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}
