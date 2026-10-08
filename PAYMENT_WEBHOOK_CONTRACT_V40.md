# Coin Pattern v40 — 결제 Webhook 계약 준비

## 목적
실제 결제 공급자를 연결하기 전에 서버가 결제 이벤트를 안전하게 받아 구독 상태로 변환하는 경계를 고정합니다.

## 원칙
1. 결제 성공 화면은 PRO 권한의 근거가 아닙니다.
2. 서버는 서명 검증이 끝난 webhook만 처리합니다.
3. `event_id`를 저장해 동일 webhook이 여러 번 도착해도 한 번만 반영합니다.
4. 외부 공급자의 상태를 Coin Pattern 내부 상태(`active`, `trialing`, `past_due`, `canceled`, `expired`)로 변환합니다.
5. 클라이언트는 webhook을 직접 호출하거나 PRO 권한을 직접 변경하지 않습니다.
6. 현재 v40은 실제 결제 공급자를 연결하지 않으며 개발용 secret만 사용합니다.

## Endpoint
`POST /v1/webhooks/payment`

Headers:
- `X-CoinPattern-Webhook-Signature: HMAC-SHA256`

Payload 핵심 필드:
- `event_id`
- `type`
- `data.user_id`
- `data.status`
- `data.current_period_end`
- `data.provider`
- `data.provider_customer_id`
- `data.provider_subscription_id`

지원 이벤트:
- `subscription.created`
- `subscription.updated`
- `subscription.renewed`
- `subscription.trialing`
- `subscription.canceled`
- `subscription.expired`
- `subscription.revoked`

## 출시 전 필수 보완
- 운영용 webhook secret은 환경변수/secret manager에 저장
- HTTPS 강제
- 공급자 공식 SDK/서명 규격으로 검증
- replay 방지(이벤트 timestamp 등)
- 운영 DB 및 transaction 정책
- 결제/환불/해지 정책과 개인정보·약관 검토
