import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
rng=np.random.default_rng(0)
d=pd.read_csv('/w/panel.csv'); d=d[(d.level==2)&(d.region!='г. Петропавловск')]
def s(name):
    return d[d.indicator==name].groupby(['region','year']).value.first().rename(name)
Y=s('Урожайность зерновых культур')
clim={'gtk':'Гидротермический коэффициент (ГТК)','k':'Коэффициент увлажнения (K)',
      't58':'Сумма активных температур воздуха выше 10 °C за май–август, ΣT(5-8)',
      'r58':'Сумма осадков за вегетационный период','rcold':'Сумма осадков за холодный период',
      'rspr':'Сумма осадков за весенний период (апрель–май)'}
X=pd.concat([s(v).rename(k) for k,v in clim.items()],axis=1)
D=pd.concat([np.log(Y).rename('ly'),X],axis=1).dropna(subset=['ly']).reset_index()
print('наблюдений с урожайностью:',len(D),' районов',D.region.nunique(),' лет',D.year.nunique())
print('пропуски климата:',D[list(clim)].isna().sum().to_dict())

def fe_fit(df, xs, twoway=False):
    df=df.dropna(subset=xs).copy()
    Z=df[xs].astype(float)
    # демин по району (+ по году при twoway) — итерационно
    y=df.ly.copy(); M=pd.concat([y,Z],axis=1)
    for _ in range(50 if twoway else 1):
        M=M-M.groupby(df.region).transform('mean')
        if twoway: M=M-M.groupby(df.year).transform('mean')
    if not twoway:
        t=(df.year-df.year.mean()); t=t-t.groupby(df.region).transform('mean'); M['trend']=t
    Xm=M.drop(columns='ly').values; ym=M.ly.values
    b,*_=np.linalg.lstsq(Xm,ym,rcond=None); res=ym-Xm@b
    r2=1-(res**2).sum()/(ym**2).sum()
    return b, r2, df, Xm, ym

def perm_p(df, xs, twoway, n=2000):
    b0,r20,dd,_,_=fe_fit(df,xs,twoway)
    cnt=0
    for _ in range(n):
        p=dd.copy()
        # перестановка лет климата внутри района (сохраняет распределение района)
        for c in xs:
            p[c]=p.groupby('region')[c].transform(lambda v: rng.permutation(v.values))
        _,r2,_,_,_=fe_fit(p,xs,twoway)
        cnt+= r2>=r20
    return (cnt+1)/(n+1), b0, r20

print('\n=== Модель 1: ln урожайности ~ климат + эффект района + общий тренд ===')
for xs in (['gtk'],['k'],['r58'],['t58'],['rcold'],['r58','t58'],['gtk','rcold']):
    p,b,r2=perm_p(D,xs,False,1000)
    print(f'{"+".join(xs):10s} b={np.round(b[:len(xs)],4)} R²(within)={r2:.3f}  p_perm={p:.4f}')
print('\n=== Модель 2: двусторонние эффекты (район + год): только межрайонные аномалии ===')
for xs in (['gtk'],['k'],['r58'],['t58']):
    p,b,r2=perm_p(D,xs,True,1000)
    print(f'{"+".join(xs):10s} b={np.round(b,4)} R²(within)={r2:.3f}  p_perm={p:.4f}')

# Jackknife по районам и прогноз на отложенных годах для лучшей одиночной модели
for xs in (['gtk'],['r58','t58']):
    bs=[fe_fit(D[D.region!=r],xs,False)[0][0] for r in D.region.unique()]
    print(f'\njackknife {xs}: b1 от {min(bs):.4f} до {max(bs):.4f} (знак стабилен: {np.sign(min(bs))==np.sign(max(bs))})')
    # leave-one-year-out: прогноз урожайности района по климату года vs базовая (район+тренд)
    err_m, err_b = [], []
    for yv in sorted(D.year.unique()):
        tr=D[D.year!=yv].dropna(subset=xs); te=D[D.year==yv].dropna(subset=xs)
        if te.empty: continue
        for model in ('m','b'):
            cols = xs if model=='m' else []
            Xtr=np.column_stack([pd.get_dummies(tr.region).values.astype(float), tr.year.values-2017]+[tr[c].values for c in cols])
            bb,*_=np.linalg.lstsq(Xtr,tr.ly.values,rcond=None)
            dm=pd.get_dummies(te.region).reindex(columns=pd.get_dummies(tr.region).columns,fill_value=0).values.astype(float)
            Xte=np.column_stack([dm, te.year.values-2017]+[te[c].values for c in cols])
            e=np.exp(te.ly.values)-np.exp(Xte@bb)
            (err_m if model=='m' else err_b).append(np.abs(e))
    em=np.concatenate(err_m).mean(); eb=np.concatenate(err_b).mean()
    print(f'  отложенный год: MAE климат-модели {em:.2f} ц/га против базовой {eb:.2f} ц/га — улучшение {100*(1-em/eb):.0f}%')
