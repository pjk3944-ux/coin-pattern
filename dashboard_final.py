from pathlib import Path
import json, os, subprocess, sys, uuid
from datetime import datetime, timedelta
import pandas as pd
import requests
import streamlit as st
from cmc_meta import get_meta as cmc_get_meta

ROOT = Path(__file__).resolve().parent
# v57: analysis data/output persist in a stable user directory across app updates.
default_home = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.coinpattern'))) / 'CoinPattern'
APP_HOME = Path(os.environ.get('COIN_PATTERN_HOME', str(default_home)))
OUT = APP_HOME / 'output'
SECTOR_MAP_FILE = ROOT / 'sector_map.csv'
PRO_UNLOCKED = False  # Flip to True only when the real launch/payment stack is ready.

# v55: centralized analysis service architecture. The client reads prepared results from the analysis API;
# beta/local fallback can still read the local output folder. Full 730-day bootstrap is a server/admin job only.
# v54: responsive mobile-web + desktop product UI polish.
# v21: local reaction/retention measurement scaffold. No payment is enabled.
ANALYTICS_DIR = OUT / 'analytics'
DEVICE_FILE = ANALYTICS_DIR / 'device_id.txt'
EVENT_FILE = ANALYTICS_DIR / 'events.csv'
DAILY_FILE = ANALYTICS_DIR / 'daily_snapshot.csv'
WATCH_FILE = ANALYTICS_DIR / 'watchlist.csv'
PROFILE_FILE = ANALYTICS_DIR / 'profile.json'
ONBOARDING_FILE = ANALYTICS_DIR / 'onboarding.json'
PRO_INTENT_FILE = ANALYTICS_DIR / 'pro_intent.csv'
PRO_PREVIEW_FILE = ANALYTICS_DIR / 'pro_preview.csv'
# Existing-data reuse helper.
def _find_existing_coinpattern_home():
    candidates = []
    env_home = os.environ.get("COIN_PATTERN_HOME")
    if env_home:
        candidates.append(Path(env_home).expanduser())
    candidates += [Path.home() / "AppData" / "Local" / "CoinPattern", Path.home() / "CoinPattern_Data"]
    try:
        here = Path(__file__).resolve().parent
        candidates += [here / "data", here / "output", here.parent / "data", here.parent / "output"]
    except Exception:
        pass
    for p in candidates:
        try:
            if (p / "data" / "market.db").exists() or (p / "market.db").exists(): return p
            if (p / "output" / "candidates_final.csv").exists() or (p / "candidates_final.csv").exists(): return p
        except Exception: pass
    return None

EXISTING_COINPATTERN_HOME = _find_existing_coinpattern_home()

ENTITLEMENT_FILE = ANALYTICS_DIR / 'entitlement_test.json'
SERVER_CONFIG_FILE = ANALYTICS_DIR / 'server_config.json'
AUTH_TOKEN_FILE = ANALYTICS_DIR / 'auth_token.txt'

def device_id():
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    if DEVICE_FILE.exists():
        return DEVICE_FILE.read_text(encoding='utf-8').strip()
    value = uuid.uuid4().hex
    DEVICE_FILE.write_text(value, encoding='utf-8')
    return value

DEVICE_ID = device_id()


def load_profile():
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    if PROFILE_FILE.exists():
        try:
            p=json.loads(PROFILE_FILE.read_text(encoding='utf-8'))
            if p.get('member_id'): return p
        except Exception:
            pass
    p={'member_id':'CP-'+uuid.uuid4().hex[:10].upper(),'nickname':'','created_at':datetime.now().isoformat(timespec='seconds')}
    PROFILE_FILE.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    return p

PROFILE=load_profile()

def load_server_config():
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    if SERVER_CONFIG_FILE.exists():
        try:
            d=json.loads(SERVER_CONFIG_FILE.read_text(encoding='utf-8'))
            if isinstance(d,dict): return d
        except Exception:
            pass
    # Beta: the bundled local auth server is the account gate for free-member features.
    return {'mode':'local_dev','base_url':'http://127.0.0.1:8787','environment':'development','dev_admin_key':'coinpattern-v43-dev'}

SERVER_CONFIG=load_server_config()

def load_auth_token():
    try: return AUTH_TOKEN_FILE.read_text(encoding='utf-8').strip() if AUTH_TOKEN_FILE.exists() else ''
    except Exception: return ''

def save_auth_token(token):
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    if token: AUTH_TOKEN_FILE.write_text(str(token),encoding='utf-8')
    elif AUTH_TOKEN_FILE.exists(): AUTH_TOKEN_FILE.unlink()

def server_request(path, method='GET', payload=None):
    base=str(SERVER_CONFIG.get('base_url','')).rstrip('/')
    token=load_auth_token()
    if not base or not token: return None
    try:
        headers={'Authorization':'Bearer '+token}
        if SERVER_CONFIG.get('dev_admin_key'): headers['X-CoinPattern-Dev-Key']=str(SERVER_CONFIG.get('dev_admin_key'))
        r=requests.request(method,base+path,json=payload,headers=headers,timeout=4)
        return r.json() if r.headers.get('content-type','').startswith('application/json') else None
    except Exception: return None

def server_entitlement():
    return server_request('/v1/entitlement')

def current_server_user():
    """Return the authenticated server user for member-only beta features."""
    token=load_auth_token()
    if not token:
        return None
    me=server_request('/v1/me')
    return me if isinstance(me,dict) and me.get('id') else None

def detail_usage(market):
    """Server-side daily unique-coin detail allowance. Anonymous users get 5/day, free members 20/day, PRO unlimited."""
    market=str(market or '').strip().upper()
    if not market:
        return {'allowed':False,'used':0,'limit':0,'pro':False,'member':False,'error':'invalid_market'}
    base=str(SERVER_CONFIG.get('base_url','')).rstrip('/')
    token=load_auth_token()
    try:
        headers={}
        payload={'market':market}
        if token:
            headers['Authorization']='Bearer '+token
        else:
            payload['device_id']=DEVICE_ID
        r=requests.post(base+'/v1/detail-usage',json=payload,headers=headers,timeout=4)
        data=r.json()
        if isinstance(data,dict): return data
    except Exception:
        pass
    return {'allowed':True,'used':0,'limit':5 if not token else 20,'pro':False,'member':bool(token),'server_unavailable':True}

def render_detail_usage_meter(info):
    """Compact premium usage meter for daily unique-coin detail views."""
    used=max(0,int(info.get('used',0) or 0))
    limit=max(0,int(info.get('limit',0) or 0))
    member=bool(info.get('member'))
    pro=bool(info.get('pro'))
    if pro:
        st.markdown(
            '<div class="detail-usage pro">'
            '<div class="du-top"><div><span class="du-kicker">DAILY DETAIL</span>'
            '<span class="du-title">오늘 상세분석</span></div>'
            '<span class="du-pro-badge">💎 PRO · 무제한</span></div>'
            '<div class="du-sub">원하는 코인을 제한 없이 확인할 수 있습니다.</div>'
            '</div>', unsafe_allow_html=True)
        return
    shown=min(used,limit)
    remain=max(0,limit-shown)
    pct=0 if limit<=0 else min(100, int(round(shown/limit*100)))
    label='무료 회원' if member else '비회원'
    remain_text='오늘 남은 상세분석 없음' if remain==0 else f'오늘 {remain}개 남음'
    state='full' if remain==0 else 'normal'
    st.markdown(
        f'<div class="detail-usage {state}">'
        f'<div class="du-top"><div><span class="du-kicker">DAILY DETAIL · {label.upper()}</span>'
        f'<span class="du-title">오늘 상세분석 <b>{shown}</b><i>/{limit}</i></span></div>'
        f'<span class="du-remain">{remain_text}</span></div>'
        f'<div class="du-track"><div class="du-fill" style="width:{pct}%"></div></div>'
        f'<div class="du-bottom"><span>고유 코인 기준 · 같은 코인은 다시 열어도 차감되지 않습니다.</span>'
        f'<b>{pct}% 사용</b></div>'
        '</div>', unsafe_allow_html=True)

def render_detail_limit_gate(info, selected):
    used=int(info.get('used',0) or 0); limit=int(info.get('limit',5) or 5); member=bool(info.get('member')); pro=bool(info.get('pro'))
    if pro: return
    if member:
        headline='오늘 무료 상세분석 20개를 모두 확인했습니다.'
        sub='더 깊은 분석과 전체 패턴 증거는 PRO에서 제공할 수 있습니다.'
    else:
        headline='오늘 무료 상세분석 5개를 모두 확인했습니다.'
        sub='무료 회원가입하면 하루 20개까지 확인할 수 있습니다.'
    st.markdown(f"""<div class="member-gate"><div class="member-gate-icon">🔒</div><div class="member-gate-copy"><div class="member-gate-kicker">DAILY DETAIL LIMIT</div><h3>{headline}</h3><p>{sub}</p><p style="margin-top:7px;color:#7f9ab5">오늘 확인한 고유 코인 {used}개 · 무료 회원 {20 if member else 5}개/일</p></div></div>""",unsafe_allow_html=True)
    if member:
        st.info('PRO가 필요하다면 상단의 💎 PRO 탭에서 제공 기능을 확인할 수 있습니다.')
        return
    st.markdown('### ✨ 무료 회원가입')
    st.caption('결제 없이 가입할 수 있으며, 가입하면 오늘부터 하루 20개의 고유 코인을 상세 분석할 수 있습니다.')
    login_tab, reg_tab = st.tabs(['🔐 로그인','✨ 무료 회원가입'])
    with login_tab:
        e=st.text_input('이메일',key=f'detail_login_email_{selected}')
        pw=st.text_input('비밀번호',type='password',key=f'detail_login_pw_{selected}')
        if st.button('로그인하고 계속 보기',type='primary',use_container_width=True,key=f'detail_login_btn_{selected}'):
            try:
                base=str(SERVER_CONFIG.get('base_url','')).rstrip('/')
                rr=requests.post(base+'/v1/login',json={'email':e,'password':pw},timeout=4); data=rr.json()
                if rr.ok and data.get('access_token'):
                    save_auth_token(data['access_token']); track('server_login','detail_limit'); st.rerun()
                else: st.error(data.get('error','로그인 실패'))
            except Exception as ex: st.error(f'회원 서버 연결 실패: {ex}')
    with reg_tab:
        e2=st.text_input('이메일',key=f'detail_reg_email_{selected}')
        pw2=st.text_input('비밀번호 (10자 이상)',type='password',key=f'detail_reg_pw_{selected}')
        if st.button('무료 회원가입 후 계속 보기',type='primary',use_container_width=True,key=f'detail_register_btn_{selected}'):
            try:
                base=str(SERVER_CONFIG.get('base_url','')).rstrip('/')
                rr=requests.post(base+'/v1/register',json={'email':e2,'password':pw2},timeout=4); data=rr.json()
                if rr.ok and data.get('access_token'):
                    save_auth_token(data['access_token']); track('server_register','detail_limit'); st.rerun()
                else: st.error(data.get('error','회원가입 실패'))
            except Exception as ex: st.error(f'회원 서버 연결 실패: {ex}')
    st.caption('무료 회원가입 · 결제 없음 · 하루 20개 상세분석')

def render_member_gate(feature='TOP 30'):
    # Keep the market-flow experience public; only the deeper candidate list requires
    # a free account. No payment or PRO entitlement is required here.
    st.markdown(
        '<div class="member-gate">'
        '<div class="member-gate-icon">🔓</div>'
        '<div class="member-gate-copy"><div class="member-gate-kicker">FREE MEMBER</div>'
        f'<h3>{feature}는 무료 회원에게 공개됩니다.</h3>'
        '<p>오늘의 시장 흐름은 누구나 볼 수 있습니다. 무료 회원이 되면 전체 후보와 관심 코인 기능을 사용할 수 있습니다.</p></div>'
        '</div>', unsafe_allow_html=True)
    if not st.session_state.get('_cp_top30_gate_tracked',False):
        track('top30_member_gate_view')
        st.session_state['_cp_top30_gate_tracked']=True
    if not SERVER_CONFIG.get('base_url'):
        st.warning('회원가입 서버가 연결되지 않았습니다. 설정에서 계정 서버를 확인하세요.')
        return
    login_tab, reg_tab = st.tabs(['🔐 로그인','✨ 무료 회원가입'])
    with login_tab:
        e=st.text_input('이메일',key='top30_login_email')
        pw=st.text_input('비밀번호',type='password',key='top30_login_pw')
        if st.button('로그인하고 TOP 30 보기',type='primary',use_container_width=True,key='top30_login_btn'):
            try:
                base=str(SERVER_CONFIG.get('base_url','')).rstrip('/')
                rr=requests.post(base+'/v1/login',json={'email':e,'password':pw},timeout=4)
                data=rr.json()
                if rr.ok and data.get('access_token'):
                    save_auth_token(data['access_token']); track('server_login','top30_gate'); st.rerun()
                else: st.error(data.get('error','로그인 실패'))
            except Exception as ex: st.error(f'회원 서버 연결 실패: {ex}')
    with reg_tab:
        e2=st.text_input('이메일',key='top30_reg_email')
        pw2=st.text_input('비밀번호 (10자 이상)',type='password',key='top30_reg_pw')
        if st.button('무료 회원가입 후 TOP 30 보기',type='primary',use_container_width=True,key='top30_register_btn'):
            try:
                base=str(SERVER_CONFIG.get('base_url','')).rstrip('/')
                rr=requests.post(base+'/v1/register',json={'email':e2,'password':pw2},timeout=4)
                data=rr.json()
                if rr.ok and data.get('access_token'):
                    save_auth_token(data['access_token']); track('server_register','top30_gate'); st.rerun()
                else: st.error(data.get('error','회원가입 실패'))
            except Exception as ex: st.error(f'회원 서버 연결 실패: {ex}')
    st.caption('무료 회원가입 · 결제 없음 · 전체 후보 확인을 위한 계정입니다.')


def render_admin_console(me):
    if not me or me.get('role')!='admin': return
    st.markdown('### 🛠️ 관리자 운영 콘솔 (v45 개발)')
    st.caption('개발 서버 전용 관리자 화면입니다. 실제 운영에서는 별도 관리자 인증/권한 체계로 교체해야 합니다.')
    c1,c2=st.columns([1,2])
    with c1:
        if st.button('👥 회원 목록 새로고침',use_container_width=True,key='admin_refresh_users'):
            st.rerun()
    users=server_request('/v1/admin/users','POST')
    user_rows=(users or {}).get('users',[])
    if not user_rows:
        st.info('회원 데이터가 없습니다.')
        return
    labels={u['id']:f"{u['email']} · {u['id']} · {'관리자' if u.get('role')=='admin' else '회원'}" for u in user_rows}
    uid=st.selectbox('관리 대상 회원',list(labels),format_func=lambda x:labels[x],key='admin_target_user')
    target=next((u for u in user_rows if u['id']==uid),{})
    a,b,c=st.columns(3); a.metric('이메일',target.get('email','-')); b.metric('이메일 인증','완료' if target.get('email_verified') else '미완료'); c.metric('역할',target.get('role','user'))
    sub=server_request('/v1/subscription') if uid==me.get('id') else None
    if uid!=me.get('id'):
        # Admin subscription endpoint returns the resulting state after each action; fetch through a small event only when needed.
        sr=server_request('/v1/admin/subscription','POST',{'user_id':uid,'type':'get'})
        if sr and sr.get('subscription'):
            ss=sr['subscription']; st.write(f"현재 구독: **{ss.get('plan','FREE')}** · **{ss.get('status','-')}** · 만료 {ss.get('current_period_end') or '-'}")
        else: st.caption('선택 회원의 구독 상태를 불러오지 못했습니다.')
    ev_labels={'subscription.updated':'PRO 활성','subscription.trialing':'Trial','subscription.past_due':'결제 지연','subscription.canceled':'기간 종료 예약','subscription.expired':'즉시 만료','subscription.revoked':'PRO 회수'}
    ev=st.selectbox('구독 관리 이벤트',list(ev_labels),format_func=lambda x:ev_labels[x],key='admin_sub_event')
    days=st.number_input('기간(일)',1,3650,30,1,key='admin_sub_days')
    if st.button('구독 상태 적용',type='primary',use_container_width=True,key='admin_apply_sub'):
        r=server_request('/v1/admin/subscription','POST',{'user_id':uid,'type':ev,'days':int(days)})
        if r and r.get('ok'):
            st.success(f"{uid} 구독 상태를 {r.get('subscription',{}).get('status','-')}로 변경했습니다.")
            st.json(r.get('subscription',{})); st.rerun()
        else: st.error('구독 상태 변경 실패: '+str((r or {}).get('error','server error')))
    with st.expander('📋 최근 감사 로그',expanded=False):
        ar=server_request('/v1/admin/audit','POST',{'limit':100})
        if ar and ar.get('audit'):
            st.dataframe(pd.DataFrame(ar['audit']),use_container_width=True,hide_index=True)
        else: st.info('감사 로그가 없습니다.')
    with st.expander('🧪 개발 관리자 승격',expanded=False):
        st.caption('현재 개발 환경에서만 사용합니다. 실제 운영에서는 수동 DB 승격 대신 별도 관리자 초대/권한 승인 절차를 사용해야 합니다.')
        if st.button('선택 회원을 관리자 권한으로 승격',key='dev_promote_admin'):
            r=server_request('/v1/dev/bootstrap-admin','POST',{'user_id':uid})
            if r and r.get('ok'): st.success('관리자 권한을 부여했습니다.'); st.rerun()
            else: st.error('승격 실패: '+str((r or {}).get('error','server error')))

def entitlement_contract_status():
    return {
        'api_contract':'v1',
        'auth':'Bearer access token (server-side validation required)',
        'source_of_truth':'server',
        'client_trust_local_entitlement':False,
        'payment_provider':'not_connected',
        'environment':SERVER_CONFIG.get('environment','not_connected'),
        'base_url_configured':bool(SERVER_CONFIG.get('base_url')),
        'logged_in':bool(load_auth_token())
    }


def load_test_entitlement():
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    if ENTITLEMENT_FILE.exists():
        try:
            d=json.loads(ENTITLEMENT_FILE.read_text(encoding='utf-8'))
            return d if isinstance(d,dict) else {}
        except Exception:
            pass
    return {'mode':'none','plan':'FREE','active':False,'started_at':None,'expires_at':None}

def save_test_entitlement(d):
    ENTITLEMENT_FILE.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')

def effective_pro_unlocked():
    # Production gate remains False. Only the explicit local simulation can unlock PRO.
    if PRO_UNLOCKED:
        return True
    se=server_entitlement()
    if isinstance(se,dict) and se.get('pro') is True:
        return True
    d=load_test_entitlement()
    if d.get('mode')!='local_simulation' or not d.get('active'):
        return False
    exp=d.get('expires_at')
    if exp:
        try:
            if datetime.now() >= datetime.fromisoformat(exp):
                d['active']=False; d['plan']='FREE'; d['mode']='none'; save_test_entitlement(d)
                track('pro_test_expired')
                return False
        except Exception:
            return False
    return True

def activate_test_pro(days=7):
    now=datetime.now()
    d={'mode':'local_simulation','plan':'PRO_TEST','active':True,'started_at':now.isoformat(timespec='seconds'),'expires_at':(now+timedelta(days=days)).isoformat(timespec='seconds')}
    save_test_entitlement(d); track('pro_test_activated',f'{days}days')

def revoke_test_pro():
    save_test_entitlement({'mode':'none','plan':'FREE','active':False,'started_at':None,'expires_at':None})
    track('pro_test_revoked')

def save_profile(nickname):
    PROFILE['nickname']=str(nickname).strip()[:30]
    PROFILE_FILE.write_text(json.dumps(PROFILE,ensure_ascii=False,indent=2),encoding='utf-8')
    track('profile_update','nickname')


def track(event, detail=''):
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{'timestamp': datetime.now().isoformat(timespec='seconds'), 'device_id': DEVICE_ID, 'event': event, 'detail': detail}])
    if EVENT_FILE.exists():
        row.to_csv(EVENT_FILE, mode='a', header=False, index=False, encoding='utf-8-sig')
    else:
        row.to_csv(EVENT_FILE, index=False, encoding='utf-8-sig')





def onboarding_state():
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    if ONBOARDING_FILE.exists():
        try:
            d=json.loads(ONBOARDING_FILE.read_text(encoding='utf-8'))
            return d if isinstance(d,dict) else {}
        except Exception:
            pass
    return {'completed':False,'dismissed':False}

def save_onboarding(**kwargs):
    d=onboarding_state(); d.update(kwargs)
    ONBOARDING_FILE.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')

def render_landing():
    """v29: first-visit product landing inside the app."""
    st.markdown('''
    <div style="background:#0d1929;border:1px solid #29425f;border-radius:22px;padding:30px 30px 24px;margin:4px 0 16px">
      <div style="font-size:11px;letter-spacing:1.8px;color:#7fa5c8;font-weight:900">COIN PATTERN · MARKET FLOW INTELLIGENCE</div>
      <h1 style="font-size:34px;line-height:1.25;margin:12px 0;color:#fff">지금 움직이는 코인 다음에<br><span style="color:#8fcaff">과거에는 어떤 코인이 움직였을까?</span></h1>
      <p style="color:#9db2c9;line-height:1.75;font-size:14px;margin:0 0 22px">Coin Pattern은 미래 가격을 예측하거나 매수 신호를 제공하는 서비스가 아닙니다.<br>실제 시장 데이터에서 반복해서 나타난 <b>A → B 흐름</b>과 현재 시장 상황을 함께 보여줍니다.</p>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
        <div style="background:#13243a;border:1px solid #29425f;border-radius:14px;padding:15px;color:#fff"><b>① 현재 선행</b><br><span style="color:#8ea8c2;font-size:12px">오늘 이미 강하게 움직인 코인을 찾습니다.</span></div>
        <div style="background:#13243a;border:1px solid #29425f;border-radius:14px;padding:15px;color:#fff"><b>② 다음 관심</b><br><span style="color:#8ea8c2;font-size:12px">그 뒤에 과거 어떤 코인이 움직였는지 봅니다.</span></div>
        <div style="background:#13243a;border:1px solid #29425f;border-radius:14px;padding:15px;color:#fff"><b>③ 직접 판단</b><br><span style="color:#8ea8c2;font-size:12px">관찰 횟수·상승률·유사 캔들·시장 맥락을 확인합니다.</span></div>
      </div>
    </div>''', unsafe_allow_html=True)
    a,b,c=st.columns([1,1,1])
    with a:
        if st.button('🚀 Coin Pattern 시작하기',type='primary',use_container_width=True,key='landing_start'):
            track('landing_start'); st.session_state['landing_seen']=True; st.rerun()
    with b:
        if st.button('🔍 어떻게 분석하는지 보기',use_container_width=True,key='landing_explain'):
            track('landing_explain'); st.session_state['landing_seen']=True; st.session_state['show_onboarding']=True; st.rerun()
    with c:
        if st.button('다음부터 이 화면 숨기기',use_container_width=True,key='landing_skip'):
            track('landing_dismissed'); st.session_state['landing_seen']=True; st.rerun()
    st.caption('※ 과거 관찰 데이터 기반 정보 서비스이며 미래 가격이나 수익을 보장하지 않습니다.')

