# quant-lab — TQQQ 무한매수법 백테스트 연구소

라우어 무한매수법 계열 전략을 백테스트하고, 파라미터를 walk-forward로 검증하고,
몬테카를로로 미래 시나리오를 시뮬레이션하는 프로젝트입니다.

## 빠른 시작

```bash
pip install -r requirements.txt

# 1. 데이터 받기 (인터넷 필요; 오프라인 테스트는 --synthetic)
python scripts/download_data.py

# 2. 가상 TQQQ 합성 (1999년~, 닷컴버블·2008 스트레스 테스트용)
python scripts/make_synthetic_tqqq.py

# 3. 백테스트 (기본: 40분할, +10% 익절, 소진 시 전량매도)
python backtests/run_backtest.py --ticker TQQQ
python backtests/run_backtest.py --ticker TQQQ_SYNTH   # 2000·2008 포함 구간

# 4. 파라미터 그리드서치 + walk-forward 검증
python backtests/run_backtest.py --ticker TQQQ --grid

# 5. 몬테카를로 미래 시뮬레이션 (5년 × 2000경로)
python simulations/monte_carlo.py --ticker TQQQ --years 5 --paths 2000
```

## 구조

| 경로 | 역할 |
|---|---|
| `CLAUDE.md` | Claude Code가 세션마다 읽는 프로젝트 규칙서 |
| `scripts/` | 데이터 수집(`download_data.py`), 가상 TQQQ 합성(`make_synthetic_tqqq.py`) |
| `strategies/` | 전략 구현. `infinite_buying.py` = 무한매수법 v1 |
| `backtests/` | 실행기(`run_backtest.py`) + 성과지표(`metrics.py`) |
| `simulations/` | 몬테카를로(`monte_carlo.py`) |
| `data/`, `reports/` | 캐시·결과 (git 미포함, 재생성 가능) |

## 무한매수법 v1 규칙 (구현 기준)

- 원금 40분할, 매 거래일 종가에 1회분 매수
- 평단 대비 +10% 도달 시 전량 익절 → 새 사이클 (복리)
- 40회분 소진 시: `--exhaust sell`(전량 매도, 기본) 또는 `--exhaust hold`(보유 대기)
- 수수료 0.07% + 슬리피지 0.05% (편도) 반영

## 주의

- 백테스트 성적 ≠ 미래 수익. 그리드서치 결과는 반드시 검증구간 성과와 함께 볼 것.
- TQQQ는 3배 레버리지 — 변동성 잠식 존재. `TQQQ_SYNTH`로 2000·2008 구간 필수 확인.
- 몬테카를로는 "과거 분포 유지" 가정 위의 도구이지 예언이 아님.
- 이 코드는 연구 도구이며 투자 판단과 책임은 본인에게 있음.

## 두 기기(데스크탑/노트북)에서 쓰기

```bash
# 작업 끝날 때
git add -A && git commit -m "..." && git push
# 다른 기기에서 시작할 때
git pull
python scripts/download_data.py   # data/는 git에 없으므로 기기마다 1회 실행
```
