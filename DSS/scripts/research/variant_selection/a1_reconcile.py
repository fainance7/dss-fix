import pandas as pd, numpy as np
d=pd.read_csv('/w/panel.csv')
d=d[d.level==2]
def s(name): 
    x=d[d.indicator==name].pivot_table(index='region',columns='year',values='value',aggfunc='first'); return x
Y=s('Урожайность зерновых культур'); P=s('Валовой сбор зерновых культур'); A=s('Посевная площадь зерновых культур')
print('годы урожайности', sorted(Y.columns.tolist()))
print('годы сбора', sorted(P.columns.tolist())); print('годы площади', sorted(A.columns.tolist()))
# согласованность: урожайность ≈ сбор / площадь (т / тыс.га → ц/га: т/(тыс.га*1000)*10)
calc=(P/(A*1000)*10)
r=(calc/Y).stack()
print('\nотношение (сбор/площадь)/урожайность: медиана %.3f, доля в ±5%%: %.2f, n=%d'%(r.median(), ((r-1).abs()<0.05).mean(), len(r)))
bad=r[((r-1).abs()>=0.2)]
print('расхождения >20%:'); print(bad.round(2).to_string()[:2000])
print('\nурожайность ц/га: min %.1f median %.1f max %.1f'%(Y.stack().min(),Y.stack().median(),Y.stack().max()))
print(Y.round(1).to_string())