def render_onboarding(c):
    st.markdown('### 🧭 처음이라면 이렇게 써보세요')
    st.caption('Coin Pattern은 매수 신호를 주는 앱이 아니라, 지금 움직인 코인 뒤에 과거 어떤 흐름이 이어졌는지 보여주는 분석 도구입니다.')
    a,b,d=st.columns(3)
    with a:
        st.markdown('<div class="lock-card"><h4>1️⃣ 시장 흐름 확인</h4><p>지금 움직이는 선행 코인과 과거 후행 흐름을 먼저 봅니다.</p></div>',unsafe_allow_html=True)
    with b:
        st.markdown('<div class="lock-card"><h4>2️⃣ 상세 분석</h4><p>A→B 사례·관찰 횟수·선행 코인·유사 캔들을 확인합니다.</p></div>',unsafe_allow_html=True)
    with d:
        st.markdown('<div class="lock-card"><h4>3️⃣ 관심/공유</h4><p>관심 코인으로 저장하고, 분석 카드를 만들어 다른 사람과 공유합니다.</p></div>',unsafe_allow_html=True)
    x,y=st.columns([1,1])
    with x:
        if st.button('🔗 시장 흐름부터 보기',use_container_width=True,key='onboard_flow'):
            track('onboarding_action','flow'); save_onboarding(completed=True); st.info('시장 흐름 탭에서 현재 선행 → 과거 후행 연결을 확인해보세요.')
    with y:
        if st.button('📤 공유 카드부터 보기',use_container_width=True,key='onboard_share'):
            track('onboarding_action','share_card'); save_onboarding(completed=True); st.info('상세 분석 탭에서 코인을 선택하면 공유 카드를 만들 수 있습니다.')
    if st.button('다음부터 이 안내 숨기기',key='onboard_hide'):
        track('onboarding_dismissed'); save_onboarding(completed=True,dismissed=True); st.rerun()

def load_watchlist():
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    if not WATCH_FILE.exists():
        return []
    try:
        d=pd.read_csv(WATCH_FILE)
        return sorted(set(d['market'].dropna().astype(str).tolist())) if 'market' in d.columns else []
    except Exception:
        return []

def save_watchlist(items):
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({'market':sorted(set(items))}).to_csv(WATCH_FILE,index=False,encoding='utf-8-sig')

def toggle_watch(market):
    items=load_watchlist(); market=str(market)
    if market in items:
        items.remove(market); action='removed'
    else:
        items.append(market); action='added'
    save_watchlist(items)
    track('watchlist_change', f'{action}:{market}')
    return items

def render_watchlist(c):
    st.markdown('<div class="section-title"><div><h2>⭐ 관심 코인</h2><p>내가 저장한 코인만 모아서 현재 움직임과 관심도 변화를 확인합니다.</p></div><span class="section-badge">WATCHLIST</span></div>', unsafe_allow_html=True)
    items=load_watchlist()
    if not items:
        st.markdown('<div class="empty">아직 관심 코인이 없습니다. 상세 분석에서 ⭐ 관심 코인을 추가하면 이곳에서 변화 추적이 시작됩니다.</div>', unsafe_allow_html=True)
        return
    rows=[]
    changes=daily_changes(c)
    for m in items:
        hit=c[c.market.astype(str).map(ticker)==ticker(m)]
        if hit.empty: continue
        r=hit.iloc[0]
        live=safe_float(r.get('live_ret',r.get('ret1'))); score=safe_float(r.get('score'))
        ch=changes[changes.market.astype(str).map(ticker)==ticker(m)] if not changes.empty else pd.DataFrame()
        delta=safe_float(ch.iloc[0].get('score_delta')) if not ch.empty else None
        status=[]
        if live>=0.03: status.append('현재 상승 강함')
        elif live<=-0.03: status.append('현재 하락 주의')
        if delta is not None and delta>=5: status.append('관심도 상승')
        if not status: status.append('변화 관찰')
        rows.append({'코인':ticker(m),'현재':f'{live:+.1%}','관심 점수':f'{score:.1f}','관심도 변화':f'{delta:+.1f}점' if delta is not None else '-', '상태':' · '.join(status)})
    if rows:
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    st.caption('※ 상태는 현재 시세와 과거 분석 점수의 변화에 따른 참고용 표시이며 투자 판단이나 수익을 보장하지 않습니다.')
    st.markdown('### 관심 코인 관리')
    for m in items:
        col1,col2=st.columns([5,1])
        with col1: st.write(f'⭐ {ticker(m)}')
        with col2:
            if st.button('삭제',key=f'watch_del_{m}',use_container_width=True):
                toggle_watch(m); st.rerun()

def track_pro_feature_interest(feature, coin=''):
    """v33: record which locked PRO capability the user actually wants to inspect."""
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        row={'timestamp':datetime.now().isoformat(timespec='seconds'),'feature':str(feature),'coin':str(coin)}
        exists=PRO_PREVIEW_FILE.exists()
        pd.DataFrame([row]).to_csv(PRO_PREVIEW_FILE,mode='a',header=not exists,index=False,encoding='utf-8-sig')
        track('pro_feature_interest', f'{feature}|{coin}')
    except Exception:
        pass

def track_pro_intent(intent):
    """v34: record pre-launch willingness to use PRO without payment or signup."""
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        row={'timestamp':datetime.now().isoformat(timespec='seconds'),'intent':str(intent)}
        exists=PRO_INTENT_FILE.exists()
        pd.DataFrame([row]).to_csv(PRO_INTENT_FILE,mode='a',header=not exists,index=False,encoding='utf-8-sig')
        track('pro_intent', str(intent))
    except Exception:
        pass

def pro_intent_counts():
    if not PRO_INTENT_FILE.exists(): return {}
    try:
        e=pd.read_csv(PRO_INTENT_FILE)
        if 'intent' not in e.columns: return {}
        return e['intent'].astype(str).value_counts().to_dict()
    except Exception:
        return {}

def pro_feature_counts():
    if not PRO_PREVIEW_FILE.exists(): return {}
    try:
        e=pd.read_csv(PRO_PREVIEW_FILE)
        if 'feature' not in e.columns: return {}
        return e['feature'].astype(str).value_counts().to_dict()
    except Exception:
        return {}

def local_usage_summary():
    if not EVENT_FILE.exists():
        return 0, 0, 0
    try:
        e=pd.read_csv(EVENT_FILE)
        e['timestamp']=pd.to_datetime(e['timestamp'], errors='coerce')
        e=e.dropna(subset=['timestamp'])
        days=e['timestamp'].dt.date.nunique()
        sessions=int((e['event']=='session_start').sum())
        pro=int(e['event'].isin(['pro_view','pro_interest']).sum())
        return days, sessions, pro
    except Exception:
        return 0,0,0

def retention_summary():
    """v31: derive local pre-launch activation/retention metrics from anonymous event history."""
    base = {
        'first_date': None, 'days_used': 0, 'sessions': 0,
        'd1': False, 'd3': False, 'd7': False,
        'detail': False, 'watchlist': False, 'share': False,
        'pro_view': False, 'pro_interest': False, 'daily_check': False,
    }
    if not EVENT_FILE.exists():
        return base
    try:
        e=pd.read_csv(EVENT_FILE)
        if 'timestamp' not in e.columns or 'event' not in e.columns:
            return base
        e['timestamp']=pd.to_datetime(e['timestamp'], errors='coerce')
        e=e.dropna(subset=['timestamp']).sort_values('timestamp')
        if e.empty: return base
        dates=sorted(set(e['timestamp'].dt.date.tolist()))
        first=dates[0]
        base['first_date']=first.isoformat()
        base['days_used']=len(dates)
        base['sessions']=int((e['event']=='session_start').sum())
        base['d1']=(first+timedelta(days=1)) in dates
        base['d3']=(first+timedelta(days=3)) in dates
        base['d7']=(first+timedelta(days=7)) in dates
        ev=set(e['event'].astype(str).tolist())
        base['detail']=bool({'detail_view','coin_detail_view'} & ev)
        base['watchlist']='watchlist_change' in ev
        base['share']='share_card_created' in ev
        base['pro_view']='pro_view' in ev
        base['pro_interest']='pro_interest' in ev
        base['daily_check']='daily_check_complete' in ev
        return base
    except Exception:
        return base


def render_retention_panel():
    r=retention_summary()
    st.markdown('### 📈 제품 사용 여정')
    if not r['first_date']:
        st.caption('아직 사용 기록이 없습니다. 앱을 사용하면 이 PC에서만 여정이 자동 집계됩니다.')
        return
    st.caption(f"첫 사용일 {r['first_date']} · 이 기기 기준 로컬 측정")
    a,b,c=st.columns(3)
    a.metric('D1 재방문', '확인' if r['d1'] else '미확인')
    b.metric('D3 재방문', '확인' if r['d3'] else '미확인')
    c.metric('D7 재방문', '확인' if r['d7'] else '미확인')
    steps=[
        ('첫 방문', True), ('상세 분석', r['detail']), ('관심 코인', r['watchlist']),
        ('공유 카드', r['share']), ('PRO 관심', r['pro_interest']), ('오늘 확인', r['daily_check'])
    ]
    labels=[]
    for name, ok in steps:
        labels.append(f"{'✅' if ok else '⬜'} {name}")
    st.write(' → '.join(labels))
    st.caption('D1/D3/D7은 첫 사용일 기준 정확히 +1/+3/+7일에 세션이 있었는지만 봅니다. 전체 사용자 유지율이 아니라 이 PC 한 대의 사전 검증 지표입니다.')


def render_return_loop(c, live_ok):
    """v32: give the user a concrete reason to revisit the app today."""
    try:
        pulse = daily_pulse_rows(c)
    except Exception:
        pulse = pd.DataFrame()
    watch = load_watchlist()
    reasons=[]
    if isinstance(watch, pd.DataFrame) and not watch.empty:
        w=set(watch.get('market', pd.Series(dtype=str)).astype(str).tolist())
        if 'market' in c.columns:
            wc=c[c['market'].astype(str).isin(w)].copy()
            if not wc.empty:
                wc['score_delta']=pd.to_numeric(wc.get('score_delta',0),errors='coerce').fillna(0)
                wc['live_ret']=pd.to_numeric(wc.get('live_ret',wc.get('ret1',0)),errors='coerce').fillna(0)
                if (wc['score_delta'].abs() >= 3).any():
                    reasons.append('⭐ 관심 코인의 관심도가 달라졌습니다.')
                elif (wc['live_ret'].abs() >= 0.05).any():
                    reasons.append('📈 관심 코인 중 오늘 움직임이 큰 코인이 있습니다.')
    try:
        if not pulse.empty:
            if 'new' in pulse.columns and pulse['new'].astype(bool).any():
                reasons.append('🆕 오늘 새롭게 TOP 30에 들어온 코인이 있습니다.')
            if 'score_delta' in pulse.columns and pd.to_numeric(pulse['score_delta'],errors='coerce').abs().fillna(0).max() >= 5:
                reasons.append('🔄 관심도 순위가 크게 변한 코인이 있습니다.')
    except Exception:
        pass
    if not reasons:
        reasons=['🧭 오늘의 TOP 5와 시장 흐름을 다시 확인해 보세요.']
    today=datetime.now().date().isoformat()
    st.markdown("""
    <div class='section-title'><div><h2>🔔 오늘 다시 볼 이유</h2><p>어제와 달라진 시장을 빠르게 확인합니다.</p></div></div>
    """,unsafe_allow_html=True)
    for r in reasons[:3]:
        st.markdown(f"<div class='status' style='padding:11px 14px;margin:6px 0'>{r}</div>",unsafe_allow_html=True)
    if st.button('✅ 오늘 확인 완료',use_container_width=True,key='daily_return_check'):
        track('daily_check_complete',today)
        st.success('오늘 확인 기록을 저장했습니다.')


def save_daily_snapshot(c):
    """Store one compact daily snapshot so the next visit can show what changed."""
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().date().isoformat()
        rows=[]
        for i, (_, r) in enumerate(c.head(30).iterrows()):
            rows.append({
                'date': today,
                'market': str(r.get('market','')),
                'score': safe_float(r.get('score')),
                'live_ret': safe_float(r.get('live_ret', r.get('ret1'))),
                'best_source': str(r.get('best_source','')),
                'rank': int(i + 1)
            })
        if not rows: return
        new=pd.DataFrame(rows)
        if DAILY_FILE.exists():
            old=pd.read_csv(DAILY_FILE)
            old=old[old['date'].astype(str).ne(today)]
            out=pd.concat([old,new], ignore_index=True)
        else:
            out=new
        out.to_csv(DAILY_FILE,index=False,encoding='utf-8-sig')
    except Exception:
        pass

def daily_changes(c):
    """Compare today's candidates with the previous saved analysis day."""
    if not DAILY_FILE.exists(): return pd.DataFrame()
    try:
        h=pd.read_csv(DAILY_FILE)
        h['date']=h['date'].astype(str)
        dates=sorted(h['date'].unique())
        today=datetime.now().date().isoformat()
        prev=[d for d in dates if d < today]
        if not prev: return pd.DataFrame()
        prev_df=h[h.date==prev[-1]][['market','score','live_ret','rank']].rename(
            columns={'score':'prev_score','live_ret':'prev_ret','rank':'prev_rank'})
        cur=c[['market','score','live_ret']].copy()
        cur['rank']=range(1,len(cur)+1)
        m=cur.merge(prev_df,on='market',how='left')
        m['score_delta']=m['score']-m['prev_score']
        m['rank_delta']=m['prev_rank']-m['rank']
        m['is_new']=m['prev_rank'].isna()
        return m.sort_values(['is_new','score_delta','rank_delta'],ascending=[False,False,False])
    except Exception:
        return pd.DataFrame()


def render_watch_pulse(c):
    """Show only meaningful changes among saved coins so users have a reason to reopen the app."""
    items=load_watchlist()
    st.markdown('<div class="section-title"><div><h2>⭐ 내 관심 코인 변화</h2><p>저장한 코인 중 오늘 확인할 변화만 빠르게 보여줍니다.</p></div><span class="section-badge">WATCH PULSE</span></div>', unsafe_allow_html=True)
    if not items:
        st.info('관심 코인을 저장하면 이곳에서 오늘의 변화가 자동으로 표시됩니다.')
        return
    changes=daily_changes(c)
    rows=[]
    for m in items:
        hit=c[c.market.astype(str).map(ticker)==ticker(m)]
        if hit.empty: continue
        r=hit.iloc[0]
        live=safe_float(r.get('live_ret',r.get('ret1')))
        score=safe_float(r.get('score'))
        ch=changes[changes.market.astype(str).map(ticker)==ticker(m)] if not changes.empty else pd.DataFrame()
        delta=safe_float(ch.iloc[0].get('score_delta')) if not ch.empty else 0.0
        rank_delta=safe_float(ch.iloc[0].get('rank_delta')) if not ch.empty else 0.0
        if live>=0.05 or live<=-0.05 or delta>=5 or rank_delta>=3:
            rows.append((m,live,score,delta,rank_delta))
    if not rows:
        st.success('현재 큰 변화가 없습니다. 관심 코인 상태가 안정적입니다.')
        return
    rows.sort(key=lambda x:(abs(x[3]),abs(x[1])),reverse=True)
    cards=[]
    for m,live,score,delta,rank_delta in rows[:5]:
        cls='red' if live>0 else 'blue' if live<0 else ''
        tags=[]
        if live>=0.05: tags.append('현재 강한 상승')
        elif live<=-0.05: tags.append('현재 큰 하락')
        if delta>=5: tags.append('관심도 상승')
        if rank_delta>=3: tags.append('순위 상승')
        cards.append(f'<div class="brief-card"><div class="brief-coin">⭐ {ticker(m)}</div><div class="brief-ret {cls}">{live:+.1%}</div><div class="brief-sub">관심 {score:.1f} · 점수 {delta:+.1f}점 · {" · ".join(tags)}</div></div>')
    st.markdown('<div class="brief-grid">'+''.join(cards)+'</div>', unsafe_allow_html=True)

def _live_leader_flow(live_df, p, max_leaders=6, targets_per_leader=4):
    """Current Upbit leaders -> historical A→B next-day targets.

    IMPORTANT: leaders come from the entire live KRW ticker universe, not from
    candidates_final.csv. This prevents a strong live mover such as ORCA from
    disappearing simply because it is not in the historical TOP30 candidate list.
    """
    if live_df is None or live_df.empty or 'market' not in live_df.columns:
        return []
    x=live_df.copy()
    x['__live']=pd.to_numeric(x.get('live_ret'),errors='coerce')
    x=x.dropna(subset=['market','__live']).copy()
    x['market']=x['market'].astype(str)
    pos=x[x['__live']>0].sort_values('__live',ascending=False)
    leaders=pos.head(max_leaders).copy()
    if len(leaders)<max_leaders:
        rest=x[~x.market.isin(leaders.market)].sort_values('__live',ascending=False).head(max_leaders-len(leaders))
        leaders=pd.concat([leaders,rest],ignore_index=True)

    patterns=p.copy() if p is not None else pd.DataFrame()
    if not patterns.empty:
        patterns=patterns.copy()
        patterns['source']=patterns['source'].astype(str)
        patterns['market']=patterns['market'].astype(str)
        patterns['observations']=pd.to_numeric(patterns.get('observations',0),errors='coerce').fillna(0)
        patterns=patterns[patterns.observations>=12]
    by_market={str(r.market):r for _,r in x.iterrows()}
    out=[]
    for _,src in leaders.iterrows():
        source=str(src.market)
        hist=patterns[patterns.source==source].copy() if not patterns.empty else pd.DataFrame()
        targets=[]
        if not hist.empty:
            hist=hist.sort_values(['pattern_score','bayes_rate','observations'],ascending=False).head(targets_per_leader)
            for _,h in hist.iterrows():
                target=str(h.market)
                tr=by_market.get(target)
                target_live=safe_float(tr.get('live_ret')) if tr is not None else None
                targets.append({
                    'market':target,
                    'pattern_rate':safe_float(h.get('bayes_rate',h.get('pattern_rate',.5))),
                    'obs':safe_int(h.get('observations',h.get('pattern_obs',0))),
                    'avg_next':safe_float(h.get('avg_next_ret',0)),
                    'current_ret':target_live,
                    'current_price':safe_float(tr.get('live_price')) if tr is not None and tr.get('live_price') is not None and not pd.isna(tr.get('live_price')) else None,
                })
        out.append({
            'source':source,
            'source_live':safe_float(src.get('__live')),
            'source_price':safe_float(src.get('live_price')) if src.get('live_price') is not None and not pd.isna(src.get('live_price')) else None,
            'targets':targets,
        })
    return out

def _leader_target_summary(flow_item):
    targets=flow_item.get('targets',[])
    if not targets:
        return '과거 A→B 데이터 부족'
    bits=[]
    for t in targets[:3]:
        now=t.get('current_ret')
        now_txt=f'현재 {now:+.1%}' if now is not None else '현재 시세 확인 필요'
        status=' · 이미 상승 중' if now is not None and now>0 else ''
        bits.append(f"{ticker(t['market'])} {t['pattern_rate']:.1%} · {t['obs']}회 · {now_txt}{status}")
    return ' / '.join(bits)


def render_daily_briefing(c, p, live_ok, live_df):
    """Daily briefing and Flow use the same live-leader list."""
    changes=daily_changes(c)
    st.markdown('<div class="section-title"><div><h2>📰 오늘의 시장 브리핑</h2><p>현재 가장 강하게 움직이는 선행 코인과 과거 다음날 흐름을 함께 읽습니다.</p></div><span class="section-badge">DAILY BRIEFING</span></div>', unsafe_allow_html=True)
    live=c.copy()
    live['live_ret_num']=live.apply(lambda r: live_change(r)[0], axis=1)
    leaders=_live_leader_flow(live_df,p,max_leaders=3,targets_per_leader=3)
    top_score=c.head(1).iloc[0] if not c.empty else None
    up=down=0
    if not changes.empty:
        valid=changes.dropna(subset=['prev_score'])
        up=int((valid.score_delta>0).sum()); down=int((valid.score_delta<0).sum())
    new_count=int(changes.is_new.sum()) if not changes.empty else 0
    cols=st.columns(4)
    with cols[0]: st.metric('현재 상승 코인', f'{int((live.live_ret_num>0).sum())}개')
    with cols[1]: st.metric('관심도 상승', f'{up}개', delta=f'{up-down:+d} 순증' if not changes.empty else None)
    with cols[2]: st.metric('새로 등장', f'{new_count}개' if not changes.empty else '-')
    with cols[3]: st.metric('현재 선행 1위', ticker(leaders[0].get('source')) if leaders else '-')
    if not leaders:
        st.info('현재 시세를 불러오면 오늘의 선행 → 후행 흐름이 여기에 표시됩니다.')
        return
    cards=[]
    for item in leaders:
        ret=item['source_live']; cls='red' if ret>0 else 'blue' if ret<0 else ''
        summary=_leader_target_summary(item)
        cards.append(f'<div class="brief-card"><div class="brief-coin">🔴 {ticker(item["source"])}</div><div class="brief-ret {cls}">{ret:+.1%}</div><div class="brief-sub">현재 선행 · 과거 다음날 관심</div><div class="leader-flow-mini">{summary}</div></div>')
    st.markdown('<div class="brief-grid">'+''.join(cards)+'</div>', unsafe_allow_html=True)
    st.caption('현재 선행은 Upbit 현재가 스냅샷, 다음 관심은 과거 선행 코인 발생 후 다음날의 A→B 관찰입니다.' if live_ok else '현재 시세 연결이 없어 저장된 등락을 표시 중입니다.')
    if not changes.empty:
        valid=changes[changes['prev_rank'].notna()].sort_values('rank_delta',ascending=False)
        if not valid.empty:
            r=valid.iloc[0]
            st.markdown(f'<div class="brief-callout">📌 <b>관심도 변화:</b> {ticker(r.get("market"))} · 관심도 {safe_float(r.get("score")):.1f} · 순위 {safe_int(r.get("prev_rank"))}위 → {safe_int(r.get("rank"))}위</div>', unsafe_allow_html=True)

