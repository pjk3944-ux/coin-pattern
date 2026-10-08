"""Coin Pattern v38 development auth server. LOCAL DEVELOPMENT ONLY.
No real payment, email verification, production TLS, rate limiting, or external deployment is included.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64, hashlib, hmac, json, os, secrets, sqlite3
from subscription_domain_v41 import normalize_event, apply_subscription_event, entitlement_from_subscription
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import time

SESSION_DAYS=int(os.environ.get('COIN_PATTERN_SESSION_DAYS','7'))
RATE_WINDOW=60
RATE_LIMIT=int(os.environ.get('COIN_PATTERN_RATE_LIMIT','10'))
RATE_BUCKETS={}

def rate_ok(key, limit=RATE_LIMIT):
    now_ts=time.time(); arr=RATE_BUCKETS.setdefault(key,[])
    RATE_BUCKETS[key]=[x for x in arr if now_ts-x<RATE_WINDOW]
    if len(RATE_BUCKETS[key])>=limit: return False
    RATE_BUCKETS[key].append(now_ts); return True

DEV_ADMIN_KEY=os.environ.get('COIN_PATTERN_DEV_ADMIN_KEY','coinpattern-v43-dev')
REQUIRE_EMAIL_VERIFIED=os.environ.get('COIN_PATTERN_REQUIRE_EMAIL_VERIFIED','0')=='1'
WEBHOOK_SECRET=os.environ.get('COIN_PATTERN_WEBHOOK_SECRET','coinpattern-v40-webhook-dev')

HOST='127.0.0.1'; PORT=8787
DB='server_dev.sqlite3'

def now(): return datetime.now(timezone.utc)
def iso(dt=None): return (dt or now()).isoformat()
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL)')
    ucols={r[1] for r in c.execute('PRAGMA table_info(users)').fetchall()}
    for name,ddl in [('email_verified','INTEGER NOT NULL DEFAULT 0'),('role',"TEXT NOT NULL DEFAULT 'user'")]:
        if name not in ucols: c.execute(f'ALTER TABLE users ADD COLUMN {name} {ddl}')
    c.execute("CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,actor_user_id TEXT,action TEXT NOT NULL,target_user_id TEXT,ip TEXT,detail_json TEXT)")
    c.execute('CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at TEXT NOT NULL,created_at TEXT NOT NULL,revoked_at TEXT)')
    cols={r[1] for r in c.execute('PRAGMA table_info(sessions)').fetchall()}
    for name,ddl in [('created_at',"TEXT NOT NULL DEFAULT ''"),('revoked_at','TEXT')]:
        if name not in cols: c.execute(f'ALTER TABLE sessions ADD COLUMN {name} {ddl}')
    c.execute("UPDATE sessions SET created_at=? WHERE created_at='' OR created_at IS NULL",(iso(),))
    c.execute('CREATE TABLE IF NOT EXISTS auth_tokens(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,kind TEXT NOT NULL,expires_at TEXT NOT NULL,created_at TEXT NOT NULL,used_at TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,window_start REAL NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS subscriptions(user_id TEXT PRIMARY KEY,plan TEXT NOT NULL,status TEXT NOT NULL,current_period_start TEXT,current_period_end TEXT,cancel_at_period_end INTEGER NOT NULL DEFAULT 0,provider TEXT,provider_customer_id TEXT,provider_subscription_id TEXT,updated_at TEXT NOT NULL)')
    cols={r[1] for r in c.execute('PRAGMA table_info(subscriptions)').fetchall()}
    for name,ddl in [('current_period_start','TEXT'),('cancel_at_period_end','INTEGER NOT NULL DEFAULT 0'),('updated_at',"TEXT NOT NULL DEFAULT ''")]:
        if name not in cols: c.execute(f'ALTER TABLE subscriptions ADD COLUMN {name} {ddl}')
    c.execute("UPDATE subscriptions SET updated_at=? WHERE updated_at='' OR updated_at IS NULL",(iso(),))
    c.execute("CREATE TABLE IF NOT EXISTS webhook_events(event_id TEXT PRIMARY KEY,received_at TEXT NOT NULL,event_type TEXT NOT NULL,payload_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'processed',processed_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS detail_usage(usage_date TEXT NOT NULL,scope_key TEXT NOT NULL,market TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(usage_date,scope_key,market))")
    c.execute("CREATE INDEX IF NOT EXISTS idx_detail_usage_scope_date ON detail_usage(scope_key,usage_date)")
    wcols={r[1] for r in c.execute('PRAGMA table_info(webhook_events)').fetchall()}
    if 'status' not in wcols: c.execute("ALTER TABLE webhook_events ADD COLUMN status TEXT NOT NULL DEFAULT 'processed'")
    if 'processed_at' not in wcols: c.execute('ALTER TABLE webhook_events ADD COLUMN processed_at TEXT')
    c.commit(); return c

def pw_hash(pw,salt=None):
    salt=salt or secrets.token_bytes(16); dk=hashlib.pbkdf2_hmac('sha256',pw.encode(),salt,210000)
    return 'pbkdf2_sha256$210000$'+base64.urlsafe_b64encode(salt).decode()+'$'+base64.urlsafe_b64encode(dk).decode()
def pw_ok(pw,stored):
    try:
        _,it,salt,dk=stored.split('$'); raw=base64.urlsafe_b64decode(salt); want=base64.urlsafe_b64decode(dk)
        got=hashlib.pbkdf2_hmac('sha256',pw.encode(),raw,int(it)); return hmac.compare_digest(got,want)
    except Exception:return False

def audit(c,action,actor_user_id=None,target_user_id=None,ip='',detail=None):
    c.execute('INSERT INTO audit_logs(created_at,actor_user_id,action,target_user_id,ip,detail_json) VALUES(?,?,?,?,?,?)',(iso(),actor_user_id,action,target_user_id,ip,json.dumps(detail or {},ensure_ascii=False,separators=(',',':'))))

def user_from_token(token,c):
    if not token:return None
    h=hashlib.sha256(token.encode()).hexdigest(); r=c.execute('SELECT user_id,expires_at,revoked_at FROM sessions WHERE token_hash=?',(h,)).fetchone()
    if not r or r['revoked_at']:return None
    if r['expires_at'] and datetime.fromisoformat(r['expires_at'])<=now(): c.execute('DELETE FROM sessions WHERE token_hash=?',(h,)); c.commit(); return None
    return c.execute('SELECT * FROM users WHERE id=?',(r['user_id'],)).fetchone()

class H(BaseHTTPRequestHandler):
    def sendj(self,x,code=200):
        raw=json.dumps(x,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def body(self):
        try:
            n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
        except Exception:return {}
    def token(self):
        v=self.headers.get('Authorization',''); return v[7:].strip() if v.startswith('Bearer ') else ''
    def dev_admin_ok(self):
        return self.headers.get('X-CoinPattern-Dev-Key','') == DEV_ADMIN_KEY
    def do_POST(self):
        c=db(); path=urlparse(self.path).path; b=self.body()
        if path=='/v1/register':
            if not rate_ok('register:'+self.client_address[0],5): return self.sendj({'error':'rate_limited'},429)
            email=str(b.get('email','')).strip().lower(); pw=str(b.get('password',''))
            if '@' not in email or len(pw)<10 or len(pw)>128 or email.count('@')!=1:return self.sendj({'error':'invalid_email_or_password'},400)
            try:
                uid='CP-'+secrets.token_hex(6).upper(); c.execute("INSERT INTO users(id,email,password_hash,created_at,email_verified,role) VALUES(?,?,?,?,0,'user')",(uid,email,pw_hash(pw),iso())); c.execute('INSERT INTO subscriptions(user_id,plan,status,current_period_start,current_period_end,cancel_at_period_end,provider,provider_customer_id,provider_subscription_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(uid,'FREE','active',None,None,0,None,None,None,iso())); token=secrets.token_urlsafe(32); h=hashlib.sha256(token.encode()).hexdigest(); exp=now()+timedelta(hours=24)
                c.execute('INSERT INTO auth_tokens VALUES(?,?,?,?,?,?)',(h,uid,'email_verify',iso(exp),iso(),None)); audit(c,'register',target_user_id=uid,ip=self.client_address[0]); c.commit()
            except sqlite3.IntegrityError:return self.sendj({'error':'email_already_exists'},409)
            out=self._login(c,uid,return_json=True); out['email_verified']=False; out['dev_verification_token']=token; out['verification_expires_at']=iso(exp); out['development_only']=True; return self.sendj(out)
        if path=='/v1/login':
            if not rate_ok('login:'+self.client_address[0],10): return self.sendj({'error':'rate_limited'},429)
            email=str(b.get('email','')).strip().lower(); r=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
            if not r or not pw_ok(str(b.get('password','')),r['password_hash']):
                audit(c,'login_failed',target_user_id=(r['id'] if r else None),ip=self.client_address[0],detail={'email':email}); c.commit(); return self.sendj({'error':'invalid_credentials'},401)
            if REQUIRE_EMAIL_VERIFIED and not int(r['email_verified']): return self.sendj({'error':'email_not_verified'},403)
            audit(c,'login_success',target_user_id=r['id'],ip=self.client_address[0]); c.commit(); return self._login(c,r['id'])
        if path=='/v1/logout':
            t=self.token(); c.execute('UPDATE sessions SET revoked_at=? WHERE token_hash=?',(iso(),hashlib.sha256(t.encode()).hexdigest())); c.commit(); return self.sendj({'ok':True})
        if path=='/v1/logout_all':
            t=self.token(); u=user_from_token(t,c)
            if not u:return self.sendj({'error':'unauthorized'},401)
            c.execute('UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL',(iso(),u['id'])); audit(c,'logout_all',actor_user_id=u['id'],ip=self.client_address[0]); c.commit(); return self.sendj({'ok':True})
        if path=='/v1/account/sessions' and self.command=='POST':
            t=self.token(); u=user_from_token(t,c)
            if not u:return self.sendj({'error':'unauthorized'},401)
            rows=c.execute('SELECT token_hash,created_at,expires_at,revoked_at FROM sessions WHERE user_id=? ORDER BY created_at DESC',(u['id'],)).fetchall()
            current_hash=hashlib.sha256(t.encode()).hexdigest()
            sessions=[]
            for r in rows:
                sessions.append({'session_id':r['token_hash'][:16],'created_at':r['created_at'],'expires_at':r['expires_at'],'revoked':bool(r['revoked_at']),'current':r['token_hash']==current_hash})
            return self.sendj({'sessions':sessions})
        if path=='/v1/account/sessions/revoke' and self.command=='POST':
            t=self.token(); u=user_from_token(t,c)
            if not u:return self.sendj({'error':'unauthorized'},401)
            sid=str(b.get('session_id','')).strip()
            rows=c.execute('SELECT token_hash FROM sessions WHERE user_id=?',(u['id'],)).fetchall()
            match=next((r['token_hash'] for r in rows if r['token_hash'].startswith(sid)),None)
            if not match:return self.sendj({'error':'session_not_found'},404)
            c.execute('UPDATE sessions SET revoked_at=? WHERE token_hash=?',(iso(),match)); audit(c,'session_revoked',actor_user_id=u['id'],ip=self.client_address[0],detail={'session_id':sid}); c.commit()
            return self.sendj({'ok':True})
        if path=='/v1/account/export' and self.command=='POST':
            t=self.token(); u=user_from_token(t,c)
            if not u:return self.sendj({'error':'unauthorized'},401)
            sub=c.execute('SELECT plan,status,current_period_start,current_period_end,cancel_at_period_end,provider,updated_at FROM subscriptions WHERE user_id=?',(u['id'],)).fetchone()
            logs=c.execute('SELECT created_at,action,target_user_id,detail_json FROM audit_logs WHERE actor_user_id=? OR target_user_id=? ORDER BY id DESC LIMIT 500',(u['id'],u['id'])).fetchall()
            audit(c,'account_export',actor_user_id=u['id'],ip=self.client_address[0]); c.commit()
            return self.sendj({'account':{'id':u['id'],'email':u['email'],'created_at':u['created_at'],'email_verified':bool(u['email_verified']),'role':u['role']},'subscription':dict(sub) if sub else None,'audit':[dict(x) for x in logs]})
        if path=='/v1/account/delete' and self.command=='POST':
            t=self.token(); u=user_from_token(t,c)
            if not u:return self.sendj({'error':'unauthorized'},401)
            pw=str(b.get('password',''))
            if not pw_ok(pw,u['password_hash']):return self.sendj({'error':'invalid_password'},403)
            uid=u['id']; old_email=u['email']
            # Redact identifiable audit fields before deleting the account.
            c.execute("UPDATE audit_logs SET actor_user_id=NULL,target_user_id=NULL,ip=NULL,detail_json=? WHERE actor_user_id=? OR target_user_id=?",(json.dumps({'redacted':True,'reason':'account_deleted'},separators=(',',':')),uid,uid))
            c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            c.execute('DELETE FROM auth_tokens WHERE user_id=?',(uid,))
            c.execute('DELETE FROM subscriptions WHERE user_id=?',(uid,))
            c.execute('DELETE FROM users WHERE id=?',(uid,))
            c.commit()
            return self.sendj({'ok':True,'deleted':True})
        if path=='/v1/email/verify':
            token=str(b.get('token','')); h=hashlib.sha256(token.encode()).hexdigest()
            r=c.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND kind='email_verify' AND used_at IS NULL",(h,)).fetchone()
            if not r or datetime.fromisoformat(r['expires_at'])<=now(): return self.sendj({'error':'invalid_or_expired_token'},400)
            c.execute('UPDATE users SET email_verified=1 WHERE id=?',(r['user_id'],)); c.execute('UPDATE auth_tokens SET used_at=? WHERE token_hash=?',(iso(),h)); audit(c,'email_verified',target_user_id=r['user_id'],ip=self.client_address[0]); c.commit()
            return self.sendj({'ok':True,'email_verified':True})
        if path=='/v1/email/resend_verification':
            if not rate_ok('verify:'+self.client_address[0],5): return self.sendj({'error':'rate_limited'},429)
            email=str(b.get('email','')).strip().lower(); u=c.execute('SELECT id,email_verified FROM users WHERE email=?',(email,)).fetchone()
            if not u or int(u['email_verified']): return self.sendj({'ok':True,'message':'If verification is needed, a new link was generated.'})
            token=secrets.token_urlsafe(32); h=hashlib.sha256(token.encode()).hexdigest(); exp=now()+timedelta(hours=24); c.execute('INSERT INTO auth_tokens VALUES(?,?,?,?,?,?)',(h,u['id'],'email_verify',iso(exp),iso(),None)); audit(c,'email_verification_resent',target_user_id=u['id'],ip=self.client_address[0]); c.commit()
            return self.sendj({'ok':True,'dev_verification_token':token,'verification_expires_at':iso(exp),'development_only':True})
        if path=='/v1/password/change':
            t=self.token(); u=user_from_token(t,c)
            if not u:return self.sendj({'error':'unauthorized'},401)
            old=str(b.get('current_password','')); new=str(b.get('new_password',''))
            row=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
            if not row or not pw_ok(old,row['password_hash']):return self.sendj({'error':'invalid_current_password'},401)
            if len(new)<10 or len(new)>128:return self.sendj({'error':'password_policy'},400)
            c.execute('UPDATE users SET password_hash=? WHERE id=?',(pw_hash(new),u['id']))
            c.execute('UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL',(iso(),u['id'])); c.commit()
            return self._login(c,u['id'])
        if path=='/v1/password/request_reset':
            if not rate_ok('reset:'+self.client_address[0],5):return self.sendj({'error':'rate_limited'},429)
            email=str(b.get('email','')).strip().lower(); u=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
            # Dev-only: return a reset token so the flow can be tested without an email provider.
            if not u:return self.sendj({'ok':True,'message':'If the account exists, a reset token was generated.'})
            token=secrets.token_urlsafe(32); h=hashlib.sha256(token.encode()).hexdigest(); exp=now()+timedelta(minutes=15)
            c.execute('INSERT INTO auth_tokens VALUES(?,?,?,?,?,?)',(h,u['id'],'password_reset',iso(exp),iso(),None)); c.commit()
            return self.sendj({'ok':True,'dev_reset_token':token,'expires_at':iso(exp),'development_only':True})
        if path=='/v1/password/reset':
            token=str(b.get('token','')); new=str(b.get('new_password',''))
            if len(new)<10 or len(new)>128:return self.sendj({'error':'password_policy'},400)
            h=hashlib.sha256(token.encode()).hexdigest(); r=c.execute('SELECT * FROM auth_tokens WHERE token_hash=? AND kind=? AND used_at IS NULL',(h,'password_reset')).fetchone()
            if not r or datetime.fromisoformat(r['expires_at'])<=now():return self.sendj({'error':'invalid_or_expired_token'},400)
            c.execute('UPDATE users SET password_hash=? WHERE id=?',(pw_hash(new),r['user_id']))
            c.execute('UPDATE auth_tokens SET used_at=? WHERE token_hash=?',(iso(),h)); c.execute('UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL',(iso(),r['user_id'])); c.commit()
            return self._login(c,r['user_id'])
        if path=='/v1/admin/users' and self.command=='POST':
            if not self.dev_admin_ok(): return self.sendj({'error':'admin_key_required'},403)
            actor=user_from_token(self.token(),c)
            if not actor or actor['role']!='admin': return self.sendj({'error':'admin_required'},403)
            rows=c.execute('SELECT id,email,created_at,email_verified,role FROM users ORDER BY created_at DESC LIMIT 200').fetchall(); audit(c,'admin_list_users',actor_user_id=actor['id'],ip=self.client_address[0]); c.commit()
            return self.sendj({'users':[dict(x) for x in rows]})
        if path=='/v1/admin/audit' and self.command=='POST':
            if not self.dev_admin_ok(): return self.sendj({'error':'admin_key_required'},403)
            actor=user_from_token(self.token(),c)
            if not actor or actor['role']!='admin': return self.sendj({'error':'admin_required'},403)
            limit=max(1,min(int(b.get('limit',200)),500)); action=str(b.get('action','')).strip()
            rows=c.execute('SELECT * FROM audit_logs'+(' WHERE action=?' if action else '')+' ORDER BY id DESC LIMIT ?', ((action,limit) if action else (limit,))).fetchall()
            audit(c,'admin_list_audit',actor_user_id=actor['id'],ip=self.client_address[0],detail={'action':action,'limit':limit}); c.commit()
            return self.sendj({'audit':[dict(x) for x in rows]})
        if path=='/v1/admin/subscription' and self.command=='POST':
            if not self.dev_admin_ok(): return self.sendj({'error':'admin_key_required'},403)
            actor=user_from_token(self.token(),c)
            if not actor or actor['role']!='admin': return self.sendj({'error':'admin_required'},403)
            uid=str(b.get('user_id','')).strip(); event_type=str(b.get('type','')).strip(); days=max(1,min(int(b.get('days',30)),3650))
            allowed=('subscription.created','subscription.updated','subscription.renewed','subscription.trialing','subscription.past_due','subscription.canceled','subscription.expired','subscription.revoked')
            if event_type=='get': return self.sendj({'ok':True,'user':dict(target),'subscription':dict(srow)})
            if event_type not in allowed:return self.sendj({'error':'invalid_event_type'},400)
            target=c.execute('SELECT id,email FROM users WHERE id=?',(uid,)).fetchone(); srow=c.execute('SELECT * FROM subscriptions WHERE user_id=?',(uid,)).fetchone()
            if not target or not srow:return self.sendj({'error':'user_not_found'},404)
            end=now()+timedelta(days=days)
            if event_type in ('subscription.expired','subscription.revoked'): end=now()-timedelta(seconds=1)
            status={'subscription.trialing':'trialing','subscription.past_due':'past_due','subscription.canceled':'canceled','subscription.expired':'expired','subscription.revoked':'expired'}.get(event_type,'active')
            payload={'user_id':uid,'status':status,'current_period_start':iso(),'current_period_end':iso(end),'cancel_at_period_end':event_type=='subscription.canceled','provider':'admin_simulation','provider_customer_id':None,'provider_subscription_id':None}
            try:new_state=apply_subscription_event(dict(srow),normalize_event(event_type,payload))
            except ValueError as ex:return self.sendj({'error':'invalid_subscription_transition','detail':str(ex)},409)
            c.execute("UPDATE subscriptions SET plan=?,status=?,current_period_start=?,current_period_end=?,cancel_at_period_end=?,provider=?,provider_customer_id=?,provider_subscription_id=?,updated_at=? WHERE user_id=?",(new_state['plan'],new_state['status'],new_state.get('current_period_start'),new_state.get('current_period_end'),int(new_state.get('cancel_at_period_end',False)),new_state.get('provider'),new_state.get('provider_customer_id'),new_state.get('provider_subscription_id'),iso(),uid))
            audit(c,'admin_subscription_change',actor_user_id=actor['id'],target_user_id=uid,ip=self.client_address[0],detail={'event_type':event_type,'days':days}); c.commit()
            return self.sendj({'ok':True,'user':dict(target),'subscription':new_state})
        if path=='/v1/dev/bootstrap-admin' and self.command=='POST':
            if not self.dev_admin_ok(): return self.sendj({'error':'dev_admin_required'},403)
            uid=str(b.get('user_id','')).strip(); email=str(b.get('email','')).strip().lower(); r=c.execute('SELECT * FROM users WHERE '+('id=?' if uid else 'email=?'),(uid or email,)).fetchone()
            if not r:return self.sendj({'error':'user_not_found'},404)
            c.execute("UPDATE users SET role='admin' WHERE id=?",(r['id'],)); audit(c,'dev_bootstrap_admin',target_user_id=r['id'],ip=self.client_address[0]); c.commit()
            return self.sendj({'ok':True,'user_id':r['id'],'role':'admin','development_only':True})
        if path=='/v1/detail-usage':
            tkn=self.token(); u=user_from_token(tkn,c) if tkn else None
            market=str(b.get('market','')).strip().upper()
            if not market: return self.sendj({'error':'invalid_market'},400)
            if u:
                scope='user:'+str(u['id']); member=True
                s=c.execute('SELECT * FROM subscriptions WHERE user_id=?',(u['id'],)).fetchone(); ent=entitlement_from_subscription(dict(s) if s else None); pro=bool(ent.get('pro'))
                limit=0 if pro else 20
            else:
                device=str(b.get('device_id','')).strip()
                if not device or len(device)>128: return self.sendj({'error':'device_id_required'},400)
                scope='device:'+device; member=False; pro=False; limit=5
            today=now().date().isoformat()
            exists=c.execute('SELECT 1 FROM detail_usage WHERE usage_date=? AND scope_key=? AND market=?',(today,scope,market)).fetchone()
            used=int(c.execute('SELECT COUNT(*) FROM detail_usage WHERE usage_date=? AND scope_key=?',(today,scope)).fetchone()[0])
            if pro or exists:
                return self.sendj({'allowed':True,'used':used,'limit':limit,'pro':pro,'member':member,'counted':False,'market':market,'date':today})
            if used>=limit:
                return self.sendj({'allowed':False,'used':used,'limit':limit,'pro':pro,'member':member,'counted':False,'market':market,'date':today})
            try:
                c.execute('INSERT INTO detail_usage(usage_date,scope_key,market,created_at) VALUES(?,?,?,?)',(today,scope,market,iso())); c.commit()
            except sqlite3.IntegrityError:
                pass
            used=int(c.execute('SELECT COUNT(*) FROM detail_usage WHERE usage_date=? AND scope_key=?',(today,scope)).fetchone()[0])
            audit(c,'detail_usage',actor_user_id=(u['id'] if u else None),ip=self.client_address[0],detail={'market':market,'member':member,'pro':pro,'used':used,'limit':limit}); c.commit()
            return self.sendj({'allowed':True,'used':used,'limit':limit,'pro':pro,'member':member,'counted':True,'market':market,'date':today})
        if path=='/v1/webhooks/payment':
            sig=self.headers.get('X-CoinPattern-Webhook-Signature','')
            raw=json.dumps(b,ensure_ascii=False,separators=(',',':')).encode()
            expected=hmac.new(WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest()
            if not sig or not hmac.compare_digest(sig,expected): return self.sendj({'error':'invalid_webhook_signature'},401)
            event_id=str(b.get('event_id','')).strip(); event_type=str(b.get('type','')).strip(); data=b.get('data') or {}
            if not event_id or not event_type: return self.sendj({'error':'invalid_webhook'},400)
            payload_hash=hashlib.sha256(raw).hexdigest()
            old=c.execute('SELECT event_id FROM webhook_events WHERE event_id=?',(event_id,)).fetchone()
            if old: return self.sendj({'ok':True,'duplicate':True,'event_id':event_id})
            uid=str(data.get('user_id','')).strip()
            if not uid: return self.sendj({'error':'missing_user_id'},400)
            u=c.execute('SELECT id FROM users WHERE id=?',(uid,)).fetchone()
            if not u: return self.sendj({'error':'user_not_found'},404)
            s=c.execute('SELECT * FROM subscriptions WHERE user_id=?',(uid,)).fetchone()
            if event_type not in ('subscription.created','subscription.updated','subscription.renewed','subscription.trialing','subscription.past_due','subscription.canceled','subscription.expired','subscription.revoked'):
                return self.sendj({'error':'unsupported_event_type'},400)
            try:
                new_state=apply_subscription_event(dict(s),normalize_event(event_type,data))
                c.execute("UPDATE subscriptions SET plan=?,status=?,current_period_start=?,current_period_end=?,cancel_at_period_end=?,provider=?,provider_customer_id=?,provider_subscription_id=?,updated_at=? WHERE user_id=?",(new_state['plan'],new_state['status'],new_state.get('current_period_start'),new_state.get('current_period_end'),int(new_state.get('cancel_at_period_end',False)),new_state.get('provider'),new_state.get('provider_customer_id'),new_state.get('provider_subscription_id'),iso(),uid))
            except ValueError as ex:
                return self.sendj({'error':'invalid_subscription_transition','detail':str(ex)},409)
            c.execute('INSERT INTO webhook_events(event_id,received_at,event_type,payload_hash,status,processed_at) VALUES(?,?,?,?,?,?)',(event_id,iso(),event_type,payload_hash,'processed',iso())); c.commit()
            return self.sendj({'ok':True,'event_id':event_id,'processed':True,'subscription':new_state})
        if path=='/v1/dev/webhook':
            if not self.dev_admin_ok(): return self.sendj({'error':'dev_admin_required'},403)
            t=self.token(); u=user_from_token(t,c)
            if not u: return self.sendj({'error':'unauthorized'},401)
            event_type=str(b.get('type','')).strip(); days=max(1,min(int(b.get('days',7)),3650))
            if event_type not in ('subscription.created','subscription.updated','subscription.renewed','subscription.trialing','subscription.past_due','subscription.canceled','subscription.expired','subscription.revoked'):
                return self.sendj({'error':'invalid_event_type'},400)
            active_end=now()+timedelta(days=days); end=active_end if event_type not in ('subscription.expired','subscription.revoked') else now()-timedelta(seconds=1)
            status='trialing' if event_type=='subscription.trialing' else ('past_due' if event_type=='subscription.past_due' else ('canceled' if event_type=='subscription.canceled' else ('expired' if event_type in ('subscription.expired','subscription.revoked') else 'active')))
            payload={'event_id':'dev-'+secrets.token_hex(8),'type':event_type,'data':{'user_id':u['id'],'status':status,'current_period_start':iso(),'current_period_end':iso(end),'cancel_at_period_end':event_type=='subscription.canceled','provider':'dev_simulation','provider_customer_id':None,'provider_subscription_id':None}}
            raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(); sig=hmac.new(WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest()
            return self._process_signed_webhook(c,payload,sig,simulation=True)
        if path=='/v1/dev/subscription':
            if not self.dev_admin_ok(): return self.sendj({'error':'dev_admin_required'},403)
            t=self.token(); u=user_from_token(t,c)
            if not u: return self.sendj({'error':'unauthorized'},401)
            action=str(b.get('action','')).lower(); days=max(1,min(int(b.get('days',7)),3650))
            mapping={'activate':'subscription.updated','trial':'subscription.trialing','revoke':'subscription.revoked','expire':'subscription.expired'}
            et=mapping.get(action)
            if not et:return self.sendj({'error':'invalid_action'},400)
            payload={'event_id':'dev-'+secrets.token_hex(8),'type':et,'data':{'user_id':u['id'],'status':('trialing' if action=='trial' else 'active'),'current_period_start':iso(),'current_period_end':iso(now()+timedelta(days=days)) if action in ('activate','trial') else iso(now()-timedelta(seconds=1)),'cancel_at_period_end':False,'provider':'dev_simulation','provider_customer_id':None,'provider_subscription_id':None}}
            if action=='revoke': payload['data']['status']='expired'
            raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(); sig=hmac.new(WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest()
            return self._process_signed_webhook(c,payload,sig,simulation=True)
        return self.sendj({'error':'not_found'},404)
    def _process_signed_webhook(self,c,payload,sig,simulation=False):
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        expected=hmac.new(WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,expected): return self.sendj({'error':'invalid_webhook_signature'},401)
        event_id=str(payload.get('event_id','')).strip(); event_type=str(payload.get('type','')).strip(); data=payload.get('data') or {}
        old=c.execute('SELECT event_id FROM webhook_events WHERE event_id=?',(event_id,)).fetchone()
        if old:return self.sendj({'ok':True,'duplicate':True,'event_id':event_id,'simulation':simulation})
        uid=str(data.get('user_id','')).strip(); s=c.execute('SELECT * FROM subscriptions WHERE user_id=?',(uid,)).fetchone()
        if not s:return self.sendj({'error':'user_or_subscription_not_found'},404)
        new_state=apply_subscription_event(dict(s),normalize_event(event_type,data))
        c.execute("UPDATE subscriptions SET plan=?,status=?,current_period_start=?,current_period_end=?,cancel_at_period_end=?,provider=?,provider_customer_id=?,provider_subscription_id=?,updated_at=? WHERE user_id=?",(new_state['plan'],new_state['status'],new_state.get('current_period_start'),new_state.get('current_period_end'),int(new_state.get('cancel_at_period_end',False)),new_state.get('provider'),new_state.get('provider_customer_id'),new_state.get('provider_subscription_id'),iso(),uid))
        c.execute('INSERT INTO webhook_events(event_id,received_at,event_type,payload_hash,status,processed_at) VALUES(?,?,?,?,?,?)',(event_id,iso(),event_type,hashlib.sha256(raw).hexdigest(),'processed',iso())); c.commit()
        return self.sendj({'ok':True,'event_id':event_id,'processed':True,'simulation':simulation,'subscription':new_state})

    def _login(self,c,uid,return_json=False):
        token=secrets.token_urlsafe(32); exp=now()+timedelta(days=SESSION_DAYS); c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at,revoked_at) VALUES(?,?,?,?,NULL)',(hashlib.sha256(token.encode()).hexdigest(),uid,iso(exp),iso())); c.commit(); out={'access_token':token,'token_type':'Bearer','expires_at':iso(exp)}
        if return_json:return out
        return self.sendj(out)
    def do_GET(self):
        c=db(); path=urlparse(self.path).path; u=user_from_token(self.token(),c); t=now().isoformat()
        if path=='/health':return self.sendj({'ok':True,'api_version':'v1','environment':'development','security_version':'v45'})
        if not u:return self.sendj({'error':'unauthorized'},401)
        if path=='/v1/me':return self.sendj({'id':u['id'],'email':u['email'],'created_at':u['created_at'],'email_verified':bool(u['email_verified']),'role':u['role']})
        if path=='/v1/subscription':
            s=c.execute('SELECT * FROM subscriptions WHERE user_id=?',(u['id'],)).fetchone(); return self.sendj({k:s[k] for k in ('plan','status','current_period_start','current_period_end','cancel_at_period_end','provider','provider_customer_id','provider_subscription_id','updated_at')})
        if path=='/v1/entitlement':
            s=c.execute('SELECT * FROM subscriptions WHERE user_id=?',(u['id'],)).fetchone(); ent=entitlement_from_subscription(dict(s) if s else None)
            return self.sendj({'pro':ent['pro'],'checked_at':t,'expires_at':ent.get('expires_at'),'status':ent.get('status')})
        return self.sendj({'error':'not_found'},404)
    def log_message(self,*a):pass

if __name__=='__main__':
    db().close(); print(f'DEV ONLY: http://{HOST}:{PORT}'); ThreadingHTTPServer((HOST,PORT),H).serve_forever()
