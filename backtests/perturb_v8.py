"""v8 상위 2후보(S2 추세스위칭·S3 하이브리드) 파라미터 교란 — 신호(기울기·스트릭) 고원 확인."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtests.candidates_v8 as C  # noqa: E402
from backtests.run_v8 import load_prices  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

SLOPES = [10, 15, 20, 25, 30]
STREAKS = [3, 4, 5, 6, 7]


def grid(prices, idx, qqq, fn, metric):
    print(f"| 기울기\\스트릭 | " + " | ".join(str(s) for s in STREAKS) + " |")
    print("|---" * (len(STREAKS) + 1) + "|")
    for sl in SLOPES:
        cells = []
        for cd in STREAKS:
            trend = v6.trend_signal_v6(qqq, idx, require_rising=True,
                                       slope_lookback=sl, confirm_days=cd)
            eq, _ = fn(prices, trend)
            m = C.compute(eq)
            base = "**" if (sl == 20 and cd == 5) else ""
            cells.append(f"{base}{metric(m)}{base}")
        print(f"| {'**20**' if sl==20 else sl} | " + " | ".join(cells) + " |")


def main():
    prices, idx = load_prices()
    qqq = C.load("QQQ").loc[:"2026-07-01"]
    for name, fn in [("S2 추세스위칭", C.s2_trend_switch), ("S3 하이브리드", C.s3_hybrid)]:
        print(f"# {name} 파라미터 교란 (전체구간)\n")
        print("## CAGR\n")
        grid(prices, idx, qqq, fn, lambda m: f"{m['CAGR']:.0%}")
        print("\n## 샤프\n")
        grid(prices, idx, qqq, fn, lambda m: f"{m['Sharpe']:.2f}")
        print("\n## MDD\n")
        grid(prices, idx, qqq, fn, lambda m: f"{m['MDD']:.0%}")
        print()


if __name__ == "__main__":
    main()
