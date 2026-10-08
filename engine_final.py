import sqlite3, time, json, os
from pathlib import Path
from datetime import datetime, timezone
import requests, numpy as np, pandas as pd

ROOT=Path(__file__).resolve().parent
# v57: analysis data persists outside the app package so replacing/updating the app does not
# force another 730-day bootstrap. Set COIN_PATTERN_HOME to override the storage location.
default_home = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.coinpattern'))) / 'CoinPattern'
APP_HOME = Path(os.environ.get('COIN_PATTERN_HOME', str(default_home)))
DATA=APP_HOME/'data'; OUT=APP_HOME/'output'
UPDB=DATA/'market.db'; BNDB=DATA/'binance.db'; CACHE=DATA/'binance_symbols.json'
UPBIT='https://api.upbit.com'; BINANCE='https://api.binance.com'
HISTORY=730; PAGE=200; SLEEP=.08; THRESH=.05; MIN_OBS=12; TOP=30; REFRESH_DAYS=3
MODE=os.getenv('COIN_PATTERN_MODE','cache').lower()  # full/refresh/cache
s=requests.Session(); s.headers['User-Agent']='CoinPatternFinal/1.0'

SECTORS={'VIRTUAL':'AI','TAO':'AI','FET':'AI','RNDR':'AI','WLD':'AI','ONDO':'RWA','ENA':'RWA','MKR':'RWA','PENDLE':'RWA','LINK':'Oracle','API3':'Oracle','PYTH':'Oracle','AAVE':'DeFi','UNI':'DeFi','CRV':'DeFi','COMP':'DeFi','DYDX':'DeFi','JUP':'DeFi','WIF':'MEME','DOGE':'MEME','SHIB':'MEME','PEPE':'MEME','BONK':'MEME','FLOKI':'MEME','IMX':'Gaming','GALA':'Gaming','BEAM':'Gaming','AXS':'Gaming','SAND':'Gaming','MANA':'Gaming','ARB':'Layer2','OP':'Layer2','MATIC':'Layer2','SOL':'Layer1','ADA':'Layer1','AVAX':'Layer1','DOT':'Layer1','ATOM':'Layer1','NEAR':'Layer1','SUI':'Layer1','APT':'Layer1','FIL':'Infra','AR':'Infra','GRT':'Infra','AKT':'DePIN','HNT':'DePIN','STORJ':'DePIN'}

def dbs():
    DATA.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)
    with sqlite3.connect(UPDB) as c: c.execute('CREATE TABLE IF NOT EXISTS daily(market,date,open,high,low,close,volume,value,PRIMARY KEY(market,date))')
    with sqlite3.connect(BNDB) as c: c.execute('CREATE TABLE IF NOT EXISTS daily(symbol,date,open,high,low,close,volume,PRIMARY KEY(symbol,date))')

def get(url,params=None):
    wait=1
    for _ in range(6):
        r=s.get(url,params=params,timeout=25)
        if r.status_code==200:return r.json()
        if r.status_code in (418,429):time.sleep(wait);wait=min(wait*2,20);continue
        r.raise_for_status()
    raise RuntimeError('rate limit retry exhausted')

def up_markets():
    return sorted(x['market'] for x in get(UPBIT+'/v1/market/all',{'isDetails':'false'}) if x['market'].startswith('KRW-'))

def binance_symbols():
    if CACHE.exists():
        try:
            x=json.loads(CACHE.read_text())
            if x.get('ts',0)>time.time()-86400:return set(x['symbols'])
        except Exception: pass
    d=get(BINANCE+'/api/v3/exchangeInfo'); z={x['symbol'] for x in d['symbols'] if x.get('status')=='TRADING' and x.get('quoteAsset')=='USDT' and x.get('isSpotTradingAllowed',True)}
    CACHE.write_text(json.dumps({'ts':time.time(),'symbols':sorted(z)})); return z

