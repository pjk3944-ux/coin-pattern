from __future__ import annotations

import json, os, re, time
from pathlib import Path
from typing import Dict, List, Tuple
import requests

ROOT = Path(__file__).resolve().parent
DEFAULT_HOME = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.coinpattern'))) / 'CoinPattern'
APP_HOME = Path(os.environ.get('COIN_PATTERN_HOME', str(DEFAULT_HOME)))
CACHE_FILE = APP_HOME / 'output' / 'cmc_meta_cache.json'
BASE_URL = 'https://pro-api.coinmarketcap.com/public-api'
TTL = int(os.environ.get('CMC_META_TTL_SECONDS', str(24*3600)))
TIMEOUT = 8

# Coin Pattern display vocabulary. CMC remains the source; these labels only normalize
# CMC's many category names into a compact UI vocabulary.
RULES: List[Tuple[str,str,Tuple[str,...]]] = [
 ('AIMeme','AI · 밈',('ai meme','ai memes','ai memecoin','ai meme coin')),
 ('Layer2','레이어2',('layer 2','layer-2','layer2','rollup','zk rollup','zkrollup','optimistic rollup','scaling')),
 ('Layer1','레이어1',('layer 1','layer-1','layer1','smart contract platform','smart contracts')),
 ('RWA','RWA',('real world assets','real-world assets','rwa','tokenized assets')),
 ('Oracle','오라클',('oracle','oracles')),
 ('Interoperability','네트워크 · 상호운용',('interoperability','interoperable','cross-chain','cross chain','bridge')),
 ('DePIN','DePIN · AI 인프라',('depin','decentralized physical infrastructure','decentralized infrastructure')),
 ('DID','DID',('decentralized identity','digital identity','identity','did')),
 ('Payments','결제 인프라',('payments','payment','remittance')),
 ('DeFi','DeFi',('defi','decentralized finance','dex','lending','yield farming','liquid staking','restaking','derivatives')),
 ('NFT','NFT · 게임',('nft','non-fungible token','gaming','gamefi','game fi','gaming guild')),
 ('Meme','밈',('memes','meme','memecoin','meme coin')),
 ('AI','AI',('artificial intelligence','ai & big data','ai and big data','artificial-intelligence')),
 ('Infrastructure','인프라',('infrastructure','data','indexing','storage','decentralized storage')),
]

def _norm(s):
    return ' '.join(str(s or '').lower().replace('_',' ').replace('-',' ').split())

def _match(text, needle):
    t,n=_norm(text),_norm(needle)
    if not n: return False
    if ' ' in n: return n in t
    return re.search(r'(?<![a-z0-9])'+re.escape(n)+r'(?![a-z0-9])',t) is not None

def _pick(names):
    hits=[]
    for name in names:
        for group,label,needles in RULES:
            if any(_match(name,x) for x in needles):
                hits.append((group,label,name)); break
    if not hits: return None,[]
    # Preserve the first specific match as primary, remaining distinct labels as secondary.
    primary=hits[0]
    secondary=[]
    for _,label,_ in hits[1:]:
        if label!=primary[1] and label not in secondary: secondary.append(label)
    return primary,secondary[:3]

def _load():
    try:
        if CACHE_FILE.exists():
            d=json.loads(CACHE_FILE.read_text(encoding='utf-8'))
            if isinstance(d,dict): return d
    except Exception: pass
    return {'coins':{}}

def _save(d):
    CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
    tmp=CACHE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(CACHE_FILE)

def _fetch_symbol(symbol):
    # CMC's Categories endpoint supports symbol filtering. This is materially better
    # than downloading the whole category catalog and guessing membership by symbol.
    r=requests.get(BASE_URL+'/v1/cryptocurrency/categories',
                   params={'symbol':symbol,'start':1,'limit':5000},
                   headers={'Accept':'application/json'},timeout=TIMEOUT)
    r.raise_for_status()
    payload=r.json()
    data=payload.get('data') if isinstance(payload,dict) else None
    if isinstance(data,dict): data=list(data.values())
    if not isinstance(data,list): raise ValueError('Unexpected CMC category response')
    names=[]
    for c in data:
        if isinstance(c,dict):
            n=c.get('name') or c.get('title')
            if n: names.append(str(n))
    return names

def get_meta(symbol, fallback=None, force_refresh=False):
    symbol=str(symbol or '').upper().strip()
    if not symbol:
        return _fallback(fallback)
    cache=_load(); row=cache.get('coins',{}).get(symbol)
    now=int(time.time())
    if row and not force_refresh and now-int(row.get('updated_at',0))<TTL:
        return row.get('meta') or _fallback(fallback)
    try:
        categories=_fetch_symbol(symbol)
        primary,secondary=_pick(categories)
        if primary:
            group,label,primary_category=primary
            meta={'meta_status':'confirmed','meta_group':group,'meta_label':label,
                  'meta_secondary':secondary,'meta_source':'CoinMarketCap category',
                  'cmc_category':primary_category,'raw_categories':categories[:10],
                  'meta_text':f'같은 메타 · {label}'}
        else:
            meta=_fallback(fallback)
            meta['meta_source']='CMC 조회됨 · 대표 분류 없음' if categories else meta.get('meta_source','')
            meta['raw_categories']=categories[:10]
        cache.setdefault('coins',{})[symbol]={'updated_at':now,'meta':meta}
        _save(cache)
        return meta
    except Exception:
        # Never break the dashboard because an external classification service is unavailable.
        return _fallback(fallback)

def _fallback(fallback):
    if isinstance(fallback,dict) and fallback.get('meta_label'):
        out=dict(fallback)
        out.setdefault('meta_status','fallback')
        out.setdefault('meta_source','Coin Pattern fallback taxonomy')
        out.setdefault('meta_text',f"같은 메타 · {out['meta_label']}")
        return out
    return {'meta_status':'insufficient_data','meta_group':'','meta_label':'','meta_secondary':[],
            'meta_source':'','cmc_category':'','raw_categories':[],
            'meta_text':'메타 분류 데이터 부족'}
