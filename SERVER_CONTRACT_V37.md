# Coin Pattern v37 — 서버 구독 권한 계약

## 목적
실제 결제 연결 전에 회원·구독·PRO 권한의 책임 경계를 고정합니다.

## 원칙
1. 서버가 회원/구독/PRO entitlement의 최종 기준입니다.
2. 클라이언트의 `entitlement_test.json`, `profile.json` 등 로컬 파일은 유료 권한의 근거가 아닙니다.
3. 결제 성공은 결제 페이지가 아니라 서버가 검증한 webhook/결제 조회 결과로 확정합니다.
4. 앱은 `GET /v1/entitlement` 결과로 PRO 화면을 열어야 합니다.
5. access token/secret key를 소스코드나 로컬 설정에 하드코딩하지 않습니다.

## API v1
- `GET /v1/me` — 로그인 회원 기본 정보
- `GET /v1/subscription` — 현재 구독 상태
- `GET /v1/entitlement` — PRO 사용 권한의 최종 결과
- `POST /v1/logout` — 세션 종료

## 권한 모델
`FREE` → `PRO` 전환은 서버 entitlement가 `pro=true`일 때만 허용합니다.

구독 상태는 `active`, `trialing`, `past_due`, `canceled`, `expired`를 사용합니다. 실제 결제 공급자 상태를 서버가 해석해 내부 상태로 통일합니다.

## 현재 상태
v37에서는 **실제 서버/결제 연결을 하지 않습니다.** 앱에는 계약 상태를 보여주는 준비 화면만 추가되어 있으며, 기존 로컬 PRO 테스트 기능은 개발 검증용으로만 유지됩니다.