def sync_up(markets):
    with sqlite3.connect(UPDB) as c:
        for i,m in enumerate(markets,1):
            try:
                have=c.execute('SELECT COUNT(*) FROM daily WHERE market=?',(m,)).fetchone()[0]
                if MODE=='cache' and have: continue
                rows=[]
                if have and MODE!='full': rows=get(UPBIT+'/v1/candles/days',{'market':m,'count':REFRESH_DAYS})
                else:
                    to=None
                    while len(rows)<HISTORY:
                        q={'market':m,'count':PAGE}
                        if to:q['to']=to
                        x=get(UPBIT+'/v1/candles/days',q); rows+=x
                        if len(x)<PAGE:break
                        to=x[-1]['candle_date_time_utc']+'Z'; time.sleep(SLEEP)
                for x in rows[:HISTORY]:
                    c.execute('INSERT OR REPLACE INTO daily VALUES(?,?,?,?,?,?,?,?)',(m,x['candle_date_time_utc'][:10],x['opening_price'],x['high_price'],x['low_price'],x['trade_price'],x['candle_acc_trade_volume'],x['candle_acc_trade_price']))
                c.commit()
                if MODE!='cache': print(f'[Upbit] {i}/{len(markets)} {m}')
            except Exception as e: print(f'[Upbit] {i}/{len(markets)} {m} ERROR: {e}')

def sync_bn(markets,valid):
    with sqlite3.connect(BNDB) as c:
        for i,m in enumerate(markets,1):
            sym=m.split('-')[1]+'USDT'
            if sym not in valid: continue
            try:
                have=c.execute('SELECT COUNT(*) FROM daily WHERE symbol=?',(sym,)).fetchone()[0]
                if MODE=='cache' and have: continue
                limit=REFRESH_DAYS if have and MODE!='full' else HISTORY
                rows=get(BINANCE+'/api/v3/klines',{'symbol':sym,'interval':'1d','limit':limit})
                for x in rows:
                    dt=datetime.fromtimestamp(x[0]/1000,tz=timezone.utc).strftime('%Y-%m-%d')
                    c.execute('INSERT OR REPLACE INTO daily VALUES(?,?,?,?,?,?,?)',(sym,dt,float(x[1]),float(x[2]),float(x[3]),float(x[4]),float(x[5])))
                c.commit()
                if MODE!='cache': print(f'[Binance] {i}/{len(markets)} {sym}')
            except Exception as e: print(f'[Binance] {i}/{len(markets)} {sym} ERROR: {e}')

def load(db):
    with sqlite3.connect(db) as c:return pd.read_sql_query('SELECT * FROM daily ORDER BY date',c)

def features(d):
    d=d.sort_values(['market','date']).copy(); g=d.groupby('market',sort=False)
    d['ret1']=g.close.pct_change(); d['next_ret']=g.close.shift(-1)/d.close-1
    d['vol_ma20']=g.volume.transform(lambda x:x.rolling(20,min_periods=5).mean()); d['vol_ratio']=d.volume/d.vol_ma20
    d['value_ma20']=g.value.transform(lambda x:x.rolling(20,min_periods=5).mean()); d['value_ratio']=d.value/d.value_ma20
    for n in (5,20,50,200):d[f'sma{n}']=g.close.transform(lambda x,n=n:x.rolling(n,min_periods=n).mean())
    d['above20']=(d.close>d.sma20); d['above50']=(d.close>d.sma50); d['ma20_up']=g.sma20.pct_change(5)>0
    delta=g.close.diff(); gain=delta.clip(lower=0).groupby(d.market).transform(lambda x:x.rolling(14,min_periods=14).mean()); loss=(-delta.clip(upper=0)).groupby(d.market).transform(lambda x:x.rolling(14,min_periods=14).mean()); rs=gain/loss.replace(0,np.nan); d['rsi']=100-100/(1+rs)
    d['price_band']=pd.cut(d.close,[-np.inf,100,500,1000,5000,10000,np.inf],labels=['<100','100-500','500-1k','1k-5k','5k-10k','10k+']).astype(str)
    d['symbol']=d.market.str[4:]; d['sector']=d.symbol.map(SECTORS).fillna('Other')
    daily=d.groupby('date').agg(market_up=('ret1',lambda x:(x>0).mean()),market_strong=('ret1',lambda x:(x>=THRESH).mean()),market_value=('value','sum')).reset_index(); d=d.merge(daily,on='date',how='left')
    d['size_pct']=d.groupby('date').value_ma20.rank(pct=True)
    sec=d.groupby(['date','sector']).agg(sector_up=('ret1',lambda x:(x>0).mean()),sector_ret=('ret1','mean'),sector_strong=('ret1',lambda x:(x>=THRESH).mean())).reset_index(); return d.merge(sec,on=['date','sector'],how='left')

