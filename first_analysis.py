from pathlib import Path
import os, subprocess, sys, traceback
ROOT=Path(__file__).resolve().parent
default_home = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.coinpattern'))) / 'CoinPattern'
APP_HOME = Path(os.environ.get('COIN_PATTERN_HOME', str(default_home))).expanduser()
RUNTIME=APP_HOME/'runtime'
RUNTIME.mkdir(parents=True, exist_ok=True)
LOCK=RUNTIME/'.first_analysis.lock'
LOG=RUNTIME/'first_analysis.log'
STATUS=RUNTIME/'first_analysis_status.txt'
try:
    LOCK.write_text('running', encoding='utf-8')
    STATUS.write_text('FIRST ANALYSIS RUNNING', encoding='utf-8')
    env=os.environ.copy(); env['COIN_PATTERN_MODE']='full'; env['PYTHONUNBUFFERED']='1'
    with LOG.open('w', encoding='utf-8', errors='replace') as f:
        p=subprocess.run([sys.executable, str(ROOT/'engine_final.py')], cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, text=True)
    STATUS.write_text('DONE' if p.returncode==0 else f'ERROR:{p.returncode}', encoding='utf-8')
except Exception:
    STATUS.write_text('ERROR:EXCEPTION', encoding='utf-8')
    with LOG.open('a', encoding='utf-8', errors='replace') as f: traceback.print_exc(file=f)
finally:
    try: LOCK.unlink()
    except FileNotFoundError: pass
