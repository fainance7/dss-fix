import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
rng=np.random.default_rng(1)
d=pd.read_csv('/w/panel.csv'); d=d[(d.level==2)&(d.region!='г. Петропавловск')]
def s(name): return d[d.indicator==name].groupby(['region','year']).value.first()
D=pd.concat({'vp':s('Валовая продукция растениеводства'),'sa':s('Посевная площадь'),
             'gtk':s('Гидротермический коэффициент (ГТК)'),'y':s('Урожайность зерновых культур'),
             'vsx':s('Валовая продукция сельского хозяйства')},axis=1).reset_index()
def fe(df,ycol,xcol):
    df=df.dropna(subset=[ycol,xcol]).copy()
    M=df[[ycol,xcol]].copy()
    for _ in range(60):
        M=M-M.groupby(df.region).transform('mean'); M=M-M.groupby(df.year).transform('mean')
    b=(M[xcol]*M[ycol]).sum()/(M[xcol]**2).sum()
    return b,df
def perm(df,ycol,xcol,n=500):
    b0,dd=fe(df,ycol,xcol); c=0
    for _ in range(n):
        p=dd.copy(); p[xcol]=p.groupby('region')[xcol].transform(lambda v: rng.permutation(v.values))
        c+=abs(fe(p,ycol,xcol)[0])>=abs(b0)
    return b0,(c+1)/(n+1),len(dd)
D['lv']=np.log(D.vp/D.sa)   # ln стоимости продукции на 1 га посевов (годовые эффекты снимают цены и инфляцию)
D['lvsx']=np.log(D.vsx)
for yc,lab in (('lv','ln(ВП растениеводства / посевы)'),('lvsx','ln(ВП сельского хозяйства района)')):
    b,p,n=perm(D,yc,'gtk')
    print(f'{lab:36s} ~ ГТК | район+год: b={b:.3f}, p_perm={p:.3f}, n={n}')
# Климат 2030 по сценарным оценкам эксперта: +0,7 °C (верхняя граница — равномерно на май–август), осадки 0…+5 %
b_y=0.341; gtk0=0.90; t58=2151.0
for dT,dR,lab in ((0.7,0.0,'потепление 0,7 °C, осадки без изменений'),(0.7,0.05,'потепление 0,7 °C, осадки +5 %'),(0.35,0.0,'потепление летом вдвое меньше (0,35 °C)')):
    t_new=t58+dT*123
    g_new=gtk0*(1+dR)*t58/t_new
    print(f'{lab:45s}: ΣT(5-8) {t58:.0f}→{t_new:.0f}, ГТК {gtk0:.2f}→{g_new:.3f}, урожайность {100*(np.exp(b_y*(g_new-gtk0))-1):+.1f} %')
# Сверка: сумма районов ВП СХ против областного итога
o=pd.read_csv('/w/panel.csv'); o=o[(o.level==1)&(o.indicator=='Валовая продукция сельского хозяйства')].set_index('year').value
r=D.groupby('year').vsx.sum()
print('\nсверка ВП СХ: сумма районов / область:',(r/o).dropna().round(3).to_dict())
