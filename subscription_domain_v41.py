"""Provider-neutral subscription state machine for Coin Pattern v41.
No payment provider SDK is included here.
"""
from datetime import datetime, timezone

def _now_iso(): return datetime.now(timezone.utc).isoformat()

VALID_STATUSES={"active","trialing","past_due","canceled","expired"}
EVENTS={
 "subscription.created","subscription.updated","subscription.renewed","subscription.trialing",
 "subscription.past_due","subscription.canceled","subscription.expired","subscription.revoked"
}

def normalize_event(event_type,data):
    if event_type not in EVENTS: raise ValueError("unsupported event type")
    status=str(data.get("status") or "active")
    if event_type=="subscription.trialing": status="trialing"
    if event_type=="subscription.past_due": status="past_due"
    if event_type=="subscription.canceled": status="canceled"
    if event_type in ("subscription.expired","subscription.revoked"): status="expired"
    if status not in VALID_STATUSES: raise ValueError("invalid status")
    return {
      "type":event_type,"status":status,
      "current_period_start":data.get("current_period_start"),
      "current_period_end":data.get("current_period_end"),
      "cancel_at_period_end":bool(data.get("cancel_at_period_end",False)),
      "provider":data.get("provider"),
      "provider_customer_id":data.get("provider_customer_id"),
      "provider_subscription_id":data.get("provider_subscription_id"),
    }

def apply_subscription_event(current,event):
    cur_status=str(current.get("status") or "active")
    typ=event["type"]
    # Terminal state cannot be silently resurrected by a stale update.
    if cur_status=="expired" and typ in {"subscription.updated","subscription.created","subscription.trialing"}:
        raise ValueError("expired subscription requires a new subscription/renewal event")
    status=event["status"]
    if typ=="subscription.canceled" and not event.get("current_period_end"):
        status="canceled"
    plan="PRO" if status in {"active","trialing","past_due","canceled"} else "FREE"
    # Canceled-at-period-end retains PRO until the recorded end.
    if status=="canceled" and event.get("current_period_end"):
        plan="PRO"
    return {
      "plan":plan,"status":status,
      "current_period_start":event.get("current_period_start") or current.get("current_period_start"),
      "current_period_end":event.get("current_period_end") if event.get("current_period_end") is not None else current.get("current_period_end"),
      "cancel_at_period_end":bool(event.get("cancel_at_period_end", status=="canceled")),
      "provider":event.get("provider") or current.get("provider"),
      "provider_customer_id":event.get("provider_customer_id") or current.get("provider_customer_id"),
      "provider_subscription_id":event.get("provider_subscription_id") or current.get("provider_subscription_id"),
    }

def entitlement_from_subscription(s):
    if not s: return {"pro":False,"status":"free","expires_at":None}
    status=str(s.get("status") or "expired")
    pro=status in {"active","trialing","past_due","canceled"} and str(s.get("plan"))=="PRO"
    end=s.get("current_period_end")
    if pro and end:
        try: pro=datetime.fromisoformat(str(end))>datetime.now(timezone.utc)
        except Exception: pro=False
    if status=="expired": pro=False
    return {"pro":pro,"status":status,"expires_at":end}
