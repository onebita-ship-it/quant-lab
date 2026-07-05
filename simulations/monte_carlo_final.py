"""몬테카를로 — 최종 견고안 (v4/v3: 기울기+스트릭5 + 쿼터손절, 쿨다운 OFF).

파이프라인은 monte_carlo_v3와 동일:
  QQQ 일수익률 20일 블록 부트스트랩 → 각 경로 QQQ로 trend_signal(기울기+스트릭5) →
  같은 QQQ에서 3x TQQQ 파생 → v4 엔진(쿨다운 0)으로 전략 실행.
200일선 워밍업으로 실제 QQQ 최근 250일 부착.

두 권장 구성을 동일 경로에서 비교:
  - 최종 견고안 (100% 투입)
  - 최종 견고안 + 현금 75% (추가 방어 레버)

[한계] "과거 수익률 분포 유지" 가정. 폭락이 더 잦거나 크면 실제 위험은 더 큼.
또한 20일 블록 부트스트랩은 다년 지속 하락장을 거의 못 만들어 추세 필터의 이점을 과소평가한다.

사용: python simulations/monte_carlo_final.py --years 5 --paths 2000
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
from strategies import infinite_buying_v4 as v4  # noqa: E402

DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
EXPENSE, BORROW, LEVERAGE = 0.0095, 0.02, 3.0
WARMUP = 250


def summarize(finals, mdds):
    q = lambda a, x: np.percentile(a, x)  # noqa: E731
    return {"ret5": q(finals, 5), "ret50": q(finals, 50), "ret95": q(finals, 95),
            "mdd5": q(mdds, 5), "mdd50": q(mdds, 50), "mdd95": q(mdds, 95),
            "loss": float(np.mean(finals < 0)), "half": float(np.mean(finals <= -0.5))}


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
    warm_ret = qqq_ret[-WARMUP:]

    n_days = args.years * 252
    sim = bootstrap_paths(qqq_ret, n_days, args.paths, args.block, args.seed)
    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + n_days)
    trade_dates = full_dates[WARMUP:]
    daily_drag = EXPENSE / 252 + BORROW * (LEVERAGE - 1) / 252

    def params(alloc):
        return v4.Params(divisions=args.divisions, take_profit_pct=args.take_profit,
                         exhaust_action="quarter", use_trend_filter=True,
                         reentry_cooldown_days=0, allocation=alloc)
    p100, p75 = params(1.0), params(0.75)

    f100, m100, f75, m75 = [], [], [], []
    for i in range(args.paths):
        full_qqq_ret = np.concatenate([warm_ret, sim[i]])
        qqq_close = pd.Series(100 * np.cumprod(1 + full_qqq_ret), index=full_dates)
        trend = v4.trend_signal_v4(qqq_close, trade_dates, require_rising=True, confirm_days=5)
        tqqq_close = pd.Series(100 * np.cumprod(1 + (LEVERAGE * sim[i] - daily_drag)),
                               index=trade_dates)
        a = compute(v4.run(tqqq_close, p100, trend_ok=trend).equity)
        b = compute(v4.run(tqqq_close, p75, trend_ok=trend).equity)
        f100.append(a["TotalReturn"]); m100.append(a["MDD"])
        f75.append(b["TotalReturn"]); m75.append(b["MDD"])

    s100 = summarize(np.array(f100), np.array(m100))
    s75 = summarize(np.array(f75), np.array(m75))

    print(f"\n=== 몬테카를로 {args.paths}경로 × {args.years}년 | 최종 견고안 "
          f"(기울기+스트릭5·쿼터손절·쿨다운OFF, 분할{args.divisions}/익절{args.take_profit:.0%}) ===\n")
    hdr = f"{'지표':<26}{'최종(100% 투입)':>18}{'최종 +현금75%':>18}"
    print(hdr); print("-" * 62)
    pf = lambda x: f"{x:.1%}"  # noqa: E731
    def row(name, a, b): print(f"{name:<26}{pf(a):>18}{pf(b):>18}")
    row("총수익 5%ile", s100['ret5'], s75['ret5'])
    row("총수익 중앙값", s100['ret50'], s75['ret50'])
    row("총수익 95%ile", s100['ret95'], s75['ret95'])
    row("MDD 중앙값", s100['mdd50'], s75['mdd50'])
    row("MDD 5%ile(악화)", s100['mdd5'], s75['mdd5'])
    row("손실 확률(원금미만)", s100['loss'], s75['loss'])
    row("반토막 확률(-50%이하)", s100['half'], s75['half'])

    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"mc_final_{args.years}y.csv"
    pd.DataFrame({"ret_100": f100, "mdd_100": m100,
                  "ret_75": f75, "mdd_75": m75}).to_csv(out, index=False)
    print(f"\n[저장] {out}")
    print("[주의] 과거 분포 유지 가정. 블록 부트스트랩은 다년 하락장을 과소표현 → 필터 이점 과소평가.")


if __name__ == "__main__":
    main()
