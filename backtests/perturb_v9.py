"""v9 최종안(2엔진 통합) 파라미터 교란 — 모멘텀 창 × 추세 파라미터 고원 확인."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtests.candidates_v8 as C8  # noqa: E402
import backtests.engine_v9 as E9  # noqa: E402

ASOF = "2026-07-01"
MOM_WINDOWS = [(21, 63), (42, 84), (63, 126), (63, 252), (126, 252)]
STREAKS = [3, 4, 5, 6, 7]
SLOPES = [10, 15, 20, 25, 30]


def main():
    spine = C8.load("TQQQ_SYNTH").loc[:ASOF].index
    assets, meta = E9.load_universe()

    print("# v9 2엔진 통합 — 파라미터 교란 (전체구간)\n")

    print("## A. 모멘텀 창 민감도 (기준 추세 20/5)\n")
    print("| 모멘텀(단/장) | CAGR | MDD | 샤프 |")
    print("|---|---:|---:|---:|")
    for s, l in MOM_WINDOWS:
        prices, sig, mom, dd = E9.build_inputs(assets, meta, spine, mom_short=s, mom_long=l)
        eq, _ = E9.run_2engine(spine, prices, sig, mom, meta, dd, use_engineB=True)
        m = C8.compute(eq)
        star = "**" if (s, l) == (63, 126) else ""
        print(f"| {star}{s}/{l}{star} | {star}{m['CAGR']:.1%}{star} | {m['MDD']:.1%} | {m['Sharpe']:.2f} |")
    print()

    print("## B. 추세 기울기 × 스트릭 — CAGR (모멘텀 63/126 고정)\n")
    print("| 기울기\\스트릭 | " + " | ".join(str(c) for c in STREAKS) + " |")
    print("|---" * (len(STREAKS) + 1) + "|")
    for sl in SLOPES:
        cells = []
        for cd in STREAKS:
            prices, sig, mom, dd = E9.build_inputs(assets, meta, spine,
                                                   slope_lookback=sl, confirm_days=cd)
            eq, _ = E9.run_2engine(spine, prices, sig, mom, meta, dd, use_engineB=True)
            m = C8.compute(eq)
            base = "**" if (sl == 20 and cd == 5) else ""
            cells.append(f"{base}{m['CAGR']:.0%}{base}")
        print(f"| {'**20**' if sl==20 else sl} | " + " | ".join(cells) + " |")
    print()


if __name__ == "__main__":
    main()
