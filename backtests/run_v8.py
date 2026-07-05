"""v8 후보 5종 비교 실행 → result_v8.md 본문 출력."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtests.candidates_v8 as C  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

ASOF = "2026-07-01"
STRATS = [("S1 베이스라인", C.s1_baseline), ("S2 추세스위칭", C.s2_trend_switch),
          ("S3 하이브리드", C.s3_hybrid), ("S4 로테이션", C.s4_rotation),
          ("S5 숏검증", C.s5_short)]


def load_prices():
    keys = ["TQQQ", "UPRO", "SOXL", "SQQQ", "SGOV"]
    src = {"TQQQ": "TQQQ_SYNTH", "UPRO": "UPRO_SYNTH", "SOXL": "SOXL_SYNTH",
           "SQQQ": "SQQQ_SYNTH", "SGOV": "SGOV_SYNTH"}
    spine = C.load("TQQQ_SYNTH").loc[:ASOF]
    prices = {}
    for k in keys:
        prices[k] = C.load(src[k]).reindex(spine.index).loc[:ASOF]
    prices["TQQQ"] = prices["TQQQ"]
    return prices, spine.index


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def main():
    prices, idx = load_prices()
    qqq = C.load("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, idx, require_rising=True, confirm_days=5)

    results = {}
    for name, fn in STRATS:
        eq, info = fn(prices, trend)
        results[name] = (eq, info)

    print("# v8 — 전략 후보 5종 비교 (연 20%+·하루 10분 목표)\n")
    print("> 데이터: 합성+실 스플라이스(`*_SYNTH`, 2010이전 합성) · 신호: QQQ 200일선 기울기+스트릭5 "
          "· 비용: 편도 0.12% · 현금=SGOV(^IRX 프록시, 평균 2.0%/년)\n")

    # ① 전체 / ② 2013~ CAGR·MDD·샤프
    for tag, s0 in [("① 전체구간 (1999~2026 YTD)", None), ("② 2013~2026", "2013-01-01")]:
        print(f"## {tag}\n")
        print("| 전략 | CAGR | MDD | 샤프 | 총수익 | ⑤ ON구간 연환산 |")
        print("|---|---:|---:|---:|---:|---:|")
        for name, _ in STRATS:
            eq, _ = results[name]
            sub = eq.loc[s0:] if s0 else eq
            m = C.compute(sub)
            ann = C.on_annualized(sub, trend)
            print(f"| {name} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | "
                  f"{pf(m['TotalReturn'])} | {pf(ann)} |")
        print()

    # ③ 스트레스 4구간
    print("## ③ 스트레스 4구간 (총수익 / MDD)\n")
    hdr = "| 전략 | " + " | ".join(n for _, _, n in C.STRESS) + " |"
    print(hdr); print("|---" * (len(C.STRESS) + 1) + "|")
    for name, _ in STRATS:
        eq, _ = results[name]
        cells = []
        for s, e, _ in C.STRESS:
            m = C.compute(eq.loc[s:e])
            cells.append(f"{pf(m['TotalReturn'])} / {pf(m['MDD'])}")
        print(f"| {name} | " + " | ".join(cells) + " |")
    print()

    # ④ 연도별
    print("## ④ 연도별 수익률\n")
    yrs = sorted(C.load("TQQQ_SYNTH").loc[:ASOF].index.year.unique())
    print("| 연도 | " + " | ".join(n for n, _ in STRATS) + " |")
    print("|---" * (len(STRATS) + 1) + "|")
    ys = {name: C.yearly(results[name][0]) for name, _ in STRATS}
    for y in yrs:
        emph = "**" if y in (2025, 2026) else ""
        lab = f"{y} YTD" if y == 2026 else str(y)
        cells = [f"{emph}{lab}{emph}"]
        for name, _ in STRATS:
            cells.append(f"{emph}{pf(ys[name].get(y, float('nan')))}{emph}")
        print("| " + " | ".join(cells) + " |")
    print()

    # 전환/휩쏘 통계 (S2, S5)
    print("## 전환·휩쏘 통계 (신호 스위칭 계열)\n")
    print("| 전략 | 전환수 | TQQQ보유 스틴트 | 휩쏘율(손실스틴트) | 평균스틴트 | 최악스틴트 |")
    print("|---|---:|---:|---:|---:|---:|")
    for name in ["S2 추세스위칭", "S5 숏검증"]:
        _, info = results[name]
        print(f"| {name} | {info['switches']} | {info['stints']} | "
              f"{pf(info['whipsaw_rate'])} | {pf(info['avg_stint'])} | {pf(info['worst_stint'])} |")
    print()

    # S4 로테이션 픽 분포
    _, r4 = results["S4 로테이션"]
    print(f"**S4 로테이션 월별 보유 분포**: {r4['picks']}\n")

    # 숏 검증 결론(S2 vs S5)
    e2 = C.compute(results["S2 추세스위칭"][0]); e5 = C.compute(results["S5 숏검증"][0])
    print("## 숏 검증 결론 (S2 vs S5)\n")
    print(f"- S2(OFF=SGOV): CAGR {pf(e2['CAGR'])}, MDD {pf(e2['MDD'])}, 샤프 {e2['Sharpe']:.2f}")
    print(f"- S5(OFF=SQQQ25%+SGOV75%): CAGR {pf(e5['CAGR'])}, MDD {pf(e5['MDD'])}, 샤프 {e5['Sharpe']:.2f}")
    verdict = "숏 추가가 순이득" if (e5['CAGR'] > e2['CAGR'] and e5['Sharpe'] >= e2['Sharpe']) \
        else "숏 추가는 순손해(개선 없음)"
    print(f"- **판정: {verdict}**\n")

    # CAGR 랭킹(목표 20% 근접도)
    print("## 목표(연 20%) 근접도 랭킹 — 전체구간 CAGR\n")
    rank = sorted(STRATS, key=lambda ns: C.cagr(results[ns[0]][0]), reverse=True)
    print("| 순위 | 전략 | CAGR | MDD | 샤프 |")
    print("|---|---|---:|---:|---:|")
    for i, (name, _) in enumerate(rank, 1):
        m = C.compute(results[name][0])
        print(f"| {i} | {name} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} |")
    print()


if __name__ == "__main__":
    main()