def pair_history(d):
    src=d[d.ret1>=THRESH][['date','market','ret1','sector','price_band','size_pct','market_up','market_strong','sector_up','sector_strong']].rename(columns={'market':'source','ret1':'source_ret','sector':'source_sector','price_band':'source_band','size_pct':'source_size','sector_up':'source_sector_up','sector_strong':'source_sector_strong'})
    tgt=d[['date','market','sector','price_band','size_pct','next_ret']].copy(); z=tgt.merge(src,on='date'); z=z[z.market!=z.source].dropna(subset=['next_ret']); z['same_sector']=(z.sector==z.source_sector).astype(int); z['same_band']=(z.price_band==z.source_band).astype(int); z['target_up']=(z.next_ret>0).astype(int); return z

def patterns(h):
    if h.empty:return pd.DataFrame()
    p=h.groupby(['source','market']).agg(observations=('target_up','size'),up_count=('target_up','sum'),avg_next_ret=('next_ret','mean'),median_next_ret=('next_ret','median'),same_sector=('same_sector','mean'),same_band=('same_band','mean')).reset_index()
    p['bayes_rate']=(p.up_count+10)/(p.observations+20); p['confidence']=1-np.exp(-p.observations/30); p['pattern_score']=(p.bayes_rate-.5)*200*p.confidence
    return p[p.observations>=MIN_OBS].sort_values('pattern_score',ascending=False)

def bn_features(b):
    if b.empty:return pd.DataFrame(columns=['market','date','bn_ret1','bn_lead'])
    b=b.sort_values(['symbol','date']); b['bn_ret1']=b.groupby('symbol').close.pct_change(); b['market']='KRW-'+b.symbol.str[:-4]; b['bn_lead']=(b.bn_ret1>=.03).astype(int); return b[['market','date','bn_ret1','bn_lead']]