def render_daily_pulse(c, live_ok):
    changes=daily_changes(c)
    st.markdown('<div class="section-title"><div><h2>⚡ 오늘 달라진 시장</h2><p>이전 분석일과 비교해 관심 점수와 현재 움직임이 달라진 코인을 보여줍니다.</p></div><span class="section-badge">DAILY PULSE</span></div>', unsafe_allow_html=True)
    if changes.empty:
        st.info('비교할 이전 분석 기록이 아직 없습니다. 오늘부터 기록을 쌓으면 다음 방문부터 변화가 표시됩니다.')
        return
    top=changes.dropna(subset=['prev_score']).head(5)
    for _,r in top.iterrows():
        delta=safe_float(r.get('score_delta')); live=safe_float(r.get('live_ret'))
        cls='red' if live>0 else 'blue' if live<0 else ''
        rank_text='새 등장' if pd.isna(r.get('prev_rank')) else f'{safe_int(r.get("prev_rank"))}위 → {safe_int(r.get("rank"))}위'
        st.markdown(f'<div class="rank-card"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><div><div class="coin-ticker">🔵 {ticker(r.get("market"))}</div><div class="coin-price">관심 점수 {safe_float(r.get("score")):.1f} · 전 분석일 {safe_float(r.get("prev_score")):.1f} · {rank_text}</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:900">{delta:+.1f}점</div><div class="change {cls}" style="font-size:15px">현재 {live:+.1%}</div></div></div></div>', unsafe_allow_html=True)

RUNTIME_DIR = APP_HOME / 'runtime'
LOCK = RUNTIME_DIR / '.first_analysis.lock'
LOG = RUNTIME_DIR / 'first_analysis.log'
STATUS = RUNTIME_DIR / 'first_analysis_status.txt'
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
REQ = ['candidates_final.csv', 'pair_patterns_final.csv', 'pattern_occurrences_final.csv', 'backtest_final.csv', 'last_run_final.json']
FLOW = OUT / 'leader_flows_final.csv'

st.set_page_config(page_title='Coin Pattern Beta', page_icon='◈', layout='wide', initial_sidebar_state='collapsed')

