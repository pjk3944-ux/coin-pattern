from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json, math, os, pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'output'
HOST = os.getenv('COIN_PATTERN_ANALYSIS_HOST', '127.0.0.1')
PORT = int(os.getenv('COIN_PATTERN_ANALYSIS_PORT', '8510'))
REQ = ['candidates_final.csv','pair_patterns_final.csv','pattern_occurrences_final.csv','backtest_final.csv','last_run_final.json']

def clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return None
    return v

def records(name):
    p = OUT / name
    if not p.exists(): return []
    df = pd.read_csv(p)
    return [{k: clean(v) for k,v in row.items()} for row in df.to_dict(orient='records')]

def payload():
    ready = all((OUT / x).exists() for x in REQ)
    if not ready:
        return {'ready': False, 'service':'coin-pattern-analysis-v55'}
    try:
        meta=json.loads((OUT/'last_run_final.json').read_text(encoding='utf-8'))
    except Exception:
        meta={}
    return {
        'ready': True,
        'service':'coin-pattern-analysis-v55',
        'version':'v55',
        'meta':meta,
        'candidates':records('candidates_final.csv'),
        'patterns':records('pair_patterns_final.csv'),
        'occurrences':records('pattern_occurrences_final.csv'),
        'backtest':records('backtest_final.csv'),
    }

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, data):
        raw=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path.split('?')[0] == '/health':
            self._send(200, {'ok':True,'service':'coin-pattern-analysis-v55','ready':all((OUT/x).exists() for x in REQ)})
        elif self.path.split('?')[0] == '/v1/analysis':
            self._send(200,payload())
        else: self._send(404, {'error':'not_found'})
    def log_message(self, fmt, *args):
        print('[analysis-api]', fmt % args)

if __name__=='__main__':
    print(f'Coin Pattern analysis service v55: http://{HOST}:{PORT}')
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
