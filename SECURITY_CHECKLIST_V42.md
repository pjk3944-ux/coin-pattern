# Coin Pattern v42 Security Checklist

## 완료
- [x] Bearer access token은 DB에 SHA-256 hash로만 저장
- [x] 세션 만료
- [x] 로그아웃 세션 폐기
- [x] 전체 세션 폐기 API
- [x] 로그인/가입/재설정 요청 rate limit
- [x] 비밀번호 최소 10자
- [x] 비밀번호 변경 후 기존 세션 폐기
- [x] reset token hash 저장
- [x] reset token 15분 만료
- [x] reset token 1회 사용

## 운영 전 필수
- [ ] HTTPS/TLS
- [ ] reverse proxy 및 보안 헤더
- [ ] 공유 rate-limit 저장소
- [ ] secret manager
- [ ] 실제 이메일 인증/비밀번호 재설정 메일
- [ ] 이메일 주소 검증 정책
- [ ] 계정 잠금/abuse 방어
- [ ] DB 백업/복구
- [ ] 개인정보/약관/보존정책 검토
- [ ] 실제 결제 provider의 webhook 서명 규격 적용
