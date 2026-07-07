# 퀀트 전략 연구소 (quant-lab)

## 목표
TQQQ 무한매수법 계열 전략 백테스트 → 파라미터 최적화 → 미래 시뮬레이션으로
가장 견고한(robust) 퀀트 매매법을 찾는다.

## 프로젝트 구조
- `data/` — 시세 캐시 (git에 올리지 않음, 스크립트로 재생성)
- `scripts/` — 데이터 수집/생성 유틸
- `strategies/` — 전략 구현 (파일명: 전략명_v버전.py)
- `backtests/` — 백테스트 실행기 + 성과지표
- `simulations/` — 몬테카를로 미래 시뮬레이션
- `reports/` — 결과 리포트 (CSV/차트)

## 규칙
- 데이터: `scripts/download_data.py`로 받아 `data/`에 캐시 후 재사용 (매번 다운로드 금지)
- 백테스트 필수 반영: 매매수수료, 슬리피지 (기본값: 수수료 0.07%, 슬리피지 0.05%)
- 성과지표 필수 출력: CAGR, MDD, 샤프비율, 사이클 승률, 최장 하락기간(underwater)
- 과최적화 방지: 파라미터 최적화 시 반드시 학습구간/검증구간 분리 (walk-forward)
- TQQQ는 2010년 상장이라 그 이전 구간은 `scripts/make_synthetic_tqqq.py`로
  QQQ에서 합성한 가상 TQQQ를 사용해 2000년 닷컴버블·2008년 스트레스 테스트 수행
- 전략 수정 시마다 git commit (커밋 메시지에 변경한 파라미터 명시)
- 몬테카를로 결과 해석 시 "과거 분포가 유지된다는 가정"임을 리포트에 명시
- **태그·릴리스는 사용자가 명시적으로 지시할 때만 생성한다.** 평소 작업은 커밋·푸시까지만 하고,
  릴리스 생성을 먼저 제안하지도 않는다. (v2.0 "실전 개시" 릴리스됨 — 이후 릴리스도 같은 원칙)
- **투트랙 체제(2026-07-07)**: 운용 룰(`result_final.md §0`)은 방화벽 — 변경은 분기 리뷰+재검증 통과 시만.
  연구 트랙(새 전략 백테스트)은 상시 허용하되, 실계좌 채택은 하네스+왜곡 감사 → 3개월+ 페이퍼 →
  대표 합의 → **별도 시드/슬리브** 관문을 전부 거친다. 같은 전략의 무한 개량(v11, v12…)은 경계.

## 실행 방법
```bash
pip install -r requirements.txt
python scripts/download_data.py            # 실데이터 (인터넷 필요)
python scripts/download_data.py --synthetic # 합성 테스트 데이터 (오프라인)
python scripts/make_synthetic_tqqq.py      # QQQ → 가상 TQQQ 합성 (1999~)
python backtests/run_backtest.py           # 무한매수법 백테스트
python simulations/monte_carlo.py          # 몬테카를로 시뮬레이션
```