def current(d,p,bn):
    x=d.sort_values('date').groupby('market').tail(1).copy().merge(bn,on=['market','date'],how='left'); x[['bn_ret1','bn_lead']]=x[['bn_ret1','bn_lead']].fillna(0)
    src=x[x.ret1>=THRESH][['market','sector','price_band','size_pct','ret1']].rename(columns={'market':'source','ret1':'source_ret'})
    if src.empty:x['pair_score']=0;x['pattern_rate']=.5;x['pattern_obs']=0;x['best_source']='';x['pattern_avg_ret']=0
    else:
        a=x[['market','sector','price_band','size_pct']].assign(k=1); src=src.assign(k=1); z=a.merge(src,on='k'); z=z[z.market!=z.source]
        z=z.merge(p[['source','market','pattern_score','bayes_rate','observations','avg_next_ret']],on=['source','market'],how='left').dropna(subset=['pattern_score']); z['context']=1+.5*(z.sector_x==z.sector_y)+.25*(z.price_band_x==z.price_band_y); z['eff']=z.pattern_score*z.context
        a2=z.sort_values('eff',ascending=False).groupby('market').head(1)[['market','eff','bayes_rate','observations','source','avg_next_ret']].rename(columns={'eff':'pair_score','bayes_rate':'pattern_rate','observations':'pattern_obs','source':'best_source','avg_next_ret':'pattern_avg_ret'}); x=x.merge(a2,on='market',how='left')
        for c in ['pair_score','pattern_obs','pattern_avg_ret']:x[c]=x[c].fillna(0)
        x['pattern_rate']=x.pattern_rate.fillna(.5); x['best_source']=x.best_source.fillna('')
    x['technical_score']=20+25*x.above20.astype(int)+20*x.above50.astype(int)+15*x.rsi.between(50,70).astype(int)+10*(x.value_ratio>1.5).astype(int)+10*x.ma20_up.astype(int)
    # Final weights are intentionally unchanged from the validated baseline; self-continuation is not a score component.
    x['score']=(.55*(50+x.pair_score/2).clip(0,100)+.20*x.technical_score+.15*x.pattern_rate*100+.10*(50+25*x.bn_lead)).clip(0,100)
    def why(r):
        q=[]
        if r.best_source:q.append('source '+r.best_source)
        if r.pattern_obs>=20:q.append(f'패턴 {int(r.pattern_obs)}회')
        if r.pattern_rate>=.60:q.append(f'과거 상승률 {r.pattern_rate:.0%}')
        if r.bn_lead:q.append('Binance 선행')
        if r.value_ratio>1.5:q.append('거래대금 증가')
        if r.above20 and r.above50:q.append('20/50MA 상단')
        return ', '.join(q)
    x['reason']=x.apply(why,axis=1)
    def leader_label(v):
        if v>=.10:return '급등 선행'
        if v>=.05:return '강한 선행'
        if v>=.03:return '상승 선행'
        return '과거 선행'
    x['source_strength']=x.best_source.map(dict(zip(src.source,src.source_ret))) if len(src) else np.nan
    x['source_strength']=x['source_strength'].fillna(0); x['source_label']=x.source_strength.map(leader_label)
    return x.sort_values('score',ascending=False)

def backtest(d):
    dates=sorted(d.date.unique())[-90:]; out=[]
    for day in dates:
        prior=d[d.date<day]; p=patterns(pair_history(prior)); today=d[d.date==day]; src=today[today.ret1>=THRESH][['market']].rename(columns={'market':'source'})
        if p.empty or src.empty:continue
        z=today[['market']].assign(k=1).merge(src.assign(k=1),on='k'); z=z[z.market!=z.source].merge(p[['source','market','pattern_score']],on=['source','market']).sort_values('pattern_score',ascending=False).groupby('market').head(1).sort_values('pattern_score',ascending=False).head(TOP)
        rr=today.set_index('market').next_ret.reindex(z.market).dropna()
        if len(rr):out.append([day,rr.mean(),(rr>0).mean()])
    if not out:return {}
    r=pd.DataFrame(out,columns=['date','ret','hit']); eq=(1+r.ret).cumprod(); dd=eq/eq.cummax()-1; mu=r.ret.mean(); sd=r.ret.std(); return {'days':len(r),'top_n':TOP,'avg_daily_return':float(mu),'total_return':float(eq.iloc[-1]-1),'hit_rate':float(r.hit.mean()),'max_drawdown':float(dd.min()),'sharpe_approx':float(mu/sd*np.sqrt(365)) if sd else 0}

def continuation_backtest(d, mult=1.5):
    # Validation-only diagnostic. Deliberately excluded from final score.
    dates=sorted(d.date.unique())[-90:]; rows=[]
    for day in dates:
        x=d[(d.date==day)&(d.ret1>=THRESH)&(d.value_ratio>=mult)&(d.vol_ratio>=mult)].copy(); rr=x.set_index('market').next_ret.dropna()
        if len(rr):rows.append([day,float(rr.mean()),float((rr>0).mean()),len(rr)])
    if not rows:return {}
    r=pd.DataFrame(rows,columns=['date','ret','hit','n']); eq=(1+r.ret).cumprod(); dd=eq/eq.cummax()-1; mu=r.ret.mean(); sd=r.ret.std(ddof=1)
    return {'days':len(r),'avg_daily_return':float(mu),'total_return':float(eq.iloc[-1]-1),'hit_rate':float(r.hit.mean()),'max_drawdown':float(dd.min()),'sharpe_approx':float(mu/sd*np.sqrt(365)) if sd and sd>0 else 0,'avg_candidates_per_day':float(r.n.mean()),'condition':f'ret>=5%, value_ratio>={mult:.1f}x, vol_ratio>={mult:.1f}x'}

