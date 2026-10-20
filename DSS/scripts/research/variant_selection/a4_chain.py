import warnings, json, sys; warnings.filterwarnings('ignore')
sys.path.insert(0,'/app')
import pandas as pd, numpy as np
from app.services.landscape_impact import ur_official as uo
d=pd.read_csv('/w/panel.csv'); d=d[d.level==2]
KEYS=['петропавл','айыртау','акжар','аккайын','есиль','жамбыл','кызылжар','мамлют','тайынш','тимиряз','уалихан','жумабаев','шал акын','мусрепов']
def key(n):
    n=n.lower().replace('ё','е')
    return next((k for k in KEYS if k in n),None)
d['k']=d.region.map(key)
def s(name): return d[d.indicator==name].pivot_table(index='k',columns='year',values='value',aggfunc='first')
VP=s('Валовая продукция растениеводства'); SA=s('Посевная площадь'); PA=s('Площадь пашни'); VSX=s('Валовая продукция сельского хозяйства')
yrs=[c for c in VP.columns if 2020<=c<=2025 and c in SA.columns]
vha=(VP[yrs]/SA[yrs])            # тыс. тг на 1 га посевов, по годам
v_mean=vha.mean(axis=1); v_p10=vha.min(axis=1)   # среднее и худший год (засуха)
sown=(SA[2025]/PA[2026]).clip(upper=1.0)        # доля пашни под посевами
pres=json.load(open('/app/app/services/landscape_impact/reference/lp_variant_presets.json'))['variants']
cmp_=json.load(open('/w/lp_compare.json'))
PHI={'низкая':0.5,'базовая':0.75,'высокая':1.0}   # [Э] продуктивность новых земель относительно средней по району
model=uo.load_model()
year_idx=model.years.index(2024)
def territory_of(k): return next(t for t in model.territories if key(t)==k)
base_vsx=VSX[2024]  # млрд тг
rows=[]
for vg,short in (('lp-v1-conservative','V1'),('lp-v2-balanced','V2'),('lp-v3-development','V3')):
    for phi_name,phi in PHI.items():
        for vcol,vname in ((v_mean,'средний год'),(v_p10,'засушливый год')):
            dvp={}
            for dist,x in pres[vg]['districts'].items():
                k=key(dist)
                if k=='петропавл' or k not in vcol.index: continue
                da=x['arable']   # тыс. га пашни (+ распашка, − вывод)
                # распаханные новые земли — с коэффициентом φ, выведенные поля — по средней отдаче района
                gain=max(da,0)*phi; loss=min(da,0)
                dvp[k]=(gain+loss)*float(sown.get(k,0.9))*float(vcol[k])/1000   # млрд тг
            tot=sum(dvp.values())
            # официальный индекс: по каждой территории своё относительное изменение продукции СХ
            dt=[]; 
            for t in model.territories:
                k=key(t); rel=dvp.get(k,0.0)/float(base_vsx[k]) if k in dvp and k in base_vsx else 0.0
                blocks=uo.compute_blocks(model,t); tb=blocks['total'][year_idx]
                cur=uo.compute_indicator(model,'economic','agri_pc',t)
                val=cur['transformed'][year_idx]; nb=cur['normalized'][year_idx]
                ns,_=uo.normalize(val*(1+rel), model.blocks['economic']['indicators']['agri_pc']['bounds'])
                w=model.weight('economic','agri_pc')
                dt.append((ns-nb)*w/3)
            rows.append({'вариант':short,'продуктивность новых земель':phi_name,'год':vname,
                         'ΔВП СХ, млрд тг':round(tot,1),'ΔВП к базе 2024, %':round(100*tot/float(base_vsx.sum()),1),
                         'Δ индекса УР':round(float(np.mean(dt)),5)})
R=pd.DataFrame(rows)
mde=uo.mde_official(model)['total']
print('Порог различимости официального индекса (MDE):',round(mde,5))
print('ВП СХ районов 2024, млрд тг:',round(float(base_vsx.sum()),1))
print(R.to_string(index=False))
# разница между вариантами
piv=R.pivot_table(index=['продуктивность новых земель','год'],columns='вариант',values=['ΔВП СХ, млрд тг','Δ индекса УР'])
print('\n',piv.round(5).to_string())
# точка безубыточности «цена природы»: на сколько V3 даёт больше продукции, на 1 га земель высокой природной значимости
z={v:cmp_['oblast'][v]['nature_value'] for v in ('V1','V2','V3')}
print('\nЗемли высокой природной значимости под пашней/орошением, тыс. га:',z)
for (phi_name,yr),g in R.groupby(['продуктивность новых земель','год']):
    vv=g.set_index('вариант')['ΔВП СХ, млрд тг']
    for a,b in (('V3','V1'),('V2','V1'),('V3','V2')):
        dz=z[a]-z[b]
        if dz>0: print(f'  {phi_name:8s} {yr:15s} {a} против {b}: +{vv[a]-vv[b]:.1f} млрд тг/год ⇒ {1e9*(vv[a]-vv[b])/(dz*1000):,.0f} тг на 1 га ценных природных земель в год')
# распределение прироста пашни по районам и их климат
print('\nПрирост пашни по районам, тыс. га, и отдача 1 га посевов (тыс. тг): средний / засушливый год')
out=[]
for dist in pres['lp-v3-development']['districts']:
    k=key(dist)
    if k=='петропавл': continue
    out.append([k]+[pres[v]['districts'][dist]['arable'] for v in ('lp-v1-conservative','lp-v2-balanced','lp-v3-development')]+[round(float(v_mean.get(k,np.nan))),round(float(v_p10.get(k,np.nan)))])
print(pd.DataFrame(out,columns=['район','V1','V2','V3','отдача ср.','отдача засуха']).sort_values('V3',ascending=False).to_string(index=False))