st.markdown(r'''
<style>
:root{--bg:#050d18;--panel:#091a2d;--panel2:#0d2138;--line:#173653;--text:#eef6ff;--muted:#8ea7c1;--blue:#269cff;--cyan:#23d7c8;--red:#ff4d5f;--green:#25d7a0;--down:#4da3ff;--gold:#f4bf55}
html,body,[class*="css"]{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}
.stApp{background:radial-gradient(circle at 90% -10%,rgba(34,125,255,.10),transparent 28%),linear-gradient(180deg,#040b15 0%,#061323 54%,#050d18 100%);color:var(--text)}
.block-container{max-width:1600px;padding:18px 20px 72px}
.main .block-container{width:100%}
[data-testid=stAppViewContainer]{background:transparent}
header[data-testid="stHeader"]{background:transparent}
[data-testid="stSidebar"]{background:#071323;border-right:1px solid #16314c}
[data-testid="stSidebar"] *{color:#dbeaff}
.hero{position:relative;overflow:hidden;padding:22px 28px;border:1px solid #173b5d;border-radius:24px;background:radial-gradient(circle at 88% 0%,rgba(39,145,255,.17),transparent 31%),linear-gradient(145deg,#0a1c31,#071323 76%);box-shadow:0 18px 55px rgba(0,0,0,.22);margin-bottom:18px}
.hero:after{content:'';position:absolute;right:-110px;top:-130px;width:330px;height:330px;border-radius:50%;border:1px solid rgba(70,170,255,.13);box-shadow:0 0 0 38px rgba(70,170,255,.035),0 0 0 76px rgba(70,170,255,.018)}
.brand{display:flex;align-items:center;gap:13px;position:relative;z-index:1}
.brandmark{width:48px;height:48px;border-radius:15px;display:grid;place-items:center;background:linear-gradient(145deg,#258dff,#6547ff);font-size:25px;box-shadow:0 9px 26px rgba(39,137,255,.22)}
.hero h1{font-size:30px;letter-spacing:-.7px;margin:0;color:#fff}.hero .tag{font-size:14px;color:#7fc6ff;margin-top:2px}
.hero-sub{margin:19px 0 0;color:#d9e9fa;font-size:15px}.pills{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}
.pill{padding:7px 11px;border-radius:999px;border:1px solid #21425f;background:#0b1e33;font-size:12px;color:#b9cde1}
.pill.red{background:rgba(255,72,88,.08);border-color:rgba(255,72,88,.30);color:#ff9da6}.pill.blue{background:rgba(53,167,255,.08);border-color:rgba(53,167,255,.30);color:#82c9ff}.pill.green{background:rgba(37,215,160,.08);border-color:rgba(37,215,160,.25);color:#7cf0c8}
.section-title{display:flex;align-items:end;justify-content:space-between;gap:16px;margin:25px 0 10px}.section-title h2{margin:0;font-size:24px;letter-spacing:-.45px}.section-title p{margin:5px 0 0;color:var(--muted);font-size:13px}.section-badge{padding:5px 9px;border:1px solid #214668;border-radius:999px;color:#8bc9ff;font-size:11px;white-space:nowrap}
.metricbar{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0 20px}.metric{background:linear-gradient(180deg,#0b1e33,#081627);border:1px solid #183754;border-radius:16px;padding:14px}.metric .k{font-size:11px;color:#8ea9c4}.metric .v{font-size:22px;font-weight:800;margin-top:5px}.metric .s{font-size:10px;color:#6f8aa7;margin-top:3px}
.brief-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:10px 0}.brief-card{background:linear-gradient(180deg,#0a1b2e,#071424);border:1px solid #173652;border-radius:17px;padding:16px}.brief-coin{font-size:19px;font-weight:900}.brief-ret{font-size:24px;font-weight:900;margin-top:6px}.brief-ret.red{color:#ff5360}.brief-ret.blue{color:#4da3ff}.brief-sub{font-size:11px;color:#8fa8c5;margin-top:6px}.leader-flow-mini{font-size:10px;line-height:1.55;color:#b9d3e9;margin-top:9px;padding-top:8px;border-top:1px solid #16334d}.flow-live-ret{font-size:16px;font-weight:900;color:#ff5360;margin-top:4px}.target em.up{color:#ff5360}.target em.down{color:#4da3ff}.target em.flat{color:#a7b7c8}.target-empty{min-width:260px}.brief-callout{padding:13px 15px;border-radius:14px;background:rgba(53,167,255,.06);border:1px solid rgba(53,167,255,.20);color:#cfe8ff;margin:10px 0}
.rank-card{position:relative;background:linear-gradient(110deg,#091a2d,#081626 58%,#0a1a2e);border:1px solid #173652;border-radius:18px;padding:17px;margin:9px 0;box-shadow:0 8px 24px rgba(0,0,0,.13)}
.rank-card.hot{border-color:#c83d4b;box-shadow:0 0 0 1px rgba(255,67,80,.08),0 12px 30px rgba(255,67,80,.06)}
.rank{position:absolute;left:12px;top:14px;width:32px;height:32px;border-radius:50%;display:grid;place-items:center;background:#183552;color:#dcecff;font-weight:800;font-size:13px}.rank.hot{background:linear-gradient(145deg,#eeb43e,#9b661d);color:white}
.coin-main{padding-left:44px;display:grid;grid-template-columns:1fr .72fr 1.18fr;gap:14px;align-items:center}.coin-name{display:flex;align-items:center;gap:10px}.coin-icon{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:#081a2d;border:1px solid #3267a0;font-weight:800;color:#fff;overflow:hidden}.coin-logo{width:100%;height:100%;object-fit:contain;display:block}.coin-logo-fallback{width:100%;height:100%;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle at 35% 30%,#2b8cff,#17346d);font-weight:900;color:#fff}.coin-ticker{font-size:20px;font-weight:800}.coin-full{font-size:11px;color:#839bb6;margin-top:2px}.coin-price{font-size:11px;color:#94abc2;margin-top:6px}.change{font-size:22px;font-weight:900;color:#8ea9c4}.change.red{color:#ff5360}.change.blue{color:#4da3ff}
.leader-label{display:inline-block;padding:5px 8px;border-radius:999px;background:rgba(37,215,160,.10);border:1px solid rgba(37,215,160,.22);color:#72e9c3;font-size:11px;margin-bottom:5px}.leader-label.red{background:rgba(255,72,88,.10);border-color:rgba(255,72,88,.27);color:#ff9aa2}
.flow-box{display:grid;grid-template-columns:1fr 28px 1fr;gap:6px;align-items:center;background:rgba(4,14,26,.48);border:1px solid #15334e;border-radius:13px;padding:10px}.flow-title{font-size:10px;color:#7590ad;margin-bottom:5px}.flow-coin{font-size:14px;font-weight:800}.flow-rate{font-size:11px;color:#8ea9c4;margin-top:2px}.arrow{font-size:21px;text-align:center;color:#3aa9ff}.card-footer{display:flex;flex-wrap:wrap;gap:14px;align-items:center;border-top:1px solid #153149;margin-top:13px;padding-top:10px;color:#9bb1c8;font-size:11px}.score{margin-left:auto;color:#b8d9ff;font-weight:800}.score strong{font-size:16px;color:#fff}
.flow-panel{background:linear-gradient(180deg,#08192b,#061323);border:1px solid #173652;border-radius:20px;padding:17px}.flow-main-panel{padding:14px}.flow-leader-card{background:linear-gradient(145deg,#0a1e34,#071525 72%);border:1px solid #1a3b5a;border-radius:19px;padding:16px;margin:10px 0}.flow-leader-card.primary{border-color:#ff4654;box-shadow:0 0 0 1px rgba(255,70,84,.08),0 14px 34px rgba(255,70,84,.07)}.flow-source-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.flow-source-meta{display:flex;align-items:center;gap:10px}.flow-rank{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:#193b5c;color:#dcecff;font-size:11px;font-weight:900}.primary .flow-rank{background:linear-gradient(145deg,#f0bb48,#9b681e)}.flow-source-name{font-size:20px;font-weight:900}.flow-source-sub{font-size:10px;color:#7895b2;margin-top:3px}.flow-badge{padding:6px 9px;border-radius:999px;background:rgba(255,72,88,.10);border:1px solid rgba(255,72,88,.28);color:#ff9da6;font-size:11px;font-weight:800}.flow-source-main{display:grid;grid-template-columns:170px 1fr;gap:18px;align-items:center;margin:12px 0 9px}.flow-live-big{font-size:27px;font-weight:950;color:#ff5360}.flow-source-price{font-size:10px;color:#7895b2;margin-top:2px}.spark-wrap{height:48px;position:relative}.mini-spark{width:100%;height:38px;display:block}.spark-wrap span{position:absolute;right:2px;bottom:-1px;color:#607b96;font-size:9px}.flow-branch-title{padding:9px 11px;border-top:1px solid #163650;border-bottom:1px solid #163650;color:#cde3f6;font-size:12px;font-weight:800}.flow-branch-title span{float:right;color:#6f8ba6;font-size:9px;font-weight:600}.flow-targets{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:9px}.flow-target-branch{display:grid;grid-template-columns:14px 22px 1fr;gap:5px;align-items:center;padding:9px 10px;background:#091b2f;border:1px solid #1b3d5c;border-radius:12px}.branch-arrow{color:#4aaeff;font-size:16px}.target-rank{width:21px;height:21px;border-radius:50%;background:#122d49;display:grid;place-items:center;color:#8fb2cf;font-size:9px}.target-info b{font-size:13px}.target-info span{display:block;color:#7894ae;font-size:9px;margin-top:2px;white-space:nowrap}.flow-target-branch em{grid-column:3;font-style:normal;font-size:10px;color:#ff6a75;margin-top:3px}.flow-target-branch em.down{color:#4da3ff}.flow-target-branch em.flat{color:#9aafc2}.empty-target{grid-template-columns:1fr}.v59-flow-row{position:relative;display:grid;grid-template-columns:minmax(390px,.92fr) 72px minmax(520px,1.45fr);gap:12px;align-items:stretch;margin:16px 0}.v59-connector-col{position:relative;display:flex;align-items:center;justify-content:center;min-width:0;overflow:hidden;z-index:2}.v59-straight-arrow{font-size:34px;line-height:1;font-weight:900;text-shadow:0 0 10px currentColor;display:block}.v59-flow-source{position:relative;border:1px solid #234c72;border-radius:22px;padding:20px;background:linear-gradient(145deg,#0a2037,#071526 76%);box-shadow:0 12px 30px rgba(0,0,0,.16);transition:border-color .2s,box-shadow .2s}.v59-flow-source.hot{border-color:#ff4654;box-shadow:0 0 0 1px rgba(255,70,84,.10),0 14px 36px rgba(255,70,84,.09),inset 0 0 28px rgba(255,70,84,.035)}
/* v61 visual polish: each live leader gets its own subtle neon border, matching the flow color. */
.v59-flow-row:nth-child(1) .v59-flow-source{border-color:#ff4654;box-shadow:0 0 0 1px rgba(255,70,84,.10),0 14px 36px rgba(255,70,84,.09),inset 0 0 30px rgba(255,70,84,.035)}
.v59-flow-row:nth-child(2) .v59-flow-source{border-color:#18d9cf;box-shadow:0 0 0 1px rgba(24,217,207,.08),0 14px 32px rgba(24,217,207,.055),inset 0 0 28px rgba(24,217,207,.025)}
.v59-flow-row:nth-child(3) .v59-flow-source{border-color:#9b5cff;box-shadow:0 0 0 1px rgba(155,92,255,.08),0 14px 32px rgba(155,92,255,.055),inset 0 0 28px rgba(155,92,255,.025)}
.v59-flow-row:nth-child(4) .v59-flow-source{border-color:#ff9f43;box-shadow:0 0 0 1px rgba(255,159,67,.08),0 14px 32px rgba(255,159,67,.05),inset 0 0 28px rgba(255,159,67,.022)}
.v59-flow-row:nth-child(5) .v59-flow-source{border-color:#ff4fb3;box-shadow:0 0 0 1px rgba(255,79,179,.08),0 14px 32px rgba(255,79,179,.05),inset 0 0 28px rgba(255,79,179,.022)}.v59-flow-source .flow-source-head{margin-bottom:8px}.v59-source-logo{width:48px;height:48px;border-radius:50%;overflow:hidden;background:#071729;border:1px solid #2b5b84;display:grid;place-items:center}.v59-source-logo .coin-logo{width:46px;height:46px}.v59-flow-source .flow-source-name{font-size:23px}.v59-flow-source .flow-live-big{font-size:30px}.v59-flow-targets-panel{position:relative;border:1px solid #1c4163;border-radius:22px;padding:16px 16px 16px 28px;background:linear-gradient(145deg,#08192d,#061323 78%)}.v59-flow-targets-panel:before{display:none}.v59-connectors path{vector-effect:non-scaling-stroke}.v59-flow-source,.v59-flow-targets-panel{position:relative;z-index:3}.v59-flow-targets-panel{z-index:3}.v59-connector{fill:none;stroke-width:3.5;stroke-linecap:round;filter:drop-shadow(0 0 5px rgba(70,170,255,.28))}.v59-connector.hot{stroke:#ff3f87}.v59-connector.blue{stroke:#39a9ff}.v59-connector.purple{stroke:#9b5cff}.v59-target-card{display:grid;grid-template-columns:28px 34px 1fr auto 105px;gap:10px;align-items:center;padding:13px 14px;margin:7px 0;border:1px solid #24527a;border-radius:14px;background:linear-gradient(100deg,#0a2037,#08192b)}.v59-target-card:first-child{border-color:#7449c9;box-shadow:inset 0 0 18px rgba(116,73,201,.045)}
.v59-flow-row:nth-child(1) .v59-target-card{border-color:rgba(255,72,135,.38)}
.v59-flow-row:nth-child(2) .v59-target-card{border-color:rgba(53,199,255,.34)}
.v59-flow-row:nth-child(3) .v59-target-card{border-color:rgba(155,92,255,.34)}
.v59-flow-row:nth-child(4) .v59-target-card{border-color:rgba(255,159,67,.34)}
.v59-flow-row:nth-child(5) .v59-target-card{border-color:rgba(255,79,179,.34)}.v59-target-logo{width:30px;height:30px;border-radius:50%;overflow:hidden;border:1px solid #2b5b84;background:#071729;display:grid;place-items:center}.v59-target-logo .coin-logo{width:28px;height:28px}.v59-target-rank{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;background:#183654;color:#dbeaff;font-size:11px;font-weight:900}.v59-target-main b{font-size:15px}.v59-target-main small{display:block;color:#7d99b4;font-size:9px;margin-top:1px}.v59-target-main span{display:block;color:#8ca7c0;font-size:10px;margin-top:2px}.v59-target-why{display:block;color:#6f8da8;font-size:9px;line-height:1.35;margin-top:4px}.flow-change-banner{margin:10px 0;padding:10px 13px;border:1px solid rgba(39,215,160,.32);border-radius:12px;background:rgba(39,215,160,.06);color:#b9eedd;font-size:11px}.v59-target-now{font-size:12px;font-weight:900;color:#ff5360;white-space:nowrap}.v59-target-now.down{color:#4da3ff}.v59-target-spark{height:30px}.v59-target-spark .mini-spark{height:30px}.v59-home-lower{display:grid;grid-template-columns:1.05fr 1.25fr 1.05fr;gap:14px;margin-top:16px}.v59-lower-card{background:linear-gradient(180deg,#091c31,#071525);border:1px solid #183a5a;border-radius:18px;padding:15px;min-height:190px}.v59-lower-head{display:flex;justify-content:space-between;align-items:center;gap:8px;border-bottom:1px solid #173651;padding-bottom:10px;margin-bottom:10px}.v59-lower-head h3{margin:0;font-size:17px}.v59-lower-head span{font-size:10px;color:#6f8ca7}.v59-brief-stat{display:flex;justify-content:space-between;align-items:center;padding:9px 10px;border-radius:12px;background:#0a2138;margin:6px 0}.v59-brief-stat b{font-size:13px}.v59-brief-stat strong{font-size:17px}.v59-mini-row{display:grid;grid-template-columns:22px 1fr auto;gap:8px;padding:8px 4px;border-bottom:1px solid #112d46;align-items:center}.v59-mini-row:last-child{border-bottom:0}.v59-mini-logo{width:20px;height:20px;border-radius:50%;overflow:hidden;display:grid;place-items:center;background:#081a2d;border:1px solid #22496b}.v59-mini-logo .coin-logo{width:18px;height:18px}.v59-mini-row b{font-size:12px}.v59-mini-row span{font-size:11px;color:#8fa9c2}.v59-mini-row .up{color:#25d7a0;font-weight:900}.v59-mini-row .down{color:#4da3ff;font-weight:900}.v59-empty{color:#7895b2;font-size:11px;line-height:1.5;padding:14px 2px}.v59-flow-note{margin-top:10px;color:#7895b2;font-size:10px}.v59-home-flow .section-title{margin-top:0}.v59-home-flow .section-title h2{font-size:28px}.v59-home-flow .section-title p{font-size:13px}.flow-row{display:grid;grid-template-columns:175px 26px 1fr;gap:10px;align-items:center;margin:10px 0}.flow-source{border:1px solid #294c70;border-radius:13px;padding:11px;background:#0a1d32}.flow-source.hot{border-color:#ff4654;box-shadow:0 0 20px rgba(255,70,84,.09)}.flow-source .big{font-size:17px;font-weight:900}.target-list{display:flex;flex-wrap:wrap;gap:7px}.target{padding:9px 11px;border:1px solid #203f5d;background:#0a1c30;border-radius:12px;min-width:135px}.target b{font-size:13px}.target span{display:block;color:#86a1bb;font-size:10px;margin-top:2px}.target em{color:#5eb8ff;font-style:normal;font-size:11px}.arrow2{color:#3faeff;font-size:22px;text-align:center}
.table-wrap{background:#08182a;border:1px solid #173652;border-radius:18px;padding:7px;overflow:hidden}.detail{background:linear-gradient(145deg,#0b2036,#071526);border:1px solid #193b5c;border-radius:20px;padding:20px}.detail h3{margin:0}.detail .bigscore{font-size:39px;font-weight:900;color:#68c6ff}.evidence{display:grid;grid-template-columns:1fr 1fr;gap:10px}.evidence .ev{border:1px solid #193b5c;border-radius:14px;background:#091a2d;padding:13px}.ev .v{font-size:20px;font-weight:800;margin-top:4px}.ev .k{font-size:11px;color:#7f9ab5}
.pro-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}.pro-card{background:linear-gradient(180deg,#0b2036,#071526);border:1px solid #193b5c;border-radius:16px;padding:14px}.pro-card .k{font-size:10px;color:#7f9ab5}.pro-card .v{font-size:20px;font-weight:850;margin-top:4px;color:#f3f8ff}.pro-card .s{font-size:10px;color:#6f8aa7;margin-top:3px}
.pro-hero{background:radial-gradient(circle at 85% 10%,rgba(99,82,255,.16),transparent 30%),linear-gradient(135deg,#101d3a,#091628 72%);border:1px solid #31558a;border-radius:22px;padding:22px;box-shadow:0 16px 42px rgba(0,0,0,.18)}.pro-hero h2{margin:0;font-size:27px}.pro-hero p{color:#9bb1c8;margin:7px 0 0}.pro-price{font-size:32px;font-weight:900;margin-top:11px}.pro-price small{font-size:12px;color:#7f9ab5;font-weight:600}.pro-features{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}.pro-feature{background:#091a2d;border:1px solid #203f62;border-radius:14px;padding:13px}.pro-feature b{display:block;font-size:13px}.pro-feature span{display:block;color:#7f9ab5;font-size:10px;margin-top:4px}
.signal-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.signal-box{background:#091a2d;border:1px solid #193b5c;border-radius:16px;padding:15px}.signal-box h4{margin:0 0 9px;font-size:13px}.barrow{display:grid;grid-template-columns:96px 1fr 48px;gap:8px;align-items:center;margin:8px 0;font-size:10px;color:#9bb1c8}.barbg{height:7px;background:#142b43;border-radius:99px;overflow:hidden}.barfill{height:100%;border-radius:99px;background:linear-gradient(90deg,#287fff,#35d4ff)}
.pro-lock{margin-top:12px;padding:17px;border:1px dashed #3a6389;border-radius:16px;background:linear-gradient(135deg,rgba(36,116,255,.08),rgba(20,215,200,.04));text-align:center}.pro-lock b{font-size:16px}.pro-lock p{color:#8fa8c5;font-size:11px;margin:6px 0 12px}
.recent-row{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid #17324d;color:#cfe0ef;font-size:11px}.recent-row:last-child{border-bottom:0}.pattern-history-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px}.pattern-history-card{background:linear-gradient(145deg,#0a1d32,#071525 78%);border:1px solid #193b5a;border-radius:15px;padding:12px 13px}.ph-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px}.ph-date{font-size:11px;color:#8fa9c2}.ph-result{font-size:10px;font-weight:900;padding:4px 8px;border-radius:999px;background:#0c243a;border:1px solid #244968}.ph-result.up{color:#ff8f9a;border-color:rgba(255,77,95,.32);background:rgba(255,77,95,.08)}.ph-result.down{color:#76b8ff;border-color:rgba(77,163,255,.30);background:rgba(77,163,255,.08)}.ph-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.ph-grid>div{padding:10px 9px;border-radius:10px;background:#091a2d;border:1px solid #153650}.ph-grid span{display:block;font-size:9px;color:#718da8}.ph-grid b{display:block;margin-top:4px;font-size:13px;color:#dbeaff}.ph-grid b.rise,.ph-grid b.up{color:#ff7885}.ph-grid b.down{color:#69aaff}.ph-flow{display:flex;align-items:center;gap:8px;margin:2px 0 9px}.ph-flow .ph-coin{flex:1;padding:9px 10px;border-radius:10px;background:#091a2d;border:1px solid #153650}.ph-flow .ph-coin span{display:block;font-size:9px;color:#718da8}.ph-flow .ph-coin b{display:block;margin-top:3px;font-size:14px;color:#ff7885}.ph-flow .ph-arrow{color:#6f9fbe;font-size:18px;font-weight:800}.ph-flow .ph-target b{color:#dbeaff}.ph-flow .ph-target b.up{color:#ff7885}.ph-flow .ph-target b.down{color:#69aaff}

.empty{padding:28px;border:1px dashed #315270;border-radius:18px;background:#091a2d;text-align:center;color:#9db2c8}.status{padding:15px;border-radius:16px;background:#0a1e33;border:1px solid #214b72;color:#dcecff}.status b{font-size:14px}.log{background:#050b13;border:1px solid #1b3045;border-radius:13px;padding:11px;font-family:Consolas,monospace;font-size:10px;color:#9ec3e5;white-space:pre-wrap;max-height:240px;overflow:auto}
div[data-testid="stTabs"]{margin-top:4px}
div[data-testid="stTabs"] [data-baseweb="tab-list"]{gap:4px;border-bottom:1px solid #173652;overflow-x:auto;scrollbar-width:none}
div[data-testid="stTabs"] [data-baseweb="tab-list"]::-webkit-scrollbar{display:none}
div[data-testid="stTabs"] [data-baseweb="tab"]{color:#829ab3;padding:9px 13px;white-space:nowrap;font-size:13px}
div[data-testid="stTabs"] [aria-selected="true"]{color:#fff}
.stButton>button{border-radius:11px;border:1px solid #245078;background:#0c2843;color:#e9f5ff;font-weight:700;min-height:40px}.stButton>button:hover{border-color:#4aabff;color:white}
.stDownloadButton>button{border-radius:11px}.stDataFrame{border:1px solid #173858;border-radius:14px;overflow:hidden}
@media(min-width:1400px){
  .v59-home-flow .section-title h2{font-size:32px}
  .v59-home-flow .section-title p{font-size:14px}
  .v59-flow-source{min-height:205px}
  .v59-flow-targets-panel{min-height:205px}
  .v59-target-card{min-height:68px}
  .v59-source-logo{width:54px;height:54px}.v59-source-logo .coin-logo{width:52px;height:52px}
}
@media(max-width:900px){
  .block-container{max-width:none;padding:14px 12px 70px}
  .hero{padding:18px 17px;border-radius:19px;margin-bottom:13px}
  .hero h1{font-size:25px}.hero .tag{font-size:12px}.hero-sub{font-size:13px;margin-top:14px}
  .brandmark{width:43px;height:43px;border-radius:13px;font-size:22px}
  .pills{gap:5px;margin-top:11px}.pill{font-size:10px;padding:5px 8px}
  .section-title{margin:20px 0 8px;align-items:flex-start}.section-title h2{font-size:20px}.section-title p{font-size:11px;line-height:1.45}
  .section-badge{font-size:9px;padding:4px 7px}
  .metricbar{grid-template-columns:1fr 1fr;gap:7px}.metric{padding:11px}.metric .v{font-size:18px}
  .brief-grid,.pro-features,.pro-grid{grid-template-columns:1fr}
  .rank-card{padding:14px 12px;border-radius:16px;margin:7px 0}
  .rank{left:9px;top:10px;width:28px;height:28px;font-size:11px}
  .coin-main{padding-left:38px;grid-template-columns:1fr;gap:10px}
  .coin-icon{width:39px;height:39px}.coin-ticker{font-size:17px}.coin-full{font-size:10px}
  .change{font-size:20px}.coin-price{font-size:10px}
  .v59-flow-row{grid-template-columns:1fr;gap:8px;margin:12px 0}.v59-flow-source{padding:15px 15px 14px;border-radius:18px;min-height:0}.v59-connector-col{height:28px;min-height:28px;display:flex;align-items:center;justify-content:center}.v59-connector-col:after{display:none}.v59-straight-arrow{font-size:28px}.v59-flow-targets-panel{padding:12px;border-radius:18px;min-height:0}.v59-target-card{grid-template-columns:24px 30px 1fr auto;gap:7px;padding:10px;border-radius:12px}.v59-target-spark{display:none}.v59-home-lower{grid-template-columns:1fr}.v59-home-flow .section-title h2{font-size:22px}.v59-home-flow .section-title p{font-size:11px}.v59-home-flow .section-badge{display:none}  .flow-box{grid-template-columns:1fr 24px 1fr;padding:9px}.flow-coin{font-size:12px}.flow-rate{font-size:9px}.arrow{font-size:18px}
  .card-footer{gap:8px;font-size:9px;margin-top:10px;padding-top:9px}.score{margin-left:0;width:100%}
  .flow-panel{padding:10px;border-radius:16px}.flow-leader-card{padding:13px;border-radius:16px}.flow-source-name{font-size:18px}.flow-source-main{grid-template-columns:125px 1fr;gap:10px}.flow-live-big{font-size:23px}.flow-targets{grid-template-columns:1fr}.flow-target-branch{grid-template-columns:14px 22px 1fr}.flow-target-branch em{grid-column:3}.flow-branch-title{font-size:11px}.flow-row{grid-template-columns:1fr;gap:7px;margin:8px 0}.arrow2{display:none}.target-list{display:grid;grid-template-columns:1fr 1fr}.target{min-width:0;padding:8px}
  .detail{padding:15px;border-radius:17px}.detail .bigscore{font-size:32px}.evidence,.signal-grid,.pattern-history-grid{grid-template-columns:1fr}.ph-grid{grid-template-columns:1fr 1fr}
  .pro-hero{padding:17px;border-radius:18px}.pro-price{font-size:28px}
  div[data-testid="stTabs"] [data-baseweb="tab"]{padding:8px 10px;font-size:11px}
  div[data-testid="stTabs"] [data-baseweb="tab-list"]{margin-left:-2px;margin-right:-2px}
  [data-testid="stSidebar"]{display:none}
  .stSelectbox label,.stTextInput label{font-size:11px}
}
@media(min-width:901px){
  [data-testid="stSidebar"]{display:none}
}


/* v85+: analysis history premium UI. Hide raw JSON/DataFrame from the user-facing record page. */
.analysis-record-hero{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:22px 0 14px;padding:22px 24px;border:1px solid #1d4668;border-radius:20px;background:radial-gradient(circle at 88% 10%,rgba(47,154,255,.14),transparent 34%),linear-gradient(145deg,#0b2239,#071525 78%);box-shadow:0 14px 36px rgba(0,0,0,.16)}
.analysis-record-hero h2{margin:4px 0 6px;font-size:24px;color:#f4f9ff;letter-spacing:-.4px}.analysis-record-hero p{margin:0;color:#8ea8be;font-size:12px}.ar-kicker{font-size:9px;letter-spacing:.14em;color:#72caff;font-weight:900}.ar-status{padding:7px 11px;border-radius:999px;border:1px solid #214a68;background:#0b2339;color:#8fb1ca;font-size:10px;font-weight:900;white-space:nowrap}.ar-status.green{color:#7be7bf;border-color:rgba(39,215,160,.28);background:rgba(39,215,160,.07)}.ar-status.gold{color:#ffd27f;border-color:rgba(255,183,77,.28);background:rgba(255,183,77,.07)}.ar-status.red{color:#ff9aa3;border-color:rgba(255,77,95,.28);background:rgba(255,77,95,.07)}
.analysis-summary-grid{display:grid;grid-template-columns:1.6fr repeat(3,1fr);gap:10px;margin-bottom:24px}.analysis-summary-card{min-width:0;padding:15px 16px;border:1px solid #193b5a;border-radius:16px;background:linear-gradient(145deg,#0a1d32,#071525 80%)}.analysis-summary-card.wide{border-color:#214d70}.ar-label{font-size:9px;color:#718da8;font-weight:800;letter-spacing:.03em}.ar-value{margin-top:5px;color:#eef7ff;font-size:23px;font-weight:900;letter-spacing:-.3px}.ar-value span{margin-left:3px;color:#7d9ab4;font-size:11px;font-weight:700}.ar-date{font-size:18px}.ar-sub{margin-top:5px;color:#718da8;font-size:9px;line-height:1.45}
.analysis-block-head{display:flex;align-items:flex-end;justify-content:space-between;gap:15px;margin:20px 0 10px}.analysis-block-head h3{margin:0;color:#eaf4ff;font-size:19px}.analysis-block-head p{margin:4px 0 0;color:#7895b2;font-size:10px}.analysis-block-head>span{padding:5px 9px;border-radius:999px;border:1px solid #244a68;background:#0a2036;color:#8db1cc;font-size:9px;white-space:nowrap}.analysis-backtest-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:9px}.analysis-result-card{padding:14px;border:1px solid #193b5a;border-radius:15px;background:#091a2d}.analysis-result-card .ar-result{margin-top:5px;color:#f0f7ff;font-size:22px;font-weight:900}.analysis-result-card.up .ar-result{color:#ff7b87}.analysis-result-card.down .ar-result{color:#69aaff}.analysis-note{margin-top:9px;color:#6f8ca7;font-size:9px;line-height:1.5;padding:8px 10px;border-left:2px solid #245678;background:rgba(8,24,42,.55);border-radius:0 8px 8px 0}.analysis-diagnostic{margin-top:22px;padding:17px;border:1px solid #183b58;border-radius:17px;background:linear-gradient(145deg,rgba(9,28,47,.96),rgba(7,20,35,.96))}.diag-badge{color:#819ab2!important;background:#0a2034!important}.diag-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}.diag-grid>div{padding:10px;border:1px solid #153650;border-radius:11px;background:#091a2d}.diag-grid span{display:block;color:#6f8ca7;font-size:9px}.diag-grid b{display:block;margin-top:4px;color:#dcecff;font-size:16px}.diag-grid b.down{color:#69aaff}.analysis-footer-card{display:flex;align-items:center;gap:12px;margin:18px 0 8px;padding:13px 15px;border:1px solid #173753;border-radius:14px;background:rgba(8,24,42,.65)}.af-icon{width:27px;height:27px;border-radius:50%;display:grid;place-items:center;background:rgba(39,215,160,.10);border:1px solid rgba(39,215,160,.25);color:#64dfb0;font-weight:900}.analysis-footer-card b{color:#cfe4f6;font-size:11px}.analysis-footer-card p{margin:3px 0 0;color:#6f8ca7;font-size:9px;line-height:1.45}
@media(max-width:900px){.analysis-summary-grid{grid-template-columns:1fr 1fr}.analysis-summary-card.wide{grid-column:span 2}.analysis-backtest-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:700px){.analysis-record-hero{align-items:flex-start;flex-direction:column;padding:17px 16px}.analysis-record-hero h2{font-size:19px}.analysis-record-hero p{font-size:10px}.analysis-summary-grid{grid-template-columns:1fr 1fr;gap:8px}.analysis-summary-card{padding:12px}.analysis-summary-card.wide{grid-column:span 2}.ar-value{font-size:20px}.ar-date{font-size:15px}.analysis-block-head{align-items:flex-start;flex-direction:column;gap:7px}.analysis-backtest-grid{grid-template-columns:1fr 1fr}.analysis-result-card .ar-result{font-size:20px}.diag-grid{grid-template-columns:1fr 1fr}.analysis-footer-card{align-items:flex-start}}

/* v72: explanatory meta layer. Informational only; never changes score. */
.meta-diffusion-box{margin:9px 0 10px;padding:12px 13px;border:1px solid rgba(113,201,255,.22);border-radius:14px;background:linear-gradient(135deg,rgba(20,69,104,.18),rgba(8,25,42,.72))}
.meta-diffusion-kicker{font-size:10px;color:#83d7ff;font-weight:900;letter-spacing:.01em}.meta-diffusion-main{margin-top:5px;color:#d9edff;font-size:12px;line-height:1.45}.meta-diffusion-main b{color:#fff}.meta-diffusion-rate{margin-top:3px;color:#6f8da8;font-size:9px}
@media(max-width:700px){.meta-diffusion-box{padding:10px 11px}.meta-diffusion-main{font-size:11px}}

/* v70: TOP 5 public, full TOP 30 for free members. */
.top30-more-gate{margin:22px 0 12px;padding:24px 26px;border:1px solid rgba(74,180,255,.28);border-radius:20px;background:linear-gradient(135deg,rgba(16,42,67,.92),rgba(8,22,38,.96));box-shadow:0 10px 34px rgba(0,0,0,.18)}
.top30-more-kicker{display:inline-block;font-size:11px;letter-spacing:.12em;color:#7ed8ff;font-weight:800;margin-bottom:8px}
.top30-more-gate h3{margin:0 0 7px;color:#fff;font-size:20px}
.top30-more-gate p{margin:0;color:#9eb4c8;line-height:1.55;font-size:13px}

/* v68: TOP 30 app-style cards instead of the default Streamlit dataframe. */
.detail-usage{margin:8px 0 16px;padding:14px 16px 12px;border:1px solid #214967;border-radius:18px;background:linear-gradient(135deg,rgba(10,31,51,.98),rgba(7,20,35,.96));box-shadow:0 10px 28px rgba(0,0,0,.12)}
.detail-usage.pro{border-color:rgba(118,117,255,.42);background:radial-gradient(circle at 92% 0%,rgba(111,95,255,.14),transparent 32%),linear-gradient(135deg,#101d38,#081526)}
.du-top{display:flex;align-items:center;justify-content:space-between;gap:12px}.du-kicker{display:block;color:#72caff;font-size:9px;font-weight:900;letter-spacing:.13em}.du-title{display:block;margin-top:3px;color:#f2f8ff;font-size:14px;font-weight:800}.du-title b{font-size:20px;color:#8fd6ff}.du-title i{font-style:normal;color:#7895b0;font-size:12px;margin-left:2px}.du-remain{padding:5px 9px;border-radius:999px;border:1px solid #244c6d;background:#0a2137;color:#a9c9e4;font-size:10px;font-weight:800;white-space:nowrap}.detail-usage.full{border-color:rgba(255,183,77,.38);background:linear-gradient(135deg,rgba(47,31,15,.92),rgba(12,22,32,.96))}.detail-usage.full .du-remain{color:#ffd28a;border-color:rgba(255,183,77,.34);background:rgba(255,183,77,.08)}.du-track{height:7px;margin-top:11px;background:#132d46;border-radius:99px;overflow:hidden;border:1px solid #193a57}.du-fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#2b8dff,#48d9ff);box-shadow:0 0 14px rgba(57,177,255,.25)}.detail-usage.full .du-fill{background:linear-gradient(90deg,#ff9f43,#ffd166);box-shadow:0 0 14px rgba(255,178,70,.22)}.du-bottom{display:flex;justify-content:space-between;gap:12px;margin-top:7px;color:#6f8ca7;font-size:9px;line-height:1.4}.du-bottom b{color:#8fb1ca;font-weight:800;white-space:nowrap}.du-pro-badge{padding:5px 9px;border-radius:999px;border:1px solid rgba(126,126,255,.34);background:rgba(104,89,255,.10);color:#bfc0ff;font-size:10px;font-weight:900;white-space:nowrap}.detail-usage.pro .du-sub{margin-top:7px;color:#829bb5;font-size:10px}
.member-gate{display:flex;align-items:center;gap:16px;margin:14px 0 18px;padding:20px 22px;border:1px solid #214967;border-radius:20px;background:linear-gradient(135deg,rgba(9,29,49,.98),rgba(7,20,35,.96));box-shadow:0 14px 40px rgba(0,0,0,.16)}
.member-gate-icon{width:52px;height:52px;flex:0 0 52px;border-radius:16px;display:grid;place-items:center;background:rgba(83,193,255,.10);border:1px solid #286083;font-size:24px}
.member-gate-copy{min-width:0}.member-gate-kicker{font-size:9px;font-weight:900;letter-spacing:.12em;color:#70cbff}.member-gate h3{margin:4px 0 5px;color:#f3f8ff;font-size:18px}.member-gate p{margin:0;color:#8da8bf;font-size:11px;line-height:1.6}
@media(max-width:700px){.member-gate{align-items:flex-start;padding:16px;gap:12px}.member-gate-icon{width:44px;height:44px;flex-basis:44px;font-size:20px}.member-gate h3{font-size:15px}.member-gate p{font-size:10px}.detail-usage{padding:13px 13px 11px;border-radius:16px}.du-top{align-items:flex-start}.du-remain{font-size:9px;padding:4px 7px}.du-bottom{font-size:8px}.du-bottom span{max-width:72%}.du-title{font-size:13px}}
.top30-legend{display:flex;flex-wrap:wrap;gap:7px 18px;margin:4px 0 13px;padding:10px 13px;border:1px solid #163653;border-radius:13px;background:rgba(8,24,42,.72);color:#7895b2;font-size:10px;line-height:1.5}
.top30-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px}
.top30-card{position:relative;min-width:0;background:linear-gradient(145deg,#0a1d32,#071525 78%);border:1px solid #193b5a;border-radius:18px;padding:14px 14px 11px 50px;box-shadow:0 9px 26px rgba(0,0,0,.12);transition:transform .16s,border-color .16s,box-shadow .16s}
.top30-card:hover{transform:translateY(-2px);border-color:#2b6794;box-shadow:0 14px 32px rgba(0,0,0,.18)}
.top30-rank{position:absolute;left:13px;top:13px;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:#12304d;border:1px solid #244e70;color:#b9d7ef;font-size:11px;font-weight:900}
.top30-rank.gold{background:linear-gradient(145deg,#f0bd52,#9b681d);border-color:#e7b54d;color:#fff;box-shadow:0 0 16px rgba(244,191,85,.18)}
.top30-main{display:flex;align-items:center;justify-content:space-between;gap:10px;min-width:0}
.top30-coin{display:flex;align-items:center;gap:9px;min-width:0}
.top30-logo{width:42px;height:42px;flex:0 0 42px;border-radius:50%;overflow:hidden;display:grid;place-items:center;background:#07182a;border:1px solid #2a5277}
.top30-logo .coin-logo{width:40px;height:40px}
.top30-name{min-width:0;display:flex;flex-direction:column}
.top30-name b{font-size:15px;line-height:1.15;color:#f5f9ff}
.top30-name span{margin-top:3px;font-size:9px;color:#718ca7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:125px}
.top30-meta-box{margin-top:10px;padding:10px 12px;border-radius:12px;border:1px solid rgba(111,211,255,.24);background:linear-gradient(100deg,rgba(18,57,84,.48),rgba(8,24,39,.72));}.top30-meta-box.muted{border-color:rgba(111,149,180,.18);background:rgba(7,20,34,.55)}.top30-meta-title{font-size:11px;font-weight:950;color:#8fddff;letter-spacing:.01em}.top30-meta-main{margin-top:4px;font-size:11px;font-weight:800;color:#dceeff;line-height:1.45}.top30-meta-main b{font-size:12px;color:#fff}.top30-meta-main strong{font-size:13px;color:#fff}.top30-meta-note{margin-top:3px;font-size:9px;font-weight:600;color:#7895ae;line-height:1.35}.top30-meta-box.muted .top30-meta-title{color:#9bb1c4}.top30-meta-box.muted .top30-meta-note{color:#637f98} .top30-live{text-align:right;display:flex;flex-direction:column;align-items:flex-end;flex:0 0 auto}
.top30-live span{font-size:9px;color:#718ca7}
.top30-live strong{font-size:18px;line-height:1.15;margin-top:3px}
.top30-live.up strong,.top30-status.up{color:#ff5360}
.top30-live.down strong,.top30-status.down{color:#4da3ff}
.top30-live.flat strong,.top30-status.flat{color:#a6b7c8}
.top30-metrics{display:grid;grid-template-columns:1fr 1.25fr .95fr;gap:6px;margin-top:12px}
.top30-metric{min-width:0;padding:9px 9px 8px;border-radius:11px;background:#091a2d;border:1px solid #163651}
.top30-metric span{display:block;font-size:8px;color:#718da8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.top30-metric b{display:block;margin-top:4px;font-size:14px;color:#dcecff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.top30-metric small{display:block;margin-top:2px;font-size:8px;color:#637f9a}
.top30-metric.score-metric b{color:#71c9ff;font-size:17px}
.top30-metric.leader-metric b{color:#bfe4ff}
.top30-footer{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:9px;padding-top:8px;border-top:1px solid #132f48;color:#617e99;font-size:8px}
.top30-status{font-weight:800;font-size:9px}
/* v73: stronger visual hierarchy + current leader quote */
.top30-card{font-weight:650}
.top30-name b{font-size:17px;font-weight:900;letter-spacing:-.01em}
.top30-name span{font-weight:700}
.top30-live strong{font-size:22px;font-weight:950;letter-spacing:-.02em}
.top30-metric span{font-size:9px;font-weight:800;color:#8ba8c2}
.top30-metric b{font-size:15px;font-weight:900}
.top30-metric.score-metric b{font-size:18px;font-weight:950}
.top30-metric small{font-weight:650}
.top30-metric.leader-metric b{display:flex;align-items:baseline;gap:5px;flex-wrap:wrap}
.leader-live{font-style:normal;font-size:13px;font-weight:950}
.leader-live.up{color:#ff5360}.leader-live.down{color:#4da3ff}.leader-live.flat{color:#a6b7c8}
.top30-footer{font-weight:650}
@media(max-width:700px){.top30-name b{font-size:16px}.top30-live strong{font-size:21px}.top30-metric b{font-size:14px}.top30-metric.score-metric b{font-size:17px}.leader-live{font-size:12px}}
@media(max-width:1200px){.top30-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:700px){.top30-grid{grid-template-columns:1fr;gap:8px}.top30-card{border-radius:16px;padding:13px 12px 10px 47px}.top30-metrics{grid-template-columns:1fr 1.15fr .9fr}.top30-metric{padding:8px 7px}.top30-metric b{font-size:13px}.top30-metric.score-metric b{font-size:16px}.top30-legend{font-size:9px;padding:9px 10px}.top30-name span{max-width:110px}}
</style>
''', unsafe_allow_html=True)


