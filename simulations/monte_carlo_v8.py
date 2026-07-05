"""몬테카를로 — v8 상위 2후보(S2 추세스위칭·S3 하이브리드) 5년 분포.

monte_carlo_final과 동일 파이프라인(QQQ 20일 블록 부트스트랩 → 경로별 trend → 3x 파생).
현금(SGOV)은 상수 연 2.0%(^IRX 장기평균) 일할. S2/S3를 동일 경로에서 비교.

[한계] 과거분포 유지 가정 + 20일 블록은 다년 하락장 과소표현 → 추세필터 이점 과소평가.
사용: python simulations/monte_carlo_v8.py --years 5 --paths 2000
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
import backtests.candidates_v8 as C  # noqa: E402

DATA = ROOT / "data"
EXPENSE, BORROW, LEVERAGE = 0.0095, 0.02, 3.0
WARMUP = 250
SGOV_ANNUAL = 0.020


def summ(finals, mdds, years):
    q = lambda a, x: float(np.percentile(a, x))  # noqa: E731
    med = q(finals, 50)
    return {"ret50": med, "cagr50": (1 + med) ** (1 / years) - 1,
            "ret5": q(finals, 5), "ret95": q(finals, 95),
            "mdd50": q(mdds, 50), "mdd5": q(mdds, 5),
            "loss": float(np.mean(finals < 0)), "half": float(np.mean(finals <= -0.5))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--block", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    qqq = pd.read_csv(DATA / "QQQ.csv", index_col="Date", parse_dates=True)["Close"].dropna()
    qret = qqq.pct_change().dropna().values
    warm = qret[-WARMUP:]
    n_days = args.years * 252
    sim = bootstrap_paths(qret, n_days, args.paths, args.block, args.seed)
    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + n_days)
    trade_dates = full_dates[WARMUP:]
    drag = EXPENSE / 252 + BORROW * (LEVERAGE - 1) / 252
    sgov = pd.Series(100 * np.cumprod(np.full(len(trade_dates), 1 + SGOV_ANNUAL / 252)),
                     index=trade_dates)

    res = {"S2": {"r": [], "m": []}, "S3": {"r": [], "m": []}}
    for i in range(args.paths):
        fq = np.concatenate([warm, sim[i]])
        qc = pd.Series(100 * np.cumprod(1 + fq), index=full_dates)
        trend = v6.trend_signal_v6(qc, trade_dates, require_rising=True, confirm_days=5)
        tq = pd.Series(100 * np.cumprod(1 + (LEVERAGE * sim[i] - drag)), index=trade_dates)
        prices = {"TQQQ": tq, "SGOV": sgov}
        for tag, fn in [("S2", C.s2_trend_switch), ("S3", C.s3_hybrid)]:
            eq, _ = fn(prices, trend)
            res[tag]["r"].append(eq.iloc[-1] / eq.iloc[0] - 1)
            res[tag]["m"].append((eq / eq.cummax() - 1).min())
        if (i + 1) % 500 == 0:
            print(f"  ...{i+1}/{args.paths}", file=sys.stderr)

    s2 = summ(np.array(res["S2"]["r"]), np.array(res["S2"]["m"]), args.years)
    s3 = summ(np.array(res["S3"]["r"]), np.array(res["S3"]["m"]), args.years)
    print(f"\n# 몬테카를로 v8  |  {args.paths}경로 × {args.years}년 (현금 {SGOV_ANNUAL:.1%})\n")
    print(f"| 지표 | S2 추세스위칭 | S3 하이브리드 |")
    print("|---|---:|---:|")
    pf = lambda x: f"{x:.1%}"  # noqa: E731
    print(f"| CAGR 중앙값 | {pf(s2['cagr50'])} | {pf(s3['cagr50'])} |")
    print(f"| 총수익 중앙값 | {pf(s2['ret50'])} | {pf(s3['ret50'])} |")
    print(f"| 총수익 5%ile | {pf(s2['ret5'])} | {pf(s3['ret5'])} |")
    print(f"| 총수익 95%ile | {pf(s2['ret95'])} | {pf(s3['ret95'])} |")
    print(f"| MDD 중앙값 | {pf(s2['mdd50'])} | {pf(s3['mdd50'])} |")
    print(f"| MDD 5%ile(악화) | {pf(s2['mdd5'])} | {pf(s3['mdd5'])} |")
    print(f"| 손실확률 | {pf(s2['loss'])} | {pf(s3['loss'])} |")
    print(f"| 반토막확률 | {pf(s2['half'])} | {pf(s3['half'])} |")
    print("\n[주의] 과거분포 유지 가정. 블록 부트스트랩은 다년 하락장 과소표현 → 추세필터 이점 과소평가.")


if __name__ == "__main__":
    main()
