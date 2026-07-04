"""몬테카를로 미래 시뮬레이션 — v3(기울기+확정스트릭5) 전용.

v3의 추세 필터는 QQQ 200일선 기준이므로, 백테스트 파이프라인과 동일하게:
  1) QQQ 일수익률을 20일 블록 부트스트랩으로 미래 N년 경로 생성
  2) 각 경로의 QQQ 종가로 trend_signal_v3(기울기+스트릭5) 계산
  3) 같은 QQQ 수익률에서 3배 레버리지 TQQQ 경로를 파생(make_synthetic_tqqq와 동일 수식)
  4) 그 TQQQ 경로에 v3 전략을 돌려 결과 분포 집계

200일선이 첫 매매일부터 유효하도록 실제 QQQ 최근 250일을 워밍업으로 앞에 붙인다.
비교를 위해 동일 경로에서 '추세필터 OFF'(v1 성격)도 함께 집계한다.

[한계] "과거 수익률 분포가 미래에도 유지된다" 가정. 폭락이 더 잦거나 크면 실제 위험은 더 큼.

사용:
  python simulations/monte_carlo_v3.py --years 5 --paths 2000
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from simulations.monte_carlo import bootstrap_paths  # noqa: E402
from strategies import infinite_buying_v3 as v3  # noqa: E402

DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"

EXPENSE, BORROW, LEVERAGE = 0.0095, 0.02, 3.0  # make_synthetic_tqqq와 동일
WARMUP = 250  # 200일선 워밍업용 실제 QQQ 최근 일수


def summarize(finals, mdds):
    q = lambda a, x: np.percentile(a, x)  # noqa: E731
    return {
        "ret5": q(finals, 5), "ret50": q(finals, 50), "ret95": q(finals, 95),
        "mdd5": q(mdds, 5), "mdd50": q(mdds, 50), "mdd95": q(mdds, 95),
        "loss": float(np.mean(finals < 0)),
        "half": float(np.mean(finals <= -0.5)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--block", type=int, default=20)
    ap.add_argument("--divisions", type=int, default=40)
    ap.add_argument("--take-profit", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    qqq = pd.read_csv(DATA_DIR / "QQQ.csv", index_col="Date", parse_dates=True)["Close"].dropna()
    qqq_ret = qqq.pct_change().dropna().values
    warm_ret = qqq.pct_change().dropna().values[-WARMUP:]  # 워밍업용 실제 최근 수익률

    n_days = args.years * 252
    sim = bootstrap_paths(qqq_ret, n_days, args.paths, args.block, args.seed)
    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + n_days)
    trade_dates = full_dates[WARMUP:]

    p_on = v3.Params(divisions=args.divisions, take_profit_pct=args.take_profit,
                     exhaust_action="quarter", use_trend_filter=True)
    p_off = v3.Params(divisions=args.divisions, take_profit_pct=args.take_profit,
                      exhaust_action="quarter", use_trend_filter=False)

    fin_on, mdd_on, fin_off, mdd_off = [], [], [], []
    daily_drag = EXPENSE / 252 + BORROW * (LEVERAGE - 1) / 252
    for i in range(args.paths):
        full_qqq_ret = np.concatenate([warm_ret, sim[i]])
        qqq_close = pd.Series(100 * np.cumprod(1 + full_qqq_ret), index=full_dates)
        trend = v3.trend_signal_v3(qqq_close, trade_dates,
                                   require_rising=True, confirm_days=5)

        tqqq_ret = LEVERAGE * sim[i] - daily_drag
        tqqq_close = pd.Series(100 * np.cumprod(1 + tqqq_ret), index=trade_dates)

        r_on = v3.run(tqqq_close, p_on, trend_ok=trend)
        m_on = compute(r_on.equity)
        fin_on.append(m_on["TotalReturn"]); mdd_on.append(m_on["MDD"])

        r_off = v3.run(tqqq_close, p_off)
        m_off = compute(r_off.equity)
        fin_off.append(m_off["TotalReturn"]); mdd_off.append(m_off["MDD"])

    fin_on, mdd_on = np.array(fin_on), np.array(mdd_on)
    fin_off, mdd_off = np.array(fin_off), np.array(mdd_off)
    s_on, s_off = summarize(fin_on, mdd_on), summarize(fin_off, mdd_off)

    print(f"\n=== 몬테카를로 {args.paths}경로 × {args.years}년 | QQQ부트스트랩→3x파생 "
          f"| 분할={args.divisions} 익절={args.take_profit:.0%} 소진=quarter ===\n")
    hdr = f"{'지표':<26}{'추세OFF(v1성격)':>18}{'v3 기울기+스트릭5':>20}"
    print(hdr); print("-" * len(hdr.encode('ascii', 'replace')))
    def row(name, a, b, fmt): print(f"{name:<26}{fmt(a):>18}{fmt(b):>20}")
    pf = lambda x: f"{x:.1%}"  # noqa: E731
    row("총수익 5%ile", s_off['ret5'], s_on['ret5'], pf)
    row("총수익 중앙값", s_off['ret50'], s_on['ret50'], pf)
    row("총수익 95%ile", s_off['ret95'], s_on['ret95'], pf)
    row("MDD 중앙값", s_off['mdd50'], s_on['mdd50'], pf)
    row("MDD 5%ile(악화)", s_off['mdd5'], s_on['mdd5'], pf)
    row("손실 확률(원금미만)", s_off['loss'], s_on['loss'], pf)
    row("반토막 확률(-50%이하)", s_off['half'], s_on['half'], pf)

    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"mc_v3_{args.years}y.csv"
    pd.DataFrame({"ret_trendON": fin_on, "mdd_trendON": mdd_on,
                  "ret_trendOFF": fin_off, "mdd_trendOFF": mdd_off}).to_csv(out, index=False)
    print(f"\n[저장] {out}")
    print("[주의] 과거 분포 유지 가정. 스트레스 테스트 병행 권장.")


if __name__ == "__main__":
    main()