def has_results():
    return all((OUT / f).exists() for f in REQ)


def load_local_data():
    if not has_results():
        return None
    try:
        c = pd.read_csv(OUT / 'candidates_final.csv')
        p = pd.read_csv(OUT / 'pair_patterns_final.csv')
        occ = pd.read_csv(OUT / 'pattern_occurrences_final.csv')
        bt = pd.read_csv(OUT / 'backtest_final.csv')
        meta = json.loads((OUT / 'last_run_final.json').read_text(encoding='utf-8'))
        return c, p, occ, bt, meta
    except Exception as e:
        st.error(f'로컬 결과 파일을 읽는 중 오류가 발생했습니다: {e}')
        return None

@st.cache_data(ttl=15, show_spinner=False)
def load_analysis_api():
    base = os.getenv('COIN_PATTERN_ANALYSIS_API', 'http://127.0.0.1:8510').rstrip('/')
    try:
        r = requests.get(base + '/v1/analysis', timeout=3)
        r.raise_for_status()
        d = r.json()
        if not d.get('ready'):
            return None
        c = pd.DataFrame(d.get('candidates', []))
        p = pd.DataFrame(d.get('patterns', []))
        occ = pd.DataFrame(d.get('occurrences', []))
        bt = pd.DataFrame(d.get('backtest', []))
        meta = d.get('meta', {})
        return c, p, occ, bt, meta
    except Exception:
        return None

def load_data():
    # v55: client-first architecture. In production, every user reads the
    # server-prepared result. Local files remain only as a beta fallback.
    remote = load_analysis_api()
    if remote is not None:
        return remote
    return load_local_data()


def start_first():
    # One shared bootstrap per app instance. Create the lock atomically before
    # spawning the worker so simultaneous visitors cannot launch duplicate jobs.
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            # Recover only from a clearly abandoned lock (e.g. worker was killed).
            if datetime.now().timestamp() - LOCK.stat().st_mtime > 6 * 60 * 60:
                LOCK.unlink(missing_ok=True)
            else:
                return False
        except OSError:
            return False
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'pid': os.getpid(), 'started_at': datetime.now().isoformat()}))
    except FileExistsError:
        return False
    try:
        LOG.write_text('', encoding='utf-8')
        STATUS.write_text('STARTING', encoding='utf-8')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        subprocess.Popen(
            [sys.executable, str(ROOT / 'first_analysis.py')],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags, close_fds=(os.name != 'nt')
        )
        return True
    except Exception as e:
        STATUS.write_text('ERROR:START_FAILED ' + str(e), encoding='utf-8')
        try: LOCK.unlink(missing_ok=True)
        except OSError: pass
        return False


def log_tail():
    if not LOG.exists():
        return ''
    try:
        return LOG.read_text(encoding='utf-8', errors='replace')[-7000:]
    except Exception:
        return ''


def ticker(v):
    return str(v).replace('KRW-', '').replace('USDT-', '')


