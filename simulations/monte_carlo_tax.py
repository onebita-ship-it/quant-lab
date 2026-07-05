"""몬테카를로 — 세후+파킹 (최종 견고안 100% / 67%+리저브).

monte_carlo_final과 동일 파이프라인(QQQ 20일 블록 부트스트랩 → 각 경로 trend 신호 →
같은 QQQ에서 3x 파생)에, backtests.tax_parking_backtest.run_engine을 얹어 세금·환전·
배당세·파킹을 반영한 5년 분포를 낸다.

시나리오: 세전 / 세후개인·파킹OFF / 세후개인·파킹ON / 세후법인·파킹OFF / 세후법인·파킹ON.
구성: 100% 투입, 67%+리저브(-30/-50%).

[한계] 과거 분포 유지 가정 + 20일 블록은 다년 하락장 과소표현(필터 이점 과소평가).
      환율 1350 KRW/USD 고정 가정(250만원 공제 환산).

사용: python simulations/monte_carlo_tax.py --years 5 --paths 2000
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulations.monte_carlo import bootstrap_paths  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402
from backtests import tax_parking_backtest as TP  # noqa: E402

DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
EXPENSE, BORROW, LEVERAGE = 0.0095, 0.02, 3.0
WARMUP = 250

SCENARIOS = [
    ("세전", TP.Opts(tax_mode="none", parking=False, fx_fee=False, div_tax=False)),
    ("세후개인·파킹OFF", TP.Opts(tax_mode="individual", parking=False)),
    ("세후개인·파킹ON", TP.Opts(tax_mode="individual", parking=True)),
    ("세후법인·파킹OFF", TP.Opts(tax_mode="corporate", parking=False)),
    ("세후법인·파킹ON", TP.Opts(tax_mode="corporate", parking=True)),
]
CONFIGS = [("100% 투입", 1.00, ()), ("67% + 리저브", 0.67, (-0.30, -0.50))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--block", type=int, default=20)
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

    # (config, scenario) → list of (total_return, mdd)
    acc = {(c[0], s[0]): {"ret": [], "mdd": []} for c in CONFIGS for s in SCENARIOS}

    for i in range(args.paths):
        full_qqq_ret = np.concatenate([warm_ret, sim[i]])
        qqq_close = pd.Series(100 * np.cumprod(1 + full_qqq_ret), index=full_dates)
        trend = v6.trend_signal_v6(qqq_close, trade_dates, require_rising=True, confirm_days=5)
        tqqq_close = pd.Series(100 * np.cumprod(1 + (LEVERAGE * sim[i] - daily_drag)),
                               index=trade_dates)
        for clabel, frac, trig in CONFIGS:
            for slabel, opts in SCENARIOS:
                eq, _, _ = TP.run_engine(tqqq_close, trend, frac, trig, opts=opts)
                r = eq.iloc[-1] / eq.iloc[0] - 1
                peak = eq.cummax()
                mdd = (eq / peak - 1).min()
                acc[(clabel, slabel)]["ret"].append(r)
                acc[(clabel, slabel)]["mdd"].append(mdd)
        if (i + 1) % 250 == 0:
            print(f"  ...{i+1}/{args.paths} 경로", file=sys.stderr)

    def q(a, x):
        return float(np.percentile(a, x))

    def cagr(total):
        return (1 + total) ** (1 / args.years) - 1

    print(f"\n# 몬테카를로 세후+파킹  |  {args.paths}경로 × {args.years}년  "
          f"(환율 {TP.FX_RATE:.0f} 가정)\n")
    for clabel, _, _ in CONFIGS:
        print(f"## {clabel}\n")
        print("| 시나리오 | 총수익 중앙값 | CAGR 중앙값 | 총수익 5%ile | MDD 중앙값 | "
              "손실확률 | 반토막확률 |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        med_pre = None
        for slabel, _ in SCENARIOS:
            rets = np.array(acc[(clabel, slabel)]["ret"])
            mdds = np.array(acc[(clabel, slabel)]["mdd"])
            med = q(rets, 50)
            if med_pre is None:
                med_pre = cagr(med)
            print(f"| {slabel} | {med:.1%} | {cagr(med):.2%} | {q(rets,5):.1%} | "
                  f"{q(mdds,50):.1%} | {np.mean(rets<0):.1%} | {np.mean(rets<=-0.5):.1%} |")
        print()

    # 순효과 요약(개인, CAGR 중앙값 %p)
    print("## 세금·파킹 순효과 (CAGR 중앙값 %p, 개인 기준)\n")
    print("| 구성 | 세전 CAGR | 세후(파킹OFF) | 세금효과 | 파킹효과 | 순효과 |")
    print("|---|---:|---:|---:|---:|---:|")
    for clabel, _, _ in CONFIGS:
        pre = cagr(q(np.array(acc[(clabel, "세전")]["ret"]), 50))
        toff = cagr(q(np.array(acc[(clabel, "세후개인·파킹OFF")]["ret"]), 50))
        ton = cagr(q(np.array(acc[(clabel, "세후개인·파킹ON")]["ret"]), 50))
        print(f"| {clabel} | {pre:.2%} | {toff:.2%} | {(toff-pre)*100:+.2f}%p | "
              f"{(ton-toff)*100:+.2f}%p | {(ton-pre)*100:+.2f}%p |")
    print("\n[주의] 과거 분포 유지 가정. 블록 부트스트랩은 다년 하락장 과소표현 → 필터 이점 과소평가.")


if __name__ == "__main__":
    main()
