import warnings, json, re; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
from scipy import stats
d=pd.read_csv('/w/panel.csv'); d=d[d.level==2]
def key(n):
    n=n.lower().replace('ё','е')
    for k in ['петропавл','айыртау','акжар','аккайын','есиль','жамбыл','кызылжар','мамлют','тайынш','тимиряз','уалихан','жумабаев','шал акын','мусрепов']:
        if k in n: return k
    return None
d['k']=d.region.map(key)
def s(name): return d[d.indicator==name].pivot_table(index='k',columns='year',values='value',aggfunc='first')
Y=s('Урожайность зерновых культур').drop(index='петропавл',errors='ignore')
G=s('Гидротермический коэффициент (ГТК)')
VP=s('Валовая продукция растениеводства'); SA=s('Посевная площадь'); PA=s('Площадь пашни'); GA=s('Посевная площадь зерновых культур')
print('годы ВП растениеводства:',sorted(VP.columns.tolist()),' посевная площадь:',sorted(SA.columns.tolist()),' пашня:',sorted(PA.columns.tolist()))
ynorm=Y.mean(axis=1); ycv=Y.std(axis=1)/ynorm
gnorm=G.loc[:,2003:2025].mean(axis=1)
# 1) пространственная проверка: средняя урожайность района ~ норма ГТК
c=pd.concat([ynorm.rename('y'),gnorm.rename('g'),ycv.rename('cv')],axis=1).dropna()
r,p=stats.spearmanr(c.y,c.g); print(f'\nпространство: урожайность района ~ норма ГТК: Spearman {r:.2f}, p={p:.3f}, n={len(c)}')
r,p=stats.spearmanr(c.cv,c.g); print(f'пространство: вариабельность урожайности ~ норма ГТК: Spearman {r:.2f}, p={p:.3f}')
# 2) пригодность для богарного земледелия по районам (доля классов 5 и 4-5 во всей зонированной площади)
cx=json.load(open('/w/lp_compare_crosstab.json'))
suit={}
for dist,vv in cx['cross']['lp-p-crop'].items():
    tot={}
    for kk,ha in vv['V1'].items():
        lc=kk.split('-')[1]; tot[lc]=tot.get(lc,0)+ha
    s_=sum(tot.values()); suit[key(dist)]={'p5':tot.get('5',0)/s_, 'p45':(tot.get('4',0)+tot.get('5',0))/s_}
S=pd.DataFrame(suit).T
c2=pd.concat([ynorm.rename('y'),S],axis=1).dropna()
for col in ('p5','p45'):
    r,p=stats.spearmanr(c2.y,c2[col]); print(f'пространство: урожайность ~ доля пригодности {col}: Spearman {r:.2f}, p={p:.3f}, n={len(c2)}')
tab=pd.concat([ynorm.round(1).rename('урож_норма'),(ycv*100).round(0).rename('CV_%'),gnorm.round(2).rename('ГТК_норма'),(S.p5*100).round(0).rename('доля_пригодн5_%')],axis=1)
print(tab.sort_values('ГТК_норма').to_string())
# 3) стоимость продукции растениеводства на 1 га посевов (млн тг / тыс. га = тыс. тг/га), последние годы
v=(VP/SA).loc[:,[c for c in VP.columns if c>=2019 and c in SA.columns]]
print('\nВП растениеводства на 1 га посевов, тыс. тг/га (годы',list(v.columns),'):'); print(v.round(0).to_string())
print('\nпашня по районам (тыс. га):'); print(PA.round(0).to_string())
print('\nпосевная / пашня, медиана по районам и годам:', round(float((SA/PA).stack().median()),3) if not PA.empty else 'нет')