def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def safe_int(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return default


@st.cache_data(ttl=300, show_spinner=False)
def load_sector_map():
    """Load the product's human-readable market group map."""
    try:
        df=pd.read_csv(SECTOR_MAP_FILE)
        return {str(r.symbol).upper():str(r.sector) for _,r in df.iterrows() if str(r.symbol).strip()}
    except Exception:
        return {}

SECTOR_MAP=load_sector_map()

def market_sector(market):
    return SECTOR_MAP.get(ticker(market).upper(),'')

def sector_label(sector):
    labels={'Gaming':'NFT · 게임','MEME':'밈','AI':'AI','DeFi':'DeFi','Layer1':'레이어1','Layer2':'레이어2','Oracle':'오라클','RWA':'RWA','Infra':'인프라','DePIN':'DePIN'}
    return labels.get(sector,sector)

@st.cache_data(ttl=3600, show_spinner=False)
def market_meta(symbol):
    symbol=ticker(symbol).upper()
    fallback_sector=market_sector(symbol)
    fallback_label=sector_label(fallback_sector) if fallback_sector else ''
    fallback={'meta_group':fallback_sector,'meta_label':fallback_label,
              'meta_status':'fallback' if fallback_label else 'insufficient_data',
              'meta_source':'Coin Pattern fallback taxonomy' if fallback_label else ''}
    return cmc_get_meta(symbol, fallback=fallback)

def meta_diffusion_stats(source, occ):
    """Explanation-only same-meta diffusion stats; never changes the score."""
    src=ticker(source).upper(); src_meta=market_meta(src); group=src_meta.get('meta_group','')
    if not group or occ is None or occ.empty or 'source' not in occ.columns or 'target' not in occ.columns: return None
    o=occ.copy(); o['source']=o['source'].astype(str).map(lambda x:ticker(x).upper()); o['target']=o['target'].astype(str).map(lambda x:ticker(x).upper()); o=o[o.source==src].copy()
    if o.empty or 'target_up' not in o.columns: return None
    rows=[]
    for target,g in o.groupby('target'):
        if target==src or market_meta(target).get('meta_group')!=group: continue
        g=g.copy(); g['target_up']=pd.to_numeric(g['target_up'],errors='coerce'); g=g.dropna(subset=['target_up'])
        if len(g)>=3: rows.append((target,len(g),float(g.target_up.mean())))
    if not rows: return None
    rising=sum(1 for _,_,rate in rows if rate>=0.5)
    return {'sector':src_meta.get('meta_label',''),'raw_sector':group,'total':len(rows),'rising':rising,'rate':rising/len(rows),'peer_stats':rows,'meta_source':src_meta.get('meta_source','')}


@st.cache_data(ttl=60, show_spinner=False)
def load_hourly_candles(markets):
    out={}
    for market in list(markets)[:6]:
        try:
            r=requests.get('https://api.upbit.com/v1/candles/minutes/60', params={'market':market,'count':24}, timeout=5)
            r.raise_for_status()
            rows=r.json()
            closes=[safe_float(x.get('trade_price')) for x in reversed(rows) if x.get('trade_price') is not None]
            if closes: out[str(market)]=closes
        except Exception:
            continue
    return out

def sparkline_svg(values, up=True):
    vals=[float(v) for v in values if v is not None]
    if len(vals)<2: return ''
    lo,hi=min(vals),max(vals); span=hi-lo if hi!=lo else 1.0
    pts=[]
    for i,v in enumerate(vals):
        x=4+(i*(172/(len(vals)-1))); y=30-((v-lo)/span)*24
        pts.append(f'{x:.1f},{y:.1f}')
    stroke='#ff5360' if up else '#4da3ff'
    return '<svg class="mini-spark" viewBox="0 0 180 34" preserveAspectRatio="none"><polyline points="'+ ' '.join(pts) +'" fill="none" stroke="'+stroke+'" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>'

@st.cache_data(ttl=20, show_spinner=False)
def load_live_tickers():
    r = requests.get('https://api.upbit.com/v1/ticker/all', params={'quote_currencies':'KRW'}, timeout=8)
    r.raise_for_status()
    return pd.DataFrame(r.json())


@st.cache_data(ttl=3600, show_spinner=False)
def load_market_meta():
    """Upbit market metadata used only for coin logos/names in the UI."""
    try:
        r = requests.get('https://api.upbit.com/v1/market/all', params={'isDetails':'false'}, timeout=8)
        r.raise_for_status()
        rows = r.json()
        return {str(x.get('market')): x for x in rows if x.get('market')}
    except Exception:
        return {}

def coin_logo(market, size=42):
    """Use Upbit's official static coin logo; fall back to a clean monogram."""
    t = ticker(market)
    if not t:
        return '<span class="coin-logo-fallback">•</span>'
    url = f'https://static.upbit.com/logos/{t}.png'
    return (f'<img class="coin-logo" src="{url}" alt="{t}" width="{size}" height="{size}" '
            f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\'">'
            f'<span class="coin-logo-fallback" style="display:none">{t[:1]}</span>')

def coin_full_name(market):
    meta = load_market_meta().get(str(market), {})
    return str(meta.get('english_name') or meta.get('korean_name') or '').strip()


def merge_live(c):
    try:
        t = load_live_tickers()
        if t.empty or 'market' not in t.columns:
            return c.copy(), False
        keep = [x for x in ['market','trade_price','signed_change_rate','acc_trade_price_24h','timestamp','opening_price','high_price','low_price'] if x in t.columns]
        t = t[keep].copy().rename(columns={'trade_price':'live_price','signed_change_rate':'live_ret','acc_trade_price_24h':'live_value_24h','timestamp':'live_timestamp'})
        return c.merge(t, on='market', how='left'), True
    except Exception:
        return c.copy(), False


def live_change(r):
    v = r.get('live_ret', None)
    live = v is not None and not pd.isna(v)
    if not live:
        v = r.get('ret1', 0)
    v = safe_float(v)
    cls = 'red' if v > 0 else 'blue' if v < 0 else ''
    arrow = '↑' if v > 0 else '↓' if v < 0 else '→'
    return v, cls, arrow, live


def source_label(ret):
    if ret >= .10: return '급등 선행'
    if ret >= .05: return '강한 선행'
    if ret >= .03: return '상승 선행'
    return '과거 선행'


def icon(t):
    # No external image dependency; stable branded monogram.
    return ticker(t)[:1].upper() or '•'


def freshness_text(meta):
    if not isinstance(meta, dict):
        return '분석 시각 확인 필요', 'gray'
    raw = meta.get('run_at', '')
    try:
        if raw is None or str(raw).strip() in ('', '-', 'nan', 'NaT'):
            return '분석 시각 확인 필요', 'gray'
        ts = pd.to_datetime(raw)
        now = pd.Timestamp.now(tz=ts.tz) if getattr(ts, 'tzinfo', None) else pd.Timestamp.now()
        age = max(0.0, (now - ts).total_seconds() / 3600)
        if age < 1: return '방금 분석 완료', 'green'
        if age < 24: return f'최근 분석 {age:.0f}시간 전', 'green'
        if age < 72: return f'최근 분석 {age/24:.1f}일 전', 'gold'
        return f'분석 갱신 필요 · {age/24:.0f}일 경과', 'red'
    except Exception:
        return '분석 시각 확인 필요', 'gray'


def render_header(meta):
    run_at = meta.get('run_at', '') if isinstance(meta, dict) else ''
    fresh, _ = freshness_text(meta)
    try:
        run_display = pd.to_datetime(run_at).strftime('%m월 %d일 %H:%M') if run_at and str(run_at) not in ('nan','NaT','-') else '분석 시각 확인 필요'
    except Exception:
        run_display = '분석 시각 확인 필요'
    st.markdown(f'''
    <div class="hero">
      <div class="brand"><div class="brandmark">◈</div><div><div class="hero h1" style="padding:0;background:none;box-shadow:none;border:0;margin:0"><h1>Coin Pattern</h1></div><div class="tag">시장 흐름을 데이터로 확인하는 베타</div></div></div>
      <div class="hero-sub">지금 움직이는 코인 다음에, 과거에는 어떤 코인이 움직였을까?</div>
      <div class="pills"><span class="pill red">🔴 현재 선행 · 오늘 움직이는 코인</span><span class="pill blue">🔵 다음 관심 · 과거 다음날 패턴</span><span class="pill green">BETA · 과거 데이터 기반</span><span class="pill">{fresh}</span><span class="pill">최근 분석 · {run_display}</span></div>
    </div>''', unsafe_allow_html=True)


@st.fragment(run_every="10s")
def render_start():
    """Web deployment: automatically start the original 730-day bootstrap on first visit."""
    # Start once per Streamlit session; users should not need to press an admin bootstrap button.
    if not st.session_state.get('_cp_auto_bootstrap_attempted', False):
        st.session_state['_cp_auto_bootstrap_attempted'] = True
        if not LOCK.exists():
            started = start_first()
            if started:
                st.info('최초 접속 분석을 자동으로 시작했습니다. Upbit + Binance 730일 데이터를 준비하고 있습니다.')

    if has_results():
        st.success('730일 과거 데이터 분석이 준비되었습니다. 분석 화면으로 이동합니다.')
        st.rerun()
        return

    if LOCK.exists() or (STATUS.exists() and STATUS.read_text(encoding='utf-8', errors='replace').startswith('STARTING')):
        st.markdown('<div class="empty"><div style="font-size:36px">◈</div><h3 style="margin:8px 0;color:#fff">730일 과거 데이터 분석 중입니다</h3><p>처음 한 번만 과거 데이터를 준비합니다. 완료되면 분석 화면이 자동으로 열립니다.</p></div>', unsafe_allow_html=True)
        txt = log_tail()
        if txt:
            st.code(txt[-3500:])
        if STATUS.exists():
            status = STATUS.read_text(encoding='utf-8', errors='replace')
            if status.startswith('ERROR'):
                st.error('자동 분석 중 오류가 발생했습니다. 아래 실행 기록을 확인해 주세요.')
                if txt:
                    st.code(txt[-7000:])
    else:
        status = STATUS.read_text(encoding='utf-8', errors='replace') if STATUS.exists() else ''
        st.markdown('<div class="empty"><div style="font-size:36px">◈</div><h3 style="margin:8px 0;color:#fff">730일 분석을 준비하지 못했습니다</h3><p>서버 실행 환경에서 데이터 수집이 차단되었거나 오류가 발생했을 수 있습니다.</p></div>', unsafe_allow_html=True)
        if status:
            st.caption(f'상태: {status}')
        txt = log_tail()
        if txt:
            st.code(txt[-7000:])
        if st.button('다시 시도', type='primary', use_container_width=True, key='retry_auto_bootstrap'):
            st.session_state['_cp_auto_bootstrap_attempted'] = False
            st.rerun()


@st.fragment(run_every="20s")
def render_live_flow_fragment(p, occ):
    """Refresh only the Market Flow section every 20 seconds."""
    try:
        t=load_live_tickers()
        if t.empty:
            render_flow(pd.DataFrame(),p,pd.DataFrame(),occ)
            return
        keep=[x for x in ['market','trade_price','signed_change_rate','acc_trade_price_24h','timestamp','opening_price','high_price','low_price'] if x in t.columns]
        live_df=t[keep].copy().rename(columns={'trade_price':'live_price','signed_change_rate':'live_ret','acc_trade_price_24h':'live_value_24h','timestamp':'live_timestamp'})
        render_flow(pd.DataFrame(),p,live_df,occ)
    except Exception:
        render_flow(pd.DataFrame(),p,pd.DataFrame(),occ)

def render_flow(c, p, live_df, occ=None, max_leaders=4, targets_per_leader=3, compact=False):
    """v60: visually prioritize current leader -> historical followers -> current target state."""
    st.caption('● 현재 시장 흐름은 Upbit 시세를 약 20초마다 확인하고, 각 선행 코인의 최근 24시간 움직임을 함께 보여줍니다.')
    st.markdown('<div class="section-title"><div><h2>🔥 오늘의 시장 흐름</h2><p>지금 강하게 움직이는 선행 코인과 과거 다음날 이어졌던 패턴을 한눈에 연결합니다.</p></div><span class="section-badge">LIVE → HISTORY</span></div>', unsafe_allow_html=True)
    leader_flows=_live_leader_flow(live_df,p,max_leaders=max_leaders,targets_per_leader=targets_per_leader)
    # v67: detect when the live market leader set changes so the user can see that
    # the Flow is not a static ranking. Session state avoids any extra data work.
    current_leaders=tuple(x.get('source') for x in leader_flows[:3])
    previous_leaders=st.session_state.get('flow_leaders_snapshot')
    if previous_leaders and current_leaders and current_leaders != previous_leaders:
        changed=[ticker(x) for x in current_leaders if x not in previous_leaders]
        if changed:
            st.markdown(f'<div class="flow-change-banner">🆕 <b>새로운 시장 흐름</b> · {", ".join(changed)}가 새롭게 강한 선행 코인으로 포착됐습니다.</div>', unsafe_allow_html=True)
    if current_leaders:
        st.session_state['flow_leaders_snapshot']=current_leaders
    if not leader_flows:
        st.markdown('<div class="empty"><b>현재 선행 코인을 확인할 수 없습니다.</b><br>Upbit 현재가 스냅샷이 연결되면 자동으로 흐름을 구성합니다.</div>',unsafe_allow_html=True)
        return
    spark_map=load_hourly_candles([x['source'] for x in leader_flows])
    html=['<div class="flow-panel flow-main-panel v59-home-flow">']
    for idx,item in enumerate(leader_flows):
        src=item['source']; sr=item['source_live']; targets=item['targets']
        badge='🔥 강한 선행' if sr>=.05 else '↑ 상승 선행' if sr>=.03 else '선행'
        meta_stats=meta_diffusion_stats(src, occ)
        spark=sparkline_svg(spark_map.get(src,[]), up=sr>=0)
        source_class='v59-flow-source hot' if idx==0 else 'v59-flow-source'
        target_cards=[]
        for rank,t in enumerate(targets[:targets_per_leader],1):
            now=t.get('current_ret')
            if now is None:
                now_html='현재 시세 확인 필요'; now_cls=''
            else:
                now_html=f'현재 {now:+.1%} ↑' if now>0 else f'현재 {now:+.1%} ↓' if now<0 else '현재 0.0%'
                now_cls='down' if now<0 else ''
            tspark=sparkline_svg(load_hourly_candles([t['market']]).get(t['market'],[]), up=(now is None or now>=0)) if t.get('market') else ''
            full=coin_full_name(t['market'])
            name_line=f'<b>{ticker(t["market"])}</b>' + (f'<small>{full}</small>' if full else '')
            target_meta=market_meta(t['market'])
            source_meta=market_meta(src)
            same_meta = bool(target_meta.get('meta_group') and target_meta.get('meta_group') == source_meta.get('meta_group'))
            explanation=f'{ticker(t["market"])}는 {ticker(src)}가 강하게 움직인 뒤 다음날 상승한 과거 사례가 {t["obs"]}회 관찰됐습니다.'
            if same_meta:
                explanation += f' 같은 {target_meta.get("meta_label") or "동일 메타"}입니다.'
            if now is not None and now >= 0.10:
                explanation += ' 현재도 이미 크게 상승 중입니다.'
            target_cards.append(f'<div class="v59-target-card"><div class="v59-target-rank">{rank}</div><div class="v59-target-logo">{coin_logo(t["market"], 28)}</div><div class="v59-target-main">{name_line}<span>과거 다음날 상승 {t["pattern_rate"]:.1%} · {t["obs"]}회</span><small class="v59-target-why">{explanation}</small></div><div class="v59-target-now {now_cls}">{now_html}</div><div class="v59-target-spark">{tspark}</div></div>')
        if not target_cards:
            target_cards=['<div class="v59-empty">과거 A→B 데이터가 충분한 후행 코인이 없습니다.</div>']
        # Straight connector: keep the arrow in a dedicated middle column.
        # This intentionally replaces the curved SVG connector because the curved version
        # could overlap follower cards at different screen widths.
        arrow_color = '#ff4a86' if idx == 0 else ('#39a9ff' if idx == 1 else ('#9b5cff' if idx == 2 else '#ff9f43'))
        connector=f'<div class="v59-connector-col"><span class="v59-straight-arrow" style="color:{arrow_color}">→</span></div>'
        html.append(f"""<div class="v59-flow-row">
<div class="{source_class}">
  <div class="flow-source-head"><div class="flow-source-meta"><span class="flow-rank">{idx+1}</span><div class="v59-source-logo">{coin_logo(src, 44)}</div><div><div class="flow-source-name">{ticker(src)}</div><div class="flow-source-sub">{coin_full_name(src) or '오늘의 선행 · Upbit 현재 시세'}</div></div></div><span class="flow-badge">{badge}</span></div>
  <div class="flow-source-main"><div><div class="flow-live-big">{sr:+.1%} ↑</div><div class="flow-source-price">현재 움직임</div></div><div class="spark-wrap">{spark}<span>최근 24시간</span></div></div>
  <div class="card-footer"><span>🔥 관심도 {"높음" if sr>=.03 else "보통"}</span><span>🛡 신뢰도 참고</span><span>📈 A→B 패턴 연결</span></div>
</div>
{connector}
<div class="v59-flow-targets-panel">{(f'<div class="meta-diffusion-box"><div class="meta-diffusion-kicker">🧩 같은 메타 · {meta_stats["sector"]}</div><div class="meta-diffusion-main">과거 사례에서 <b>{meta_stats["total"]}개 종목 중 {meta_stats["rising"]}개</b>가 다음날 상승</div><div class="meta-diffusion-rate">{meta_stats["rate"]:.1%} · 점수에 반영하지 않는 설명용 통계</div></div>' if meta_stats else '')}<div class="flow-branch-title" style="margin-bottom:5px">과거 후행 패턴 <span>선행 → 다음날</span></div>{''.join(target_cards)}</div>
</div>""")
    html.append('</div>')
    st.markdown(''.join(html),unsafe_allow_html=True)
    st.caption('※ 현재 상승률은 실시간 시세이고, 후행 수치는 과거 선행 코인 발생 후 다음날의 A→B 관찰입니다. 둘은 서로 다른 정보입니다.')


def render_home_lower_panels(c, p, live_df):
    """v59: compact secondary panels matching the new product hierarchy."""
    changes=daily_changes(c)
    live=c.copy()
    live['live_ret_num']=live.apply(lambda r: live_change(r)[0], axis=1)
    leaders=_live_leader_flow(live_df,p,max_leaders=3,targets_per_leader=2)
    up=int((live.live_ret_num>0).sum()) if not live.empty else 0
    down=int((live.live_ret_num<0).sum()) if not live.empty else 0
    top_movers=live.sort_values('live_ret_num',ascending=False).head(3) if not live.empty else pd.DataFrame()

    # Briefing
    b=['<div class="v59-lower-card"><div class="v59-lower-head"><h3>📊 오늘의 시장 브리핑</h3><span>현재 시장</span></div>']
    b.append(f'<div class="v59-brief-stat"><b style="color:#25d7a0">상승 종목</b><strong>{up}개</strong></div>')
    b.append(f'<div class="v59-brief-stat"><b style="color:#4da3ff">하락 종목</b><strong>{down}개</strong></div>')
    b.append('<div style="margin-top:10px;color:#8fa9c2;font-size:11px">현재 강한 코인 TOP 3</div>')
    for _,r in top_movers.iterrows():
        rr=safe_float(r.get('live_ret_num')); cls='up' if rr>0 else 'down'; b.append(f'<div class="v59-mini-row"><span class="v59-mini-logo">{coin_logo(r.get("market"),18)}</span><b>{ticker(r.get("market"))}</b><span class="{cls}">{rr:+.1%}</span></div>')
    b.append('</div>')

    # Daily pulse
    d=['<div class="v59-lower-card"><div class="v59-lower-head"><h3>📣 오늘 달라진 시장</h3><span>이전 분석 대비</span></div>']
    if changes.empty:
        d.append('<div class="v59-empty">이전 분석 기록이 쌓이면 새롭게 등장하거나 관심도가 크게 변한 코인을 보여줍니다.</div>')
    else:
        for _,r in changes.head(4).iterrows():
            rr=safe_float(r.get('live_ret',0)); delta=safe_float(r.get('score_delta',0)); cls='up' if rr>=0 else 'down';
            d.append(f'<div class="v59-mini-row"><span class="v59-mini-logo">{coin_logo(r.get("market"),18)}</span><b>{ticker(r.get("market"))}</b><span class="{cls}">{rr:+.1%} · 점수 {delta:+.1f}</span></div>')
    d.append('</div>')

    # Watchlist
    w=['<div class="v59-lower-card"><div class="v59-lower-head"><h3>⭐ 내 관심 코인</h3><span>저장한 코인</span></div>']
    items=load_watchlist()
    if not items:
        w.append('<div class="v59-empty">관심 코인을 저장하면 현재 등락과 시장 흐름 변화를 이곳에서 확인할 수 있습니다.</div>')
    else:
        shown=0
        for m in items:
            hit=live[live.market.astype(str).map(ticker)==ticker(m)]
            if hit.empty: continue
            r=hit.iloc[0]; rr=safe_float(r.get('live_ret_num')); cls='up' if rr>=0 else 'down'
            w.append(f'<div class="v59-mini-row"><span class="v59-mini-logo">{coin_logo(m,18)}</span><b>⭐ {ticker(m)}</b><span class="{cls}">{rr:+.1%}</span></div>')
            shown+=1
            if shown>=4: break
        if shown==0: w.append('<div class="v59-empty">저장된 관심 코인의 현재 시세를 확인하지 못했습니다.</div>')
    w.append('</div>')
    st.markdown('<div class="v59-home-lower">'+''.join(b)+''.join(d)+''.join(w)+'</div>',unsafe_allow_html=True)


def render_analysis_records(meta, bt):
    """Premium, human-readable analysis history. Keep raw JSON/DataFrames out of the main UI."""
    if not isinstance(meta, dict):
        meta = {}
    run_at = meta.get('run_at', '')
    try:
        run_display = pd.to_datetime(run_at).strftime('%Y년 %m월 %d일 %H:%M') if run_at and str(run_at) not in ('nan','NaT','-') else '분석 시각 확인 필요'
    except Exception:
        run_display = '분석 시각 확인 필요'

    fresh, fresh_cls = freshness_text(meta)
    fresh_class = {'green':'green','gold':'gold','red':'red','gray':'gray'}.get(fresh_cls, 'gray')
    def num_meta(key):
        v=str(meta.get(key, '')).strip()
        return int(safe_float(v)) if v not in ('','nan','None') else 0
    markets, rows, patterns = num_meta('markets'), num_meta('rows'), num_meta('patterns')

    st.markdown(f'''
    <div class="analysis-record-hero">
      <div>
        <div class="ar-kicker">ANALYSIS HISTORY</div>
        <h2>이번 분석은 정상적으로 완료됐습니다</h2>
        <p>복잡한 원본 데이터 대신, 실제 서비스에서 의미 있는 분석 결과만 보기 쉽게 정리했습니다.</p>
      </div>
      <div class="ar-status {fresh_class}"><span>●</span> {fresh}</div>
    </div>''', unsafe_allow_html=True)

    st.markdown(f'''
    <div class="analysis-summary-grid">
      <div class="analysis-summary-card wide">
        <div class="ar-label">최근 분석</div>
        <div class="ar-value ar-date">{run_display}</div>
        <div class="ar-sub">730일 과거 데이터 기반 분석 · A→B 선행 패턴 중심</div>
      </div>
      <div class="analysis-summary-card"><div class="ar-label">분석 마켓</div><div class="ar-value">{markets:,}<span>개</span></div><div class="ar-sub">Upbit KRW 기준</div></div>
      <div class="analysis-summary-card"><div class="ar-label">원천 데이터</div><div class="ar-value">{rows:,}<span>행</span></div><div class="ar-sub">분석에 사용된 시계열</div></div>
      <div class="analysis-summary-card"><div class="ar-label">관찰 패턴</div><div class="ar-value">{patterns:,}<span>건</span></div><div class="ar-sub">선행 → 다음날 흐름</div></div>
    </div>''', unsafe_allow_html=True)

    if not bt.empty:
        r = bt.iloc[0]
        total, hit, dd = safe_float(r.get('total_return')), safe_float(r.get('hit_rate')), safe_float(r.get('max_drawdown'))
        sharpe, avg = safe_float(r.get('sharpe_approx')), safe_float(r.get('avg_daily_return'))
        days = int(safe_float(r.get('days', 0))) if str(r.get('days','')).strip() not in ('','nan','None') else 0
        top_n = int(safe_float(r.get('top_n', 30))) if str(r.get('top_n','')).strip() not in ('','nan','None') else 30
        tone_total = 'up' if total > 0 else 'down' if total < 0 else 'neutral'
        tone_dd = 'down' if dd < 0 else 'neutral'
        st.markdown(f'''
        <div class="analysis-block-head"><div><h3>📊 A→B 패턴 백테스트</h3><p>실제 운영에 사용하는 선행 흐름을 과거 구간에서 검증한 참고 결과입니다.</p></div><span>최근 {days}일 · TOP {top_n}</span></div>
        <div class="analysis-backtest-grid">
          <div class="analysis-result-card {tone_total}"><div class="ar-label">누적 수익</div><div class="ar-result">{total:.1%}</div><div class="ar-sub">과거 검증 참고값</div></div>
          <div class="analysis-result-card"><div class="ar-label">적중률</div><div class="ar-result">{hit:.1%}</div><div class="ar-sub">상승으로 끝난 비율</div></div>
          <div class="analysis-result-card {tone_dd}"><div class="ar-label">최대 낙폭</div><div class="ar-result">{dd:.1%}</div><div class="ar-sub">검증 구간 최대 하락폭</div></div>
          <div class="analysis-result-card"><div class="ar-label">평균 일수익</div><div class="ar-result">{avg:.1%}</div><div class="ar-sub">하루 기준 평균</div></div>
          <div class="analysis-result-card"><div class="ar-label">Sharpe 근사</div><div class="ar-result">{sharpe:.2f}</div><div class="ar-sub">위험 대비 성과 참고값</div></div>
        </div>
        <div class="analysis-note">※ 백테스트는 과거 데이터에 대한 검증 결과이며 미래 수익을 보장하지 않습니다. 적중률만으로 투자 결과를 판단하지 않습니다.</div>
        ''', unsafe_allow_html=True)

    self_path = OUT / 'validation_self_final.csv'
    if self_path.exists():
        try:
            sv = pd.read_csv(self_path)
            if not sv.empty:
                r = sv.iloc[0]
                s_avg, s_hit, s_dd, s_sh = safe_float(r.get('avg_daily_return')), safe_float(r.get('hit_rate')), safe_float(r.get('max_drawdown')), safe_float(r.get('sharpe_approx'))
                st.markdown(f'''
                <div class="analysis-diagnostic">
                  <div class="analysis-block-head"><div><h3>🧪 자기연속(A→A) 진단</h3><p>같은 코인이 계속 오르는 패턴을 별도로 점검한 결과입니다.</p></div><span class="diag-badge">운영 점수에 직접 사용하지 않음</span></div>
                  <div class="diag-grid"><div><span>평균 일수익</span><b class="down">{s_avg:.1%}</b></div><div><span>적중률</span><b>{s_hit:.1%}</b></div><div><span>최대 낙폭</span><b class="down">{s_dd:.1%}</b></div><div><span>Sharpe 근사</span><b>{s_sh:.2f}</b></div></div>
                  <div class="analysis-note">자기연속 신호는 별도 진단용으로만 보관하며, 선행 코인 → 후행 코인 흐름과 구분합니다.</div>
                </div>''', unsafe_allow_html=True)
        except Exception:
            pass

    st.markdown('''
    <div class="analysis-footer-card"><div class="af-icon">✓</div><div><b>분석 기록은 개발자용 원본 로그가 아닙니다.</b><p>이 화면에서는 사용자가 이해할 수 있는 핵심 결과만 보여주고, JSON·원본 테이블 같은 기술 정보는 숨겼습니다.</p></div></div>''', unsafe_allow_html=True)

def render_backtest(bt):
    if bt.empty: return
    r = bt.iloc[0]
    st.markdown('<div class="section-title"><div><h2>📊 A→B 패턴 검증</h2><p>운영 점수에 사용한 선행 흐름의 과거 검증 결과입니다.</p></div><span class="section-badge">BACKTEST</span></div>', unsafe_allow_html=True)
    vals = [('누적 수익', safe_float(r.get('total_return')), 'percent'), ('적중률', safe_float(r.get('hit_rate')), 'percent'), ('최대 낙폭', safe_float(r.get('max_drawdown')), 'percent'), ('Sharpe 근사', safe_float(r.get('sharpe_approx')), 'raw')]
    html = '<div class="metricbar">'
    for k,v,fmt in vals:
        text = f'{v:.1%}' if fmt=='percent' else f'{v:.2f}'
        html += f'<div class="metric"><div class="k">{k}</div><div class="v">{text}</div><div class="s">과거 검증 참고값</div></div>'
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_beta_feedback():
    st.markdown('<div class="section-title"><div><h2>🧪 베타 피드백</h2><p>지금 단계에서는 기능을 더 늘리기보다 실제 사용 경험을 개선합니다.</p></div><span class="section-badge">BETA</span></div>', unsafe_allow_html=True)
    st.caption('이 피드백은 이 PC에만 익명으로 저장됩니다. 결제나 회원가입은 필요하지 않습니다.')
    a,b,d=st.columns(3)
    with a:
        if st.button('👍 무엇을 봐야 하는지 바로 이해됨',use_container_width=True,key='beta_feedback_clear'):
            track('beta_feedback','clear'); st.success('기록했습니다.')
    with b:
        if st.button('🤔 유용하지만 더 써봐야 함',use_container_width=True,key='beta_feedback_useful'):
            track('beta_feedback','useful'); st.success('기록했습니다.')
    with d:
        if st.button('🧩 아직 어렵거나 부족함',use_container_width=True,key='beta_feedback_hard'):
            track('beta_feedback','hard'); st.success('기록했습니다.')


def render_top30(c, occ=None):
    st.markdown(
        '<div class="section-title"><div><h2>🏆 오늘의 관심 후보 TOP 30</h2>'
        '<p>상위 5개는 공개하고, 전체 후보는 무료 회원에게 공개합니다.</p></div>'
        '<span class="section-badge">TOP 30</span></div>',
        unsafe_allow_html=True
    )

    if c is None or c.empty:
        st.markdown('<div class="empty"><h3>표시할 후보가 없습니다.</h3><p>분석 결과가 준비되면 TOP 30이 표시됩니다.</p></div>', unsafe_allow_html=True)
        return

    # v73: show the source/leader coin's current Upbit change directly on each card.
    source_live = {}
    try:
        _lt = load_live_tickers()
        if not _lt.empty and 'market' in _lt.columns and 'signed_change_rate' in _lt.columns:
            source_live = {str(r.market): safe_float(r.signed_change_rate) for r in _lt[['market','signed_change_rate']].itertuples(index=False)}
    except Exception:
        source_live = {}

    rows=[]
    for rank, (_, r) in enumerate(c.head(30).iterrows(), 1):
        market=str(r.get('market',''))
        name=ticker(market)
        live=safe_float(r.get('live_ret', r.get('ret1')))
        score=safe_float(r.get('score'))
        rate=safe_float(r.get('pattern_rate'))
        obs=safe_int(r.get('pattern_obs'))
        src_market=str(r.get('best_source','') or '')
        src=ticker(src_market)
        src_live=source_live.get(src_market)
        src_cls='up' if src_live is not None and src_live > 0.0001 else 'down' if src_live is not None and src_live < -0.0001 else 'flat'
        cls='up' if live > 0.0001 else 'down' if live < -0.0001 else 'flat'
        rank_cls='gold' if rank <= 3 else ''
        status='상승 중' if cls=='up' else '하락 중' if cls=='down' else '보합'
        meta_stats = meta_diffusion_stats(src_market, occ) if src_market else None
        src_meta = market_meta(src_market) if src_market else {'meta_label':'','meta_status':'insufficient_data','meta_source':''}
        meta_label = src_meta.get('meta_label','')
        if meta_stats:
            meta_html = f'<div class="top30-meta-box"><div class="top30-meta-title">🧩 같은 메타 · {meta_stats["sector"]}</div><div class="top30-meta-main">과거 사례에서 <b>{meta_stats["total"]}개 종목 중 {meta_stats["rising"]}개</b>가 다음날 상승 <strong>{meta_stats["rate"]:.1%}</strong></div><div class="top30-meta-note">분류: {src_meta.get("meta_source") or "Coin Pattern"} · 점수에 반영하지 않는 설명용 통계</div></div>'
        elif meta_label:
            meta_html = f'<div class="top30-meta-box muted"><div class="top30-meta-title">🧩 같은 메타 · {meta_label}</div><div class="top30-meta-note">메타는 확인되지만 이 선행 코인의 과거 확산 사례가 충분하지 않습니다.</div></div>'
        else:
            meta_html = '<div class="top30-meta-box muted"><div class="top30-meta-title">🧩 메타 분류 데이터 부족</div><div class="top30-meta-note">CMC 또는 내부 분류 데이터가 충분하지 않습니다.</div></div>'
        rows.append(f"""
        <div class="top30-card">
          <div class="top30-rank {rank_cls}">{rank}</div>
          <div class="top30-main">
            <div class="top30-coin">
              <div class="top30-logo">{coin_logo(market, 42)}</div>
              <div class="top30-name"><b>{name}</b><span>{coin_full_name(market) or 'Upbit KRW'}</span></div>
            </div>
            <div class="top30-live {cls}"><span>현재</span><strong>{live:+.1%}</strong></div>
          </div>
          <div class="top30-metrics">
            <div class="top30-metric score-metric"><span>상대 관심도</span><b>{score:.1f}</b><small>여러 조건 종합</small></div>
            <div class="top30-metric"><span>과거 다음날 상승</span><b>{rate:.1%}</b><small>{obs}회 관찰</small></div>
            <div class="top30-metric leader-metric"><span>선행 코인 · 현재</span><b>{src or '—'}{f' <em class="leader-live {src_cls}">{src_live:+.1%}</em>' if src_live is not None else ''}</b><small>{'현재 상승 중' if src_cls=='up' else '현재 하락 중' if src_cls=='down' else '현재 보합' if src_live is not None else '현재 시세 확인 불가'}</small></div>
          </div>
          {meta_html}
          <div class="top30-footer"><span>과거 A→B 패턴 기준 · 예측값 아님</span><span class="top30-status {cls}">{status}</span></div>
        </div>""")

    st.markdown('<div class="top30-legend"><span>현재 = Upbit 실시간 시세 스냅샷</span><span>과거 다음날 상승 = 선행 코인 발생 다음날의 역사적 결과</span><span>※ 예측값이 아닌 과거 관찰 데이터</span></div>', unsafe_allow_html=True)
    # Product gate: let new visitors experience the real data before asking for an account.
    # TOP 5 stays public; ranks 6-30 are the free-member conversion point.
    st.markdown('<div class="top30-grid">'+''.join(rows[:5])+'</div>', unsafe_allow_html=True)
    if len(rows) > 5:
        member=current_server_user()
        if member:
            st.markdown('<div class="top30-grid">'+''.join(rows[5:30])+'</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="top30-more-gate">'
                '<div class="top30-more-kicker">FREE MEMBER</div>'
                '<h3>🔓 전체 관심 후보를 확인해보세요</h3>'
                '<p>상위 5개는 공개되어 있습니다. 무료 회원가입하면 6위부터 30위까지 전체 후보를 볼 수 있습니다.</p>'
                '</div>', unsafe_allow_html=True
            )
            render_member_gate('전체 관심 후보')

def build_share_card_svg(selected, live, score, rate, obs, src, sr, avg_next, bn):
    # Dependency-free 1080x1350 SVG share card.
    def esc(v):
        return html.escape(str(v), quote=True)
    live_color = "#ff4d5f" if live > 0 else "#4da3ff" if live < 0 else "#b9c7d8"
    live_text = f"{live:+.1%}"
    src_text = src or "없음"
    bn_text = "감지됨" if bn else "미감지"
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350" viewBox="0 0 1080 1350">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#071522"/><stop offset="1" stop-color="#102b45"/></linearGradient></defs>
<rect width="1080" height="1350" fill="url(#bg)"/><rect x="52" y="52" width="976" height="1246" rx="42" fill="none" stroke="#234664" stroke-width="2"/>
<text x="86" y="118" fill="#7ed8ff" font-family="Arial,sans-serif" font-size="28" font-weight="700">COIN PATTERN</text>
<text x="86" y="164" fill="#8da8bf" font-family="Arial,sans-serif" font-size="22">선행 코인으로 보는 다음 관심 흐름</text>
<text x="86" y="290" fill="#ffffff" font-family="Arial,sans-serif" font-size="76" font-weight="900">{esc(selected)}</text>
<text x="86" y="350" fill="#9db5ca" font-family="Arial,sans-serif" font-size="24">현재 등락</text><text x="86" y="414" fill="{live_color}" font-family="Arial,sans-serif" font-size="54" font-weight="800">{live_text}</text>
<text x="700" y="350" fill="#9db5ca" font-family="Arial,sans-serif" font-size="24">상대 관심 점수</text><text x="700" y="414" fill="#69c9ff" font-family="Arial,sans-serif" font-size="54" font-weight="800">{score:.1f}</text>
<rect x="86" y="472" width="908" height="2" fill="#234664"/>
<text x="86" y="540" fill="#9db5ca" font-family="Arial,sans-serif" font-size="22">과거 A→B 패턴</text><text x="86" y="610" fill="#ffffff" font-family="Arial,sans-serif" font-size="46" font-weight="800">{rate:.1%}</text><text x="330" y="610" fill="#8da8bf" font-family="Arial,sans-serif" font-size="25">/ {obs}회 관찰</text>
<text x="86" y="688" fill="#9db5ca" font-family="Arial,sans-serif" font-size="22">선행 코인</text><text x="86" y="744" fill="#ffffff" font-family="Arial,sans-serif" font-size="34" font-weight="700">{esc(src_text)}</text><text x="86" y="805" fill="#8da8bf" font-family="Arial,sans-serif" font-size="21">선행 당시 상승률 {sr:+.1%} · 평균 다음날 {avg_next:+.2%}</text>
<rect x="86" y="866" width="908" height="126" rx="24" fill="#0b2136" stroke="#1e496b"/><text x="116" y="915" fill="#a9bfd1" font-family="Arial,sans-serif" font-size="21">Binance 선행</text><text x="116" y="957" fill="#ffffff" font-family="Arial,sans-serif" font-size="29" font-weight="700">{bn_text}</text>
<text x="86" y="1080" fill="#7ed8ff" font-family="Arial,sans-serif" font-size="30" font-weight="800">오늘의 시장 흐름을 데이터로 확인하세요.</text><text x="86" y="1140" fill="#8da8bf" font-family="Arial,sans-serif" font-size="21">Coin Pattern · {generated}</text><text x="86" y="1220" fill="#71899f" font-family="Arial,sans-serif" font-size="18">※ 과거 관찰 데이터에 기반한 정보이며 미래 가격이나 수익을 보장하지 않습니다.</text>
</svg>"""


def render_share_card(c, r, selected):
    score=safe_float(r.get("score")); rate=safe_float(r.get("pattern_rate")); obs=safe_int(r.get("pattern_obs"))
    src=ticker(r.get("best_source","")); sr=safe_float(r.get("source_ret")); cur=safe_float(r.get("ret1"))
    live=safe_float(r.get("live_ret"), cur); avg_next=safe_float(r.get("pattern_avg_ret")); bn=safe_int(r.get("bn_lead"))
    svg=build_share_card_svg(selected, live, score, rate, obs, src, sr, avg_next, bn)
    track("share_card_created", selected)
    st.markdown("### 📤 공유용 분석 카드")
    st.caption("카카오톡·커뮤니티·SNS 등에 공유할 수 있는 4:5 비율 카드입니다. 파일은 이 PC에서만 생성됩니다.")
    st.download_button("⬇️ 분석 카드 저장 (SVG)", data=svg.encode("utf-8"), file_name=f"coin_pattern_{selected.replace("/", "_")}.svg", mime="image/svg+xml", use_container_width=True, key=f"share_download_{selected}")
    share_text=(f"📊 Coin Pattern | {selected}\n현재 등락 {live:+.1%} · 관심 점수 {score:.1f}\n"
                f"과거 A→B 상승률 {rate:.1%} ({obs}회)\n선행 코인 {src or '없음'}\n"
                f"※ 과거 관찰 데이터 기반이며 미래 가격·수익을 보장하지 않습니다.")
    st.markdown("**공유 문구**")
    st.code(share_text, language=None)


def render_detail(c, occ):
    st.markdown('<div class="section-title"><div><h2>🔎 코인 상세 분석</h2><p>현재 시세 + 선행 코인 + <b>다음날</b> 기준의 과거 A→B 패턴을 한 화면에서 확인합니다.</p></div><span class="section-badge">PRO DETAIL</span></div>', unsafe_allow_html=True)
    options=[ticker(x) for x in c.market.dropna().astype(str).tolist()[:100]]
    if not options:
        st.markdown('<div class="empty">분석 후보가 없습니다.</div>', unsafe_allow_html=True); return
    selected=st.selectbox('분석할 코인', options)
    usage=detail_usage(selected)
    render_detail_usage_meter(usage)
    if not usage.get('allowed',True):
        render_detail_limit_gate(usage, selected)
        return
    watched=selected in [ticker(x) for x in load_watchlist()]
    wb1,wb2=st.columns([1,5])
    with wb1:
        label='⭐ 관심 해제' if watched else '☆ 관심 코인 추가'
        if st.button(label, key=f'watch_toggle_{selected}', use_container_width=True):
            toggle_watch(selected); st.rerun()
    with wb2:
        st.caption('관심 코인은 별도 탭에서 현재 등락과 관심도 변화를 추적할 수 있습니다.')
    r=c[c.market.astype(str).map(ticker)==selected].iloc[0]

    score=safe_float(r.get('score')); rate=safe_float(r.get('pattern_rate')); obs=safe_int(r.get('pattern_obs'))
    src=ticker(r.get('best_source','')); sr=safe_float(r.get('source_ret')); cur=safe_float(r.get('ret1'))
    live=safe_float(r.get('live_ret'), cur); price=r.get('live_price', None)
    avg_next=safe_float(r.get('pattern_avg_ret')); pair=safe_float(r.get('pair_score'))
    tech=safe_float(r.get('technical_score')); bn=safe_int(r.get('bn_lead')); size=safe_float(r.get('size_pct'))
    same_sector=safe_float(r.get('same_sector')) if 'same_sector' in r.index else None
    same_band=safe_float(r.get('same_band')) if 'same_band' in r.index else None

    price_text=f'{safe_float(price):,.0f}원' if price is not None and not pd.isna(price) else '현재가 미수신'
    live_cls='red' if live>0 else 'blue' if live<0 else ''
    bn_class='red' if bn else ''
    bn_text='Binance 선행 감지' if bn else 'Binance 선행 없음'
    st.markdown(f"""<div class="detail"><div style="display:flex;justify-content:space-between;gap:20px;align-items:start"><div><div style="color:#7fa5c8;font-size:12px">다음 관심 코인</div><h3 style="font-size:28px;margin-top:5px">🔵 {selected}</h3><div style="margin-top:7px;color:#90a9c1">현재가 {price_text} · <b>현재 {live:+.1%}</b></div><div style="margin-top:6px;color:#7fa5c8;font-size:12px">과거에 먼저 오른 코인 → 다음날 {selected}가 어떻게 움직였는지 분석합니다.</div></div><div style="text-align:right"><div style="color:#7fa5c8;font-size:12px">상대 관심 점수</div><div class="bigscore">{score:.1f}</div></div></div><div class="pills"><span class="pill blue">과거 다음날 A→B {rate:.1%}</span><span class="pill">관찰 {obs}회</span><span class="pill {bn_class}">{bn_text}</span><span class="pill green">과거 대표 선행 · {src or '없음'}</span></div><div style="margin-top:14px;padding:14px 16px;border:1px solid #245070;border-radius:14px;background:#0b2136"><div style="font-size:12px;color:#7fa5c8">🕒 과거 대표 선행 흐름</div><div style="margin-top:6px;font-size:20px;font-weight:700;color:#fff">🔴 {src or '선행 코인 없음'} <span style="color:#6f95b3;margin:0 7px">→</span> 🔵 {selected}</div><div style="margin-top:7px;color:#9bb3c7;font-size:13px">{src or '이 선행 코인'}이 강하게 움직인 뒤 다음날 {selected}가 상승한 과거 패턴을 기준으로 대표 선행 코인을 표시합니다.</div><div style="margin-top:9px;color:#d9e7f2;font-size:13px">※ 여기의 ‘대표 선행’은 <b>오늘 가장 많이 오른 코인</b>이라는 뜻이 아니라, 과거 A→B 패턴에서 현재 {selected}와의 연결 점수가 가장 높은 코인입니다.</div></div></div>""", unsafe_allow_html=True)

    vals=[('현재 등락',f'{live:+.1%}','현재 시세 스냅샷'),('과거 다음날 상승률',f'{rate:.1%}',f'{obs}회 관찰'),('평균 다음날 수익률',f'{avg_next:+.2%}','과거 관찰 평균'),('선행 당시 상승률',f'{sr:+.1%}',source_label(sr))]
    html='<div class="pro-grid">'
    for k,v,sub in vals: html+=f'<div class="pro-card"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{sub}</div></div>'
    html+='</div>'; st.markdown(html,unsafe_allow_html=True)

    left,right=st.columns([1.15,1])
    with left:
        st.markdown('<div class="signal-box" style="margin-top:14px"><h4>📐 관심 점수 구성</h4>',unsafe_allow_html=True)
        components=[('A→B 선행',min(100,max(0,50+pair/2)),55),('기술 조건',min(100,max(0,tech)),20),('패턴 상승률',min(100,max(0,rate*100)),15),('Binance 선행',75 if bn else 50,10)]
        for label,val,weight in components:
            st.markdown(f'<div class="barrow"><span>{label}</span><div class="barbg"><div class="barfill" style="width:{val:.1f}%"></div></div><span>{weight}%</span></div>',unsafe_allow_html=True)
        st.markdown('</div>',unsafe_allow_html=True)
    with right:
        st.markdown('<div class="signal-box" style="margin-top:14px"><h4>🧩 패턴 맥락</h4>',unsafe_allow_html=True)
        context=[]
        if same_sector is not None: context.append(('동일 섹터 비중',f'{same_sector:.0%}'))
        if same_band is not None: context.append(('동일 가격대 비중',f'{same_band:.0%}'))
        context += [('시장 현재 등락',f'{cur:+.1%}'),('Binance 선행','감지' if bn else '미감지'),('시가 규모 백분위',f'{size:.0%}' if size else 'N/A')]
        for k,v in context: st.markdown(f'<div class="recent-row"><span>{k}</span><b>{v}</b></div>',unsafe_allow_html=True)
        st.markdown('</div>',unsafe_allow_html=True)

    # Historical evidence: show the actual observed source -> target episodes behind the score.
    st.markdown('<div class="section-title" style="margin-top:22px"><div><h2>🧾 패턴 발생 이력</h2><p>먼저 오른 코인 → 다음날 이 코인이 어떻게 움직였는지 보여주는 실제 과거 사례입니다.</p></div><span class="section-badge">EVIDENCE</span></div>', unsafe_allow_html=True)
    pair_occ = occ[(occ.source.astype(str)==str(r.get('best_source',''))) & (occ.target.astype(str)==str(r.get('market','')))].copy()
    if pair_occ.empty:
        st.markdown('<div class="empty">이 후보의 상세 발생 이력을 찾을 수 없습니다.</div>', unsafe_allow_html=True)
    else:
        pair_occ['source_date'] = pd.to_datetime(pair_occ['source_date'], errors='coerce')
        pair_occ = pair_occ.sort_values('source_date', ascending=False)
        recent = pair_occ.head(10).copy()
        recent_up = float(recent.target_up.mean()) if len(recent) else 0
        recent_avg = float(recent.target_next_ret.mean()) if len(recent) else 0
        m1,m2,m3 = st.columns(3)
        m1.metric('전체 관찰', f'{len(pair_occ):,}회')
        m2.metric('최근 10회 상승', f'{recent_up:.1%}')
        m3.metric('최근 10회 평균', f'{recent_avg:+.2%}')
        view = recent[['source_date','source','source_ret','target_next_ret','target_up']].copy()
        view['source_date'] = view['source_date'].dt.strftime('%Y-%m-%d')
        view['source_ret'] = view['source_ret'].map(lambda x: f'{safe_float(x):+.1%}')
        view['target_next_ret'] = view['target_next_ret'].map(lambda x: f'{safe_float(x):+.2%}')
        view['target_up'] = view['target_up'].map(lambda x: '상승' if int(x)==1 else '하락')
        view['source'] = view['source'].map(ticker)
        cards='<div class="pattern-history-grid">'
        for _,rr in view.iterrows():
            result=str(rr['target_up']); result_cls='up' if result=='상승' else 'down'
            target_symbol=ticker(r.get('market',''))
            cards += f'''<div class="pattern-history-card">
<div class="ph-head"><span class="ph-date">{rr["source_date"]}</span><span class="ph-result {result_cls}">{result}</span></div>
<div class="ph-flow">
  <div class="ph-coin"><span>🔴 먼저 움직인 코인</span><b>{rr["source"]} {rr["source_ret"]}</b></div>
  <div class="ph-arrow">→</div>
  <div class="ph-coin ph-target"><span>🔵 다음날 {target_symbol}</span><b class="{result_cls}">{rr["target_next_ret"]}</b></div>
</div></div>'''
        cards+='</div>'
        st.markdown(cards,unsafe_allow_html=True)
        st.caption('※ 실제 A→B 과거 관찰 사례입니다. 과거 사례가 미래 결과를 보장하지 않습니다.')

    # v33: concrete PRO value preview using the currently selected coin's real evidence.
    st.markdown('### 💎 PRO 기능 미리보기')
    st.caption('잠금은 유지하면서, 실제 선택 코인에서 PRO가 무엇을 더 보여주는지 일부만 미리 확인합니다.')
    pb1,pb2,pb3=st.columns(3)
    with pb1:
        if st.button('🧾 전체 이력 미리보기',use_container_width=True,key=f'pro_prev_hist_{selected}'):
            track_pro_feature_interest('전체 패턴 이력',selected)
            st.session_state[f'pro_prev_hist_show_{selected}']=True
    with pb2:
        if st.button('🔗 Flow 미리보기',use_container_width=True,key=f'pro_prev_flow_{selected}'):
            track_pro_feature_interest('Flow 타임라인',selected)
            st.session_state[f'pro_prev_flow_show_{selected}']=True
    with pb3:
        if st.button('📊 백테스트 미리보기',use_container_width=True,key=f'pro_prev_bt_{selected}'):
            track_pro_feature_interest('백테스트 세부',selected)
            st.session_state[f'pro_prev_bt_show_{selected}']=True
    if st.session_state.get(f'pro_prev_hist_show_{selected}',False):
        preview=pair_occ[['source_date','source_ret','target_next_ret','target_up']].head(3).copy() if 'pair_occ' in locals() and not pair_occ.empty else pd.DataFrame()
        if not preview.empty:
            preview['source_date']=preview['source_date'].dt.strftime('%Y-%m-%d')
            preview['source_ret']=preview['source_ret'].map(lambda x:f'{safe_float(x):+.1%}')
            preview['target_next_ret']=preview['target_next_ret'].map(lambda x:f'{safe_float(x):+.2%}')
            preview['target_up']=preview['target_up'].map(lambda x:'상승' if int(x)==1 else '하락')
            preview.columns=['발생일','선행 상승률','다음날 수익률','결과']
            st.dataframe(preview,use_container_width=True,hide_index=True)
        st.caption('전체 이력과 기간 필터는 PRO에서 제공할 예정입니다. 현재는 예시 3건만 표시합니다.')
    if st.session_state.get(f'pro_prev_flow_show_{selected}',False):
        st.info(f'🔗 {src or "선행 코인"} → {selected}의 날짜별 연결 흐름과 대상 수 변화가 PRO에서 제공됩니다.')
    if st.session_state.get(f'pro_prev_bt_show_{selected}',False):
        if bt_path:= (OUT/'backtest_final.csv'):
            if bt_path.exists():
                btp=pd.read_csv(bt_path)
                st.dataframe(btp.head(1),use_container_width=True,hide_index=True)
        st.caption('백테스트 전체 세부와 기간별 분석은 PRO에서 제공할 예정입니다.')

    st.markdown('<div class="pro-lock"><b>🔒 PRO — 더 깊은 패턴 증거</b><p>현재는 최근 발생 이력을 확인할 수 있고, 유료 버전에서는 전체 발생 이력·기간별 필터·상세 Flow 타임라인·백테스트 세부·관심 코인 알림까지 확장할 수 있습니다.</p><span class="mini-pill">전체 패턴 이력</span><span class="mini-pill">기간별 필터</span><span class="mini-pill">상세 Flow 타임라인</span><span class="mini-pill">백테스트 세부</span><span class="mini-pill">관심 코인 알림</span></div>',unsafe_allow_html=True)
    st.caption('※ 이 화면의 상승률·관찰 횟수·점수는 과거 데이터와 현재 시세를 정리한 정보이며 미래 수익을 보장하지 않습니다.')

def render_pro_full(c, occ, bt):
    """Full PRO implementation. Kept behind a launch gate until payment is ready."""
    st.markdown("<div class=\"section-title\"><div><h2>💎 Coin Pattern PRO</h2><p>전체 패턴 증거와 Flow를 실제 데이터로 탐색합니다.</p></div><span class=\"section-badge\">PRO</span></div>", unsafe_allow_html=True)
    if occ is None or occ.empty:
        st.warning("패턴 발생 이력 데이터가 없습니다. 먼저 최신 분석을 실행해주세요.")
        return
    occ=occ.copy(); occ["source_date"]=pd.to_datetime(occ["source_date"],errors="coerce"); occ=occ.dropna(subset=["source_date"])
    periods={"최근 7일":7,"최근 30일":30,"최근 90일":90,"전체":None}
    a,b,d=st.columns(3)
    with a: period=st.selectbox("기간",list(periods.keys()),index=2)
    with b: source_sel=st.selectbox("선행 코인",["전체"]+sorted(occ.source.dropna().astype(str).map(ticker).unique().tolist()))
    with d: target_sel=st.selectbox("대상 코인",["전체"]+sorted(occ.target.dropna().astype(str).map(ticker).unique().tolist()))
    f=occ.copy(); days=periods[period]
    if days is not None: f=f[f.source_date >= pd.Timestamp.now().normalize()-pd.Timedelta(days=days)]
    if source_sel!="전체": f=f[f.source.astype(str).map(ticker)==source_sel]
    if target_sel!="전체": f=f[f.target.astype(str).map(ticker)==target_sel]
    m1,m2,m3,m4=st.columns(4)
    m1.metric("관찰 사례",f"{len(f):,}회"); m2.metric("상승 비율",f"{float(f.target_up.mean()):.1%}" if len(f) else "-"); m3.metric("평균 다음날",f"{float(f.target_next_ret.mean()):+.2%}" if len(f) else "-"); m4.metric("중앙값 다음날",f"{float(f.target_next_ret.median()):+.2%}" if len(f) else "-")
    st.subheader("🧾 전체 패턴 발생 이력")
    if not f.empty:
        v=f.sort_values("source_date",ascending=False).copy(); v["발생일"]=v.source_date.dt.strftime("%Y-%m-%d"); v["선행"]=v.source.map(ticker); v["대상"]=v.target.map(ticker); v["선행 상승률"]=v.source_ret.map(lambda x:f"{safe_float(x):+.1%}"); v["다음날 수익률"]=v.target_next_ret.map(lambda x:f"{safe_float(x):+.2%}"); v["결과"]=v.target_up.map(lambda x:"상승" if int(x)==1 else "하락")
        keep=["발생일","선행","대상","선행 상승률","다음날 수익률","결과"]
        st.dataframe(v[keep].head(500),use_container_width=True,hide_index=True,height=450)
    else: st.info("선택한 조건에 맞는 사례가 없습니다.")
    st.subheader("🔗 Flow 타임라인")
    if not f.empty:
        flow=f.groupby(["source_date","source"]).agg(대상수=("target","nunique"),평균다음날=("target_next_ret","mean"),상승비율=("target_up","mean")).reset_index().sort_values("source_date",ascending=False)
        flow["발생일"]=flow.source_date.dt.strftime("%Y-%m-%d"); flow["선행"]=flow.source.map(ticker); flow["평균다음날"]=flow.평균다음날.map(lambda x:f"{x:+.2%}"); flow["상승비율"]=flow.상승비율.map(lambda x:f"{x:.1%}")
        st.dataframe(flow[["발생일","선행","대상수","평균다음날","상승비율"]].head(300),use_container_width=True,hide_index=True,height=340)
    st.subheader("📊 백테스트 세부")
    if bt is not None and not bt.empty: st.dataframe(bt,use_container_width=True,hide_index=True)
    else: st.info("백테스트 세부 결과가 없습니다.")
    st.caption("※ PRO 분석은 과거 관찰 데이터를 탐색하는 정보 기능이며 미래 가격이나 수익을 보장하지 않습니다.")

def render_pro(c, occ, bt):
    if effective_pro_unlocked():
        render_pro_full(c, occ, bt); return
    st.markdown("<div class=\"section-title\"><div><h2>💎 Coin Pattern PRO</h2><p>결제 시스템 연결 전까지 모든 PRO 기능은 잠금 상태로 유지합니다.</p></div><span class=\"section-badge\">PRO LOCKED</span></div>", unsafe_allow_html=True)
    st.markdown("<div class=\"pro-hero\"><div style=\"color:#8fcaff;font-size:12px;font-weight:800\">COIN PATTERN PRO</div><h2>왜 이 코인이 다음 관심인가를 끝까지 확인</h2><p>전체 패턴 이력 · 기간별 필터 · 상세 Flow 타임라인 · 백테스트 세부를 실제 데이터로 사용할 수 있도록 준비했습니다.</p><div class=\"pro-price\">🔒 출시 준비 중</div><div style=\"margin-top:12px;color:#718eab;font-size:12px\">현재는 결제·구독 기능이 연결되지 않았습니다. 출시 전에는 잠금만 해제하면 됩니다.</div></div>", unsafe_allow_html=True)
    st.markdown("<div class=\"pro-features\"><div class=\"pro-feature\"><b>🔒 전체 패턴 증거</b><span>실제 발생 이력과 기간별 결과</span></div><div class=\"pro-feature\"><b>🔒 상세 Flow 타임라인</b><span>날짜별 시장 흐름과 반복 연결</span></div><div class=\"pro-feature\"><b>🔒 백테스트 세부</b><span>관찰 수와 성과 지표</span></div><div class=\"pro-feature\"><b>🔒 전체 패턴 증거</b><span>발생 이력과 기간별 결과</span></div><div class=\"pro-feature\"><b>🔒 기간별 필터</b><span>7일·30일·90일·전체</span></div><div class=\"pro-feature\"><b>🔒 관심 코인 알림</b><span>출시 후 별도 연결</span></div></div>", unsafe_allow_html=True)
    if not st.session_state.get('_cp_pro_view_tracked', False):
        track('pro_view')
        st.session_state['_cp_pro_view_tracked'] = True
    st.info("현재 PRO는 무료 체험/결제 대상이 아닙니다. 먼저 실제 사용성과 반응을 확인한 뒤 결제 시스템을 연결합니다.")
    st.markdown("### 👀 PRO에 대한 현재 반응")
    st.caption("PRO 결제는 아직 연결되지 않았습니다. 베타에서는 기능 반응만 기록합니다.")
    a,b,c=st.columns(3)
    with a:
        if st.button("👍 꼭 필요함", use_container_width=True, key='pro_need'):
            track('pro_interest','꼭 필요함'); st.success('반응을 기록했습니다.')
    with b:
        if st.button("🤔 있으면 좋겠음", use_container_width=True, key='pro_maybe'):
            track('pro_interest','있으면 좋겠음'); st.success('반응을 기록했습니다.')
    with c:
        if st.button("🧩 아직 부족함", use_container_width=True, key='pro_lack'):
            track('pro_interest','아직 부족함'); st.success('반응을 기록했습니다.')

    st.markdown('### 🚪 무료 → PRO 전환 구조 미리보기')
    st.caption('지금 결제하는 단계가 아니라, 어떤 추가 정보에 비용을 지불할 의향이 있는지 확인하는 사전 검증입니다.')
    f1,f2=st.columns(2)
    with f1:
        st.markdown("<div class='lock-card'><h4>🆓 현재 무료</h4><p>오늘 TOP 5 · 기본 Flow · 선택 코인의 핵심 A→B 근거 · 최근 사례</p></div>",unsafe_allow_html=True)
    with f2:
        st.markdown("<div class='pro-lock'><h4>💎 PRO</h4><p>전체 발생 이력 · 기간별 필터 · 상세 Flow · 백테스트 세부 · 관심 코인 확장 기능</p></div>",unsafe_allow_html=True)
    st.markdown('**출시된다면 어떤 의향인가요?**')
    i1,i2,i3=st.columns(3)
    with i1:
        if st.button('💎 사용하겠다',use_container_width=True,key='pro_intent_yes'):
            track_pro_intent('사용 의향 높음'); st.success('의향을 기록했습니다. 결제는 진행되지 않습니다.')
    with i2:
        if st.button('👀 먼저 더 써보겠다',use_container_width=True,key='pro_intent_try'):
            track_pro_intent('추가 사용 후 판단'); st.success('의향을 기록했습니다.')
    with i3:
        if st.button('❌ 아직 필요 없다',use_container_width=True,key='pro_intent_no'):
            track_pro_intent('현재 불필요'); st.success('의향을 기록했습니다.')
    days,sessions,pro=local_usage_summary()
    st.markdown(f"<div class='pro-lock'><b>현재 사용 기록</b><p>이 기기에서 사용한 날짜 {days}일 · 세션 {sessions}회 · PRO 관련 반응 {pro}회</p><span class='mini-pill'>결제 없음</span><span class='mini-pill'>PRO 결제 없음</span><span class='mini-pill'>익명 로컬 기록</span></div>", unsafe_allow_html=True)
    st.caption("※ PRO는 미래 가격이나 수익을 보장하는 기능이 아니라 과거 시장 데이터를 더 깊게 탐색하는 분석·정보 기능입니다.")

def render_server_account():
    st.markdown('### 👤 서버 회원 계정 (v45 개발 테스트)')
    st.caption('베타에서는 회원가입·로그인 흐름을 검증합니다. 실제 결제와 외부 공개는 출시 단계에서 연결합니다.')
    cs=entitlement_contract_status()
    a,b,c=st.columns(3); a.metric('서버', '연결 설정됨' if cs['base_url_configured'] else '미설정'); b.metric('로그인','로그인됨' if cs['logged_in'] else '로그아웃'); c.metric('권한', 'PRO' if server_entitlement() and server_entitlement().get('pro') else 'FREE')
    if not cs['base_url_configured']:
        st.info('설정: server_config.json의 base_url에 http://127.0.0.1:8787 을 입력한 뒤 개발 서버를 실행하세요.')
        return
    if cs['logged_in']:
        me=server_request('/v1/me'); sub=server_request('/v1/subscription'); ent=server_entitlement()
        if me: st.write(f"회원: **{me.get('email','-')}** · ID: `{me.get('id','-')}` · 역할: `{me.get('role','user')}`")
        render_admin_console(me)
        if sub: st.write(f"구독: **{sub.get('plan','FREE')}** · 상태: **{sub.get('status','-')}**")
        if ent: st.write(f"PRO 권한: **{'활성' if ent.get('pro') else '잠금'}**")
        if st.button('로그아웃',key='server_logout'):
            server_request('/v1/logout','POST'); save_auth_token(''); track('server_logout'); st.rerun()
        with st.expander('🔐 계정 및 개인정보 관리', expanded=False):
            st.caption('v44 개발 운영 기능입니다. 계정 삭제는 되돌릴 수 없으며 서버의 회원·세션·구독 데이터를 삭제합니다.')
            if st.button('📦 내 계정 데이터 조회',key='account_export'):
                r=server_request('/v1/account/export','POST')
                if r and r.get('account'):
                    st.json(r); track('account_export')
                else: st.error('계정 데이터 조회 실패')
            sr=server_request('/v1/account/sessions','POST')
            if sr and sr.get('sessions'):
                st.write('현재 로그인 세션')
                for ss in sr['sessions']:
                    label=('현재 기기' if ss.get('current') else '다른 세션')+f" · {ss.get('created_at','-')} · 만료 {ss.get('expires_at','-')}"
                    if ss.get('revoked'): label+=' · 폐기됨'
                    st.caption(label)
                    if not ss.get('revoked') and not ss.get('current'):
                        if st.button('세션 폐기',key='revoke_'+ss['session_id']):
                            rr=server_request('/v1/account/sessions/revoke','POST',{'session_id':ss['session_id']})
                            if rr and rr.get('ok'): track('session_revoked'); st.success('세션을 폐기했습니다.'); st.rerun()
            st.markdown('**계정 삭제**')
            del_pw=st.text_input('현재 비밀번호 확인',type='password',key='delete_account_pw')
            if st.button('⚠️ 계정 영구 삭제',key='delete_account',type='secondary'):
                if not del_pw: st.warning('현재 비밀번호를 입력하세요.')
                else:
                    rr=server_request('/v1/account/delete','POST',{'password':del_pw})
                    if rr and rr.get('deleted'):
                        save_auth_token(''); track('account_deleted'); st.success('계정을 삭제했습니다.'); st.rerun()
                    else: st.error('계정 삭제 실패: '+str((rr or {}).get('error','server error')))
        st.markdown('#### 🧪 개발용 구독 시뮬레이션 (결제 아님)')
        st.caption('v41~v44 전용 로컬 개발 테스트입니다. 실제 결제·실제 PRO 판매와 분리되어 있으며, 내부적으로 webhook 상태 전이 경로를 검증합니다.')
        dc1,dc2,dc3=st.columns(3)
        with dc1:
            sim_days=st.number_input('기간(일)',min_value=1,max_value=3650,value=7,step=1,key='dev_sub_days')
        with dc2:
            if st.button('💎 PRO 활성화',use_container_width=True,key='dev_sub_activate'):
                r=server_request('/v1/dev/subscription','POST',{'action':'activate','days':int(sim_days)})
                if r and r.get('ok'): track('dev_subscription_activate',str(sim_days)); st.success('개발용 PRO를 활성화했습니다.'); st.rerun()
                else: st.error('개발용 권한 변경 실패')
        with dc3:
            if st.button('🧪 PRO 체험(trial)',use_container_width=True,key='dev_sub_trial'):
                r=server_request('/v1/dev/subscription','POST',{'action':'trial','days':int(sim_days)})
                if r and r.get('ok'): track('dev_subscription_trial',str(sim_days)); st.success('개발용 trial PRO를 활성화했습니다.'); st.rerun()
                else: st.error('개발용 trial 변경 실패')
        dc4,dc5=st.columns(2)
        with dc4:
            if st.button('⏪ PRO 회수',use_container_width=True,key='dev_sub_revoke'):
                r=server_request('/v1/dev/subscription','POST',{'action':'revoke'})
                if r and r.get('ok'): track('dev_subscription_revoke'); st.success('PRO를 FREE로 회수했습니다.'); st.rerun()
                else: st.error('개발용 권한 회수 실패')
        with dc5:
            if st.button('⏱️ 즉시 만료 테스트',use_container_width=True,key='dev_sub_expire'):
                r=server_request('/v1/dev/subscription','POST',{'action':'expire'})
                if r and r.get('ok'): track('dev_subscription_expire'); st.success('만료 상태로 변경했습니다.'); st.rerun()
                else: st.error('개발용 만료 처리 실패')
        st.caption('⚠️ 이 영역은 로컬 개발 서버의 X-CoinPattern-Dev-Key 검증이 필요한 테스트 전용 API입니다. 운영 서버에는 노출하면 안 됩니다.')
        st.markdown('#### 🔁 Webhook 상태 전이 시뮬레이터 (v41)')
        st.caption('아래 이벤트는 실제 결제가 아니라, 실제 webhook 처리 경로와 동일한 상태 머신을 시험합니다.')
        event_labels={'subscription.created':'생성 → active','subscription.updated':'갱신 → active','subscription.renewed':'갱신 → active','subscription.trialing':'체험 → trialing','subscription.past_due':'결제 실패 → past_due','subscription.canceled':'기간 종료 예약 → canceled','subscription.expired':'만료 → expired','subscription.revoked':'회수 → expired'}
        ev=st.selectbox('테스트 이벤트',list(event_labels.keys()),format_func=lambda x:event_labels[x],key='v41_webhook_event')
        if st.button('🔁 webhook 이벤트 보내기',use_container_width=True,key='v41_webhook_send'):
            r=server_request('/v1/dev/webhook','POST',{'type':ev,'days':int(sim_days)})
            if r and r.get('ok'): track('dev_webhook_simulation',ev); st.success(f'상태 전이 완료: {ev}'); st.rerun()
            else: st.error(f'webhook 시뮬레이션 실패: {r.get("error","unknown") if r else "server error"}')

    else:
        tab1,tab2=st.tabs(['로그인','회원가입'])
        with tab1:
            e=st.text_input('이메일',key='srv_login_email'); pw=st.text_input('비밀번호',type='password',key='srv_login_pw')
            if st.button('로그인',key='srv_login',type='primary'):
                try:
                    base=str(SERVER_CONFIG.get('base_url','')).rstrip('/'); rr=requests.post(base+'/v1/login',json={'email':e,'password':pw},timeout=4)
                    data=rr.json()
                    if rr.ok and data.get('access_token'):
                        save_auth_token(data['access_token']); track('server_login'); st.success('로그인 성공'); st.rerun()
                    else: st.error(data.get('error','로그인 실패'))
                except Exception as ex: st.error(f'서버 연결 실패: {ex}')
        with tab2:
            e2=st.text_input('가입 이메일',key='srv_reg_email'); pw2=st.text_input('비밀번호(10자 이상)',type='password',key='srv_reg_pw')
            if st.button('회원가입',key='srv_register',type='primary'):
                try:
                    base=str(SERVER_CONFIG.get('base_url','')).rstrip('/'); rr=requests.post(base+'/v1/register',json={'email':e2,'password':pw2},timeout=4); data=rr.json()
                    if rr.ok and data.get('access_token'):
                        save_auth_token(data['access_token']); track('server_register'); st.success('회원가입 및 로그인 성공'); st.rerun()
                    else: st.error(data.get('error','회원가입 실패'))
                except Exception as ex: st.error(f'서버 연결 실패: {ex}')

def main():
    if not st.session_state.get('_cp_session_tracked', False):
        track('session_start')
        st.session_state['_cp_session_tracked'] = True
    data = load_data()
    meta = data[4] if data else {}
    render_header(meta)
    if data is None:
        render_start(); return
    c,p,occ,bt,meta = data
    if 'best_source' not in c.columns or 'market' not in c.columns:
        st.error('후보 결과 형식이 현재 UI와 맞지 않습니다. engine_final.py 결과를 확인해주세요.'); return
    c=c[c.best_source.fillna('').astype(str).ne('KRW-A')].copy()
    c=c.sort_values('score',ascending=False)
    c, live_ok = merge_live(c)
    try:
        _live=load_live_tickers()
        if not _live.empty:
            keep=[x for x in ['market','trade_price','signed_change_rate','acc_trade_price_24h','timestamp','opening_price','high_price','low_price'] if x in _live.columns]
            live_df=_live[keep].copy().rename(columns={'trade_price':'live_price','signed_change_rate':'live_ret','acc_trade_price_24h':'live_value_24h','timestamp':'live_timestamp'})
        else:
            live_df=pd.DataFrame()
    except Exception:
        live_df=pd.DataFrame()
    save_daily_snapshot(c)
    status_html = '<div class="status" style="padding:8px 14px;margin-bottom:10px;border-radius:12px;opacity:.92"><b>● 현재 시세 연결됨</b> · Upbit 현재가 스냅샷을 반영했습니다.</div>' if live_ok else '<div class="status" style="padding:10px 14px;margin-bottom:12px"><b>○ 현재 시세 연결 안 됨</b> · 저장된 분석 데이터로 표시합니다.</div>'
    st.markdown(status_html, unsafe_allow_html=True)
    tabs=st.tabs(['🏠 홈','🔗 시장 흐름','🏆 TOP 30','🔎 상세 분석','⭐ 관심 코인','💎 PRO','📚 분석 기록','⚙️ 설정'])
    with tabs[0]:
        if 'landing_seen' not in st.session_state:
            st.session_state['landing_seen']=False
        if not st.session_state['landing_seen']:
            render_landing()
        else:
            ob=onboarding_state()
            if (st.session_state.get('show_onboarding') or (not ob.get('completed') and not ob.get('dismissed'))):
                render_onboarding(c)
            # v66: Market Flow is the primary screen. Give users an explicit instant-refresh control
            # for the live Upbit snapshot without rerunning the expensive historical analysis.
            rf1, rf2 = st.columns([1, 5])
            with rf1:
                if st.button('🔄 시장 현황 새로고침', use_container_width=True, type='primary', key='market_live_refresh'):
                    try:
                        load_live_tickers.clear()
                    except Exception:
                        pass
                    try:
                        load_hourly_candles.clear()
                    except Exception:
                        pass
                    track('market_live_refresh')
                    st.rerun()
            with rf2:
                now_txt = datetime.now().strftime('%H:%M:%S')
                st.caption(f'현재 Upbit 시세를 즉시 다시 불러옵니다 · 마지막 화면 갱신 {now_txt} · 730일 분석 데이터는 다시 구축하지 않습니다.')
            render_flow(c, p, live_df, occ, max_leaders=4, targets_per_leader=3, compact=False)
            render_home_lower_panels(c, p, live_df)
    with tabs[1]:
        render_live_flow_fragment(p, occ)
        st.caption('※ 흐름은 과거 A→B 관찰 빈도를 기반으로 하며 미래 상승을 보장하지 않습니다.')
    with tabs[2]: render_top30(c, occ)
    with tabs[3]: render_detail(c, occ)
    with tabs[4]: render_watchlist(c)
    with tabs[5]: render_pro(c, occ, bt)
    with tabs[6]:
        render_analysis_records(meta, bt)
    with tabs[7]:
        st.markdown('<div class="section-title"><div><h2>⚙️ 실행 및 데이터</h2><p>분석 데이터는 로컬에 저장됩니다.</p></div></div>',unsafe_allow_html=True)
        days,sessions,pro=local_usage_summary()
        x,y,z=st.columns(3)
        x.metric('이 기기 사용일', f'{days}일')
        y.metric('세션', f'{sessions}회')
        z.metric('PRO 반응', f'{pro}회')
        st.caption('v21부터 결제 없이 익명 로컬 사용 이벤트를 기록합니다. 이 수치는 이 PC의 사용 기록이며 전체 사용자 통계를 의미하지 않습니다.')
        st.success('v58 · 분석 데이터는 앱 폴더와 분리되어 저장됩니다. 다음 버전으로 교체해도 730일 데이터를 다시 받지 않습니다.')
        render_retention_panel()
        pf=pro_feature_counts()
        if pf:
            st.markdown('### 💎 PRO 기능 관심도')
            st.caption('이 기기에서 실제로 눌러본 잠금 기능을 집계합니다. 전체 사용자 통계가 아닙니다.')
            st.write(' · '.join([f'{k}: {v}회' for k,v in sorted(pf.items(), key=lambda x:-x[1])]))
        pi=pro_intent_counts()
        if pi:
            st.markdown('### 🚪 PRO 사용 의향')
            st.caption('결제 의사가 아니라 출시 전 제품 수요를 확인하기 위한 로컬 기록입니다.')
            st.write(' · '.join([f'{k}: {v}회' for k,v in sorted(pi.items(), key=lambda x:-x[1])]))
        render_beta_feedback()
        with st.expander('🛠 출시 준비 / 개발자 영역', expanded=False):
            st.markdown('### 🌐 서버 구독 권한 연동 준비')
            st.caption('v39는 실제 결제 없이 개발 서버 권한 흐름을 검증합니다. 출시 시 서버가 회원·결제·PRO 권한의 최종 기준이 되도록 API 계약과 상태 모델만 준비합니다.')
            cs=entitlement_contract_status()
            q1,q2,q3,q4=st.columns(4)
            q1.metric('API 계약', cs['api_contract'])
            q2.metric('권한 기준', 'SERVER')
            q3.metric('결제', '미연결')
            q4.metric('서버 URL', '설정됨' if cs['base_url_configured'] else '미설정')
            st.code('GET /v1/me\nGET /v1/subscription\nGET /v1/entitlement\nPOST /v1/logout', language='text')
            if st.button('🧪 로컬 개발 서버 주소 설정',key='set_local_auth_server'):
                SERVER_CONFIG.update({'mode':'local_dev','base_url':'http://127.0.0.1:8787','environment':'development'})
                SERVER_CONFIG_FILE.write_text(json.dumps(SERVER_CONFIG,ensure_ascii=False,indent=2),encoding='utf-8'); st.success('로컬 개발 서버 주소를 설정했습니다. server_auth_v38.py를 먼저 실행하세요.'); st.rerun()
            st.caption('클라이언트는 서버가 발급한 access token으로 entitlement를 조회하고, 로컬 entitlement 파일을 유료 권한의 근거로 신뢰하지 않는 구조입니다.')
            render_server_account()
    
    
            st.markdown('### 🧪 PRO 권한 테스트 환경')
            st.caption('출시 전 개발 검증용입니다. 실제 결제·회원 인증과 연결되지 않으며, 이 PC에서만 PRO 권한 흐름을 시뮬레이션합니다.')
            ent=load_test_entitlement()
            active=effective_pro_unlocked()
            ec1,ec2,ec3=st.columns(3)
            ec1.metric('현재 플랜', 'PRO TEST' if active else 'FREE')
            ec2.metric('권한 상태', '활성' if active else '잠금')
            exp=ent.get('expires_at')
            ec3.metric('테스트 만료', exp[:16].replace('T',' ') if active and exp else '-')
            ea,eb,ec=st.columns(3)
            with ea:
                if st.button('🧪 PRO 7일 활성화',use_container_width=True,key='test_pro_7'):
                    activate_test_pro(7); st.success('로컬 테스트용 PRO를 7일 활성화했습니다.'); st.rerun()
            with eb:
                if st.button('⏪ PRO 즉시 회수',use_container_width=True,key='test_pro_revoke'):
                    revoke_test_pro(); st.success('로컬 테스트용 PRO 권한을 회수했습니다.'); st.rerun()
            with ec:
                if st.button('🔄 권한 상태 새로고침',use_container_width=True,key='test_pro_refresh'):
                    st.rerun()
            st.caption('⚠️ 이 테스트 권한은 결제 성공을 의미하지 않습니다. 출시 시에는 서버가 결제 상태를 검증하고 권한을 결정해야 합니다.')
            st.info('최초 구축은 run_first_bootstrap.bat, 최신 갱신은 run_refresh.bat를 사용할 수 있습니다.')
        if st.button('🔄 최신 데이터 갱신',type='primary',use_container_width=True):
            env=os.environ.copy(); env['COIN_PATTERN_MODE']='refresh'; env['PYTHONUNBUFFERED']='1'
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            subprocess.Popen([sys.executable,str(ROOT/'engine_final.py')],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags,close_fds=(os.name!='nt'))
            st.success('최신 데이터 갱신을 시작했습니다. 잠시 후 새로고침하세요.')
        st.markdown('### 👤 사용자 프로필 준비')
        st.caption('현재는 실제 회원가입/로그인이 아니라, 향후 서버 회원 시스템으로 전환하기 위한 로컬 프로필입니다.')
        pc1,pc2=st.columns([2,1])
        with pc1:
            nickname=st.text_input('표시 이름(선택)',value=PROFILE.get('nickname',''),max_chars=30,key='profile_nickname')
        with pc2:
            st.metric('내 사용자 ID', PROFILE.get('member_id','-'))
        if st.button('프로필 저장',use_container_width=True):
            save_profile(nickname); st.success('프로필 정보를 저장했습니다.'); st.rerun()
        st.info('회원가입·로그인은 베타 계정 서버에 연결되어 있습니다. 결제와 실제 운영 서버는 출시 단계에서 연결합니다.')
        st.caption(f'분석 데이터 위치: {APP_HOME / "data"}')
        st.caption('데이터: Upbit KRW + Binance Spot USDT · 운영 신호는 A→B 전환 패턴 · 자기연속은 검증용')
        st.caption('📱 모바일 웹: 같은 주소를 휴대폰 브라우저에서 열어도 화면 크기에 맞춰 자동으로 재배치됩니다.')

if __name__ == '__main__': main()
