import json, pandas as pd, re
d=pd.read_csv('/w/panel.csv'); d=d[(d.level==2)&(d.indicator=='Посевная площадь')]
rng=d.groupby('region').value.agg(['min','max','last'])
pr=json.load(open('/w/lp_variant_presets.json'))['variants']
def key(n): return re.sub(r'[^а-яё]','',n.lower().replace('район','').replace('им','').replace('ауданы',''))[:6]
rk={key(r):r for r in rng.index}
for v,obj in pr.items():
    print(f'\n{v}: область arable {obj["arable"]:+.1f}')
    ok=0; tot=0
    for dn,dd in obj['districts'].items():
        r=rk.get(key(dn)); 
        if r is None: print('  ?',dn); continue
        lo,hi,last=rng.loc[r]; span=(hi-lo)*1.5; a=dd['arable']; tot+=1; ok+= abs(a)<=span
        print(f'  {r:28s} Δпашни {a:+7.1f}  размах посевов {hi-lo:6.1f} ×1.5={span:6.1f}  Δ/посевы {a/last:+.0%} {"ok" if abs(a)<=span else "ВНЕ"}')
    print(f'  внутри границ: {ok}/{tot}')
