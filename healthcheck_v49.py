"""Small deployment health-check helper; does not expose secrets."""
import sqlite3
from pathlib import Path

def check_db(path):
    p=Path(path)
    if not p.exists(): return {"ok":False,"reason":"db_missing"}
    try:
        with sqlite3.connect(p) as c:
            c.execute("SELECT 1").fetchone()
        return {"ok":True}
    except Exception as e:
        return {"ok":False,"reason":type(e).__name__}

if __name__ == '__main__':
    print({"service":"coin-pattern","ok":True})