def write_outputs(d,h,p,c,bn,bt,diag):
    c.to_csv(OUT/'candidates_final.csv',index=False,encoding='utf-8-sig'); p.to_csv(OUT/'pair_patterns_final.csv',index=False,encoding='utf-8-sig')
    # Detailed occurrence history for the current best source -> target pairs.
    # This is the evidence layer used by the detail/PRO view; it is not a new score component.
    best=c[(c.best_source.fillna('').astype(str).ne('')) & (c.pattern_obs>0)][['best_source','market']].copy()
    best=best.rename(columns={'best_source':'source'})
    occ=h.merge(best,on=['source','market'],how='inner')
    occ=occ[['date','source','source_ret','market','next_ret','target_up','same_sector','same_band']].copy()
    occ.rename(columns={'date':'source_date','market':'target','next_ret':'target_next_ret'},inplace=True)
    occ['target_up']=occ['target_up'].astype(int)
    occ=occ.sort_values(['source','target','source_date'],ascending=[True,True,False])
    occ.to_csv(OUT/'pattern_occurrences_final.csv',index=False,encoding='utf-8-sig')
    # Current leader -> target flows, one row per target.
    flow=c[(c.best_source!='') & (c.pattern_obs>0)].copy()
    flow=flow[['best_source','source_strength','source_label','market','score','pattern_rate','pattern_obs','pattern_avg_ret','reason']]
    flow.rename(columns={'best_source':'source','market':'target'},inplace=True); flow.to_csv(OUT/'leader_flows_final.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame([bt]).to_csv(OUT/'backtest_final.csv',index=False)
    pd.DataFrame([diag['self_1_5x'],diag['self_2x'],diag['self_3x']]).to_csv(OUT/'validation_self_final.csv',index=False)
    report={'decision':'A→B transition is the production signal; self-continuation is validation-only and excluded from scoring.','A→B':bt,'self_continuation':diag,'production_score_weights':{'A→B/pair':0.55,'technical':0.20,'pattern_rate':0.15,'Binance_lead':0.10},'generated_at':datetime.now().isoformat()}
    (OUT/'validation_final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'last_run_final.json').write_text(json.dumps({'run_at':datetime.now().isoformat(),'markets':int(d.market.nunique()),'rows':int(len(d)),'patterns':int(len(p)),'backtest':bt},ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    dbs()
    if MODE!='cache':
        markets=up_markets(); print('Upbit KRW markets:',len(markets)); sync_up(markets); valid=binance_symbols(); print('Binance USDT spot symbols:',len(valid)); sync_bn(markets,valid)
    else:
        print('DATA MODE: cache (no network; local SQLite only)')
    if not UPDB.exists() or not BNDB.exists(): raise RuntimeError('Local DB not found. Run run_first_bootstrap.bat once.')
    d=features(load(UPDB)); b=bn_features(load(BNDB)); h=pair_history(d); p=patterns(h); c=current(d,p,b); bt=backtest(d)
    diag={'self_1_5x':continuation_backtest(d,1.5),'self_2x':continuation_backtest(d,2.0),'self_3x':continuation_backtest(d,3.0)}
    write_outputs(d,h,p,c,b,bt,diag)
    cols=['market','score','pattern_rate','pattern_obs','pair_score','technical_score','bn_lead','reason']
    print('\n===== FINAL TOP 30 ====='); print(c[cols].head(TOP).to_string(index=False))
    print('\n===== A→B TRANSITION BACKTEST ====='); print(json.dumps(bt,ensure_ascii=False,indent=2))
    print('\n===== SELF CONTINUATION DIAGNOSTIC (NOT USED IN SCORE) ====='); print(json.dumps(diag,ensure_ascii=False,indent=2))
    print('\nFINAL DECISION: A→B production signal / self-continuation excluded from score.')
    print(f'Outputs saved to: {OUT}')

if __name__=='__main__':main()
