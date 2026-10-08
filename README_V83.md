# Coin Pattern Beta v83

이번 버전은 **v74 Meta Visible UI를 원본으로 유지**하고 아래 두 부분만 수정했습니다.

1. **메타 분류**
   - CoinMarketCap 공식 Categories API의 `symbol` 필터를 사용해 해당 코인의 현재 CMC 카테고리를 직접 조회합니다.
   - CMC 카테고리를 Coin Pattern의 표시용 메타로 정규화합니다.
   - CMC 조회 실패 시 기존 sector_map fallback을 사용합니다.
   - 메타는 설명용이며 점수에는 반영하지 않습니다.

2. **코인 상세 분석 > 패턴 발생 이력**
   - 기본 Streamlit `st.dataframe` 흰색 표를 제거했습니다.
   - 기존 다크 UI와 동일한 카드형 증거 패널로 표시합니다.
   - PC 2열 / 모바일 1열 반응형입니다.

기존 v74 시작 방식과 나머지 UI/분석 로직은 변경하지 않는 것을 목표로 했습니다.
