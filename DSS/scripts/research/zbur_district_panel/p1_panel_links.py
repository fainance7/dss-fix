import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
rng=np.random.default_rng(0)
d=pd.read_csv('/w/panel.csv'); d=d[(d.level==2)&(d.region!='г. Петропавловск')]
def s(name, dr=None):
    x=d[d.indicator==name]
    if dr: x=x[x.direction==dr]
    return x.groupby(['region','year']).value.first().rename(name)
P=pd.concat([s('Валовая продукция сельского хозяйства','economy').rename('vp'),
  s('Посевная площадь').rename('sown'), s('Посевная площадь зерновых культур').rename('garea'),
  s('Урожайность зерновых культур').rename('yld'), s('Валовой сбор зерновых культур').rename('gprod'),
  s('Гидротермический коэффициент (ГТК)').rename('gtk')],axis=1).reset_index()

def _design(df, xs, twoway):
    parts=[df[xs].astype(float).values, pd.get_dummies(df.region).values.astype(float)]
    if twoway: parts.append(pd.get_dummies(df.year).values[:,1:].astype(float))
    return np.hstack(parts)
def demean(df, cols, twoway):
    Z=_design(df, [], twoway); out={}
    for c in cols:
        v=df[c].astype(float).values; b,*_=np.linalg.lstsq(Z,v,rcond=None); out[c]=v-Z@b
    return pd.DataFrame(out, index=df.index)
def fit(df, y, xs, twoway=True):
    M=demean(df,[y]+xs,twoway); X=M[xs].values; Y=M[y].values
    b,*_=np.linalg.lstsq(X,Y,rcond=None); r=Y-X@b
    XtX=np.linalg.inv(X.T@X); meat=np.zeros((len(xs),len(xs)))
    for g,idx in df.reset_index(drop=True).groupby('region').indices.items():
        sg=X[idx].T@r[idx]; meat+=np.outer(sg,sg)
    G=df.region.nunique(); V=XtX@meat@XtX*G/(G-1)
    return b, np.sqrt(np.diag(V)), 1-(r**2).sum()/(Y**2).sum()
def perm(df,y,xs,target,twoway=True,n=999):
    b0,_,_=fit(df,y,xs,twoway); c=0
    for _ in range(n):
        p=df.copy(); p[target]=p.groupby('region')[target].transform(lambda v: rng.permutation(v.values))
        b,_,_=fit(p,y,xs,twoway); c+= abs(b[0])>=abs(b0[0])
    return (c+1)/(n+1)
def report(title, df, y, xs, target, twoway=True):
    df=df.dropna(subset=[y]+xs).copy()
    b,se,r2=fit(df,y,xs,twoway); p=perm(df,y,xs,target,twoway)
    jr=[fit(df[df.region!=g],y,xs,twoway)[0][0] for g in df.region.unique()]
    jy=[fit(df[df.year!=t],y,xs,twoway)[0][0] for t in df.year.unique()]
    sd=demean(df,[target],twoway)[target].std()
    print(f'\n### {title}\n  n={len(df)} районов={df.region.nunique()} лет={df.year.nunique()} {"район+год" if twoway else "район"}')
    print(f'  b={np.round(b,4)} se_cl={np.round(se,4)} R2within={r2:.3f} p_perm({target})={p:.4f}')
    print(f'  jackknife район [{min(jr):.3f};{max(jr):.3f}]  год [{min(jy):.3f};{max(jy):.3f}]  max|dev| {max(abs(np.r_[jr,jy]-b[0]))/abs(b[0]):.2f}')
    print(f'  SD внутрирайонной вариации {target} = {sd:.4f}')
P['lvp']=np.log(P.vp); P['lsown']=np.log(P.sown); P['lgarea']=np.log(P.garea); P['lyld']=np.log(P.yld); P['lgprod']=np.log(P.gprod)
report('A. ln ВП СХ ~ ln посевная площадь (2014–2025)', P[P.year>=2014], 'lvp', ['lsown'], 'lsown')
report('A2. ln ВП СХ ~ ln посевы + ГТК', P[P.year>=2014], 'lvp', ['lsown','gtk'], 'lsown')
report('B. ln урожайность ~ ln площадь зерновых + ГТК (отдача расширения)', P, 'lyld', ['lgarea','gtk'], 'lgarea')
report('B2. ln валовой сбор ~ ln площадь зерновых + ГТК (эластичность сбора по площади)', P, 'lgprod', ['lgarea','gtk'], 'lgarea')
report('C. ln урожайность ~ ГТК, район+год', P, 'lyld', ['gtk'], 'gtk')
report('C1. ln урожайность ~ ГТК, район+тренд не, только район', P, 'lyld', ['gtk'], 'gtk', twoway=False)
# сверка сбор = урожайность × площадь
c=P.dropna(subset=['yld','garea','gprod']); r=(c.yld*c.garea*100/c.gprod)
print('\nсверка yld*area/prod: медиана', round(r.median(),3), 'доля |откл|>5%:', round((abs(r-1)>0.05).mean(),3))
