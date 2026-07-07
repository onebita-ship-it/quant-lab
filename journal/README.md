# journal — 확정 룰북 기반 매매일지 시스템

`result_final.md §0 확정 룰북`을 그대로 코드화한 실전 운용 도구. 매일 아침 브리핑 → 체결 기록
(규칙·슬리피지 자동 대조) → 월말 요약의 3단 워크플로.

## 구성
| 파일 | 역할 |
|---|---|
| `day1_checklist.md` | **실전 첫날(Day 1) 체크리스트** — 계좌 세팅 → 배분 주문 → 기록 시작 |
| `daily_check.py` | 매일 아침: 진입 게이트(200일선 3조건 **AND 13612W 카나리아**) + 1회분 금액 + **위성(엔진A) 타깃** |
| `log_trade.py` | 체결 입력 → 규칙 준수·슬리피지 자동 대조 + 상태 갱신 |
| `monthly_report.py` | 월말: 규칙 준수율 · 추적오차(실행 드리프트) · 성과 요약 |
| `common.py` | 공용(설정·상태·신호·가격 로딩) |
| `config.json` | 설정(자동 생성, 아래 기본값) — gitignore |
| `state.json` | 포트폴리오/사이클 상태(자동 갱신) — gitignore |
| `trades.csv` | 체결 로그(자동 누적) — gitignore |

> `config.json`/`state.json`/`trades.csv`는 **개인 데이터라 git에 올리지 않는다**(첫 실행 시 기본값으로 자동 생성).

## 매일 워크플로

```bash
# 1) 아침: 오늘 할 일 확인 (--refresh로 최신가 갱신)
python journal/daily_check.py --refresh

# 2) 장중/마감: 체결한 대로 기록
python journal/log_trade.py --action buy --shares 0.53 --price 75.10           # 정규 매수
python journal/log_trade.py --action buy --shares 0.53 --price 75.10 --new-cycle # 새 사이클 첫 매수
python journal/log_trade.py --action take_profit --shares 21.4 --price 92.30    # 익절(전량)
python journal/log_trade.py --action quarter --shares 5.35 --price 40.10        # 쿼터손절 1일분
python journal/log_trade.py --action deploy_reserve                             # 리저브 편입(-30/-50% 발동 시)

# 3) 월말: 요약
python journal/monthly_report.py --month 2026-07
```

옵션: `--date YYYY-MM-DD`(기록일 지정), `--fee 실제수수료$`, `--dry`(대조만·미저장),
`daily_check --asof YYYY-MM-DD`(특정일 시뮬).

## 확정 룰북 (요약 — 상세는 `../result_final.md §0`)
0. **배분(⑧·B안 확정)**: 코어 42.5% / 추세위성 42.5% / 금(GLD) 15%, 연 1회 리밸런스.
   위성 = 원지수 추세 ON 자산 중 3·6개월 모멘텀 1위(월말 교체, 신호 OFF 시 즉시), 없으면 SGOV.
1. **진입**: QQQ 종가>200일선 AND 200일선 상승(20일) AND 5거래일 연속 **AND 13612W 카나리아**
   (SPY·EFA·EEM·AGG 월말 모멘텀 전부 양수, 다음 달 유지) → 신규 사이클 허용.
2. **매수**: 사이클 시작일에 `1회분=현금/40` 고정, 매 거래일 종가에 1회분씩 최대 40회.
3. **익절**: 평단 대비 +15% → 전량 매도, 사이클 종료.
4. **쿼터손절**: 40회분 소진 후 미익절이면 4일에 걸쳐 1/4씩 청산.
5. **현금/리저브(선택)**: 100% 투입 또는 67%+리저브(총자산 고점대비 -30%/-50%에 절반씩 편입).
6. **파킹(⑦·의무)**: 미투입 현금·리저브는 즉시 SGOV 매수. 사이클 시작 시 첫 10회분만 예수금, 나머지 SGOV.
   **매주 금요일**에 다음 주 5회분만 SGOV 매도(매일 매도 금지). 리저브 발동 시 SGOV 즉시 매도 후 투입.
   → 세후 검증상 파킹이 양도세 드래그를 거의 상쇄(`../result_tax.md`). `daily_check.py`가 금요일에 알림.

## 설정 기본값 (`config.json`)
`total_capital` 40000 · `deploy_frac` 1.0(=100%; 0.67로 바꾸면 67%+리저브) ·
`divisions` 40 · `take_profit_pct` 0.15 · `fee/slippage` 0.0007/0.0005 ·
`ma_window/slope_lookback/confirm_days` 200/20/5 · `reserve_triggers` [-0.30,-0.50].

## 주의
- 신호는 `QQQ`(기초지수) 200일선으로 판정하고, 매매는 `TQQQ`로 한다(백테스트와 동일).
- 이 도구는 **규칙 준수를 돕는 기록·점검용**이며 자동매매가 아니다. 주문·체결은 본인이 수행.
- 백테스트 한계(3배 레버리지 꼬리위험, 합성 데이터, 과거분포 가정)는 `../result_final.md` 참조.
