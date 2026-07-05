"""최종 포트폴리오 배분 백테스트 — 코어(최종 견고안) + 위성(v9 엔진A 단독)을 한 계좌에서 병행.

코어  : 최종 견고안(무한매수 개선판, 세후+파킹 검증본) — tax_parking.run_engine.
위성  : v9 엔진 A 단독(추세+모멘텀 로테이션, 엔진B 제외) — SOXL 포함/제외 두 버전.
배분  : 100/0, 80/20, 70/30, 50/50, 0/100, **연 1회 리밸런스**(연말, 편도 0.12% 비용).
엔진B : 포트폴리오에서 제외(별도 '외부 추가자금 폭락 프로토콜' 섹션).

두 슬리브를 각자 NAV(펀드)로 보고, 포트폴리오가 목표비중으로 보유·연 1회 리밸런스하는
표준 방식. 세후+파킹은 코어=run_engine(개인세+파킹) 정밀, 위성=연실현 세금 오버레이.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from backtests import tax_parking_backtest as TP  # noqa: E402
from backtests import engine_v9 as E9  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA = ROOT / "data"
ASOF = "2026-07-01"
REBAL_COST = 0.0012
STRESS = [("2000-01-01", "2002-12-31", "닷컴버블"),
          ("2007-10-01", "2009-03-31", "글로벌 금융위기"),
          ("2020-02-01", "2020-06-30", "COVID"),
          ("2022-01-01", "2022-12-31", "2022 약세장")]
ALLOCS = [(1.00, "100/0"), (0.80, "80/20"), (0.70, "70/30"), (0.50, "50/50"), (0.00, "0/100")]


def load(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


def norm(eq):
    return eq / eq.iloc[0]


def year_ends(idx):
    s = pd.Series(idx, index=idx)
    return set(pd.DatetimeIndex(s.groupby(idx.year).last().values))


def blend(core, sat, w_core, cost=REBAL_COST):
    """두 NAV를 목표비중 연 1회 리밸런스로 병합. 반환 포트폴리오 NAV(시작 1.0)."""
    core, sat = norm(core), norm(sat)
    idx = core.index
    ye = year_ends(idx)
    w_sat = 1 - w_core
    V = 1.0
    uc = V * w_core / core.iloc[0]
    us = V * w_sat / sat.iloc[0]
    out = []
    for d in idx:
        vc, vs = uc * core[d], us * sat[d]
        V = vc + vs
        if d in ye and 0 < w_core < 1:
            tgt_c = V * w_core
            turn = abs(tgt_c - vc)
            V -= turn * cost
            uc = V * w_core / core[d]
            us = V * w_sat / sat[d]
        out.append((d, V))
    s = pd.Series(dict(out)); s.index = pd.DatetimeIndex(s.index)
    return s


def aftertax_overlay(nav, base=1e8, fx=1350.0, ded=2.5e6, rate=0.22):
    """연간 실현손익(NAV 연증분 근사)에 개인 양도세를 익년 반영하는 세후 오버레이."""
    v = norm(nav) * base
    yl = v.groupby(v.index.year).last()
    years = sorted(v.index.year.unique())
    tax = {}
    prev = v.iloc[0]
    for y in years:
        gain = yl[y] - prev
        tax[y] = max(0.0, gain - ded) * rate if gain > 0 else 0.0
        prev = yl[y]
    cum, paid, out = 0.0, set(), []
    for d in v.index:
        for y in years:
            if y < d.year and y not in paid:
                cum += tax[y]; paid.add(y)
        out.append(v[d] - cum)
    res = pd.Series(out, index=v.index)
    res.iloc[-1] -= sum(tax[y] for y in years if y not in paid)
    return res / base


def yearly(nav):
    s = norm(nav)
    yl = s.groupby(s.index.year).last()
    out, prev = {}, s.iloc[0]
    for y in sorted(s.index.year.unique()):
        out[y] = yl[y] / prev - 1; prev = yl[y]
    return out


def cagr(nav):
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    return (norm(nav).iloc[-1]) ** (1 / yrs) - 1 if yrs > 0 else 0.0


def build_sleeves(spine):
    qqq = load("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5)
    tqqq = load("TQQQ_SYNTH").loc[:ASOF]
    core = TP.run_engine(tqqq, trend, 1.0, (), opts=TP.Opts("none", False, False, False))[0]
    core_tp = TP.run_engine(tqqq, trend, 1.0, (), opts=TP.Opts("individual", True))[0]
    sats = {}
    for tag, excl in [("위성(SOXL포함)", ()), ("위성(SOXL제외)", ("SOXL",))]:
        assets, meta = E9.load_universe(exclude=excl)
        prices, sig, mom, dd = E9.build_inputs(assets, meta, spine)
        sats[tag] = E9.run_2engine(spine, prices, sig, mom, meta, dd, use_engineB=False)[0]
    return norm(core), norm(core_tp), {k: norm(v) for k, v in sats.items()}


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def main():
    spine = load("TQQQ_SYNTH").loc[:ASOF].index
    core, core_tp, sats = build_sleeves(spine)

    print("# 최종 포트폴리오 배분 백테스트 — 코어(최종 견고안) + 위성(v9 엔진A)\n")
    print("> 코어=최종 견고안(무한매수 개선판) · 위성=v9 엔진A 단독(엔진B 제외) · 연 1회 리밸런스 "
          "· 비용 편도 0.12% · 데이터 `*_SYNTH`(2010이전 합성)\n")

    for sat_tag, sat in sats.items():
        print(f"## 위성 = {sat_tag}\n")
        navs = {lab: blend(core, sat, w) for w, lab in ALLOCS}

        print("### ① 전체구간 & ② 2013~ (CAGR / MDD / 샤프)\n")
        print("| 배분(코어/위성) | 전체 CAGR | 전체 MDD | 샤프 | 2013~ CAGR | 2013~ MDD | 샤프 |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for w, lab in ALLOCS:
            n = navs[lab]
            mf = compute(n); m13 = compute(n.loc["2013-01-01":])
            print(f"| {lab} | {pf(mf['CAGR'])} | {pf(mf['MDD'])} | {mf['Sharpe']:.2f} | "
                  f"{pf(m13['CAGR'])} | {pf(m13['MDD'])} | {m13['Sharpe']:.2f} |")
        print()

        print("### ③ 스트레스 4구간 (총수익 / MDD)\n")
        print("| 배분 | " + " | ".join(n for _, _, n in STRESS) + " |")
        print("|---" * (len(STRESS) + 1) + "|")
        for w, lab in ALLOCS:
            cells = []
            for s, e, _ in STRESS:
                m = compute(navs[lab].loc[s:e])
                cells.append(f"{pf(m['TotalReturn'])} / {pf(m['MDD'])}")
            print(f"| {lab} | " + " | ".join(cells) + " |")
        print()

        print("### ④ 연도별 수익률 (굵게=최악 연도)\n")
        ys = {lab: yearly(navs[lab]) for _, lab in ALLOCS}
        worst = {lab: min(ys[lab], key=ys[lab].get) for _, lab in ALLOCS}
        yrs = sorted(spine.year.unique())
        print("| 연도 | " + " | ".join(lab for _, lab in ALLOCS) + " |")
        print("|---" * (len(ALLOCS) + 1) + "|")
        for y in yrs:
            cells = [f"{y} YTD" if y == 2026 else str(y)]
            for _, lab in ALLOCS:
                v = ys[lab].get(y, float("nan"))
                mark = "**" if worst[lab] == y else ""
                cells.append(f"{mark}{pf(v)}{mark}")
            print("| " + " | ".join(cells) + " |")
        print(f"\n**최악 연도**: " + " · ".join(
            f"{lab} {worst[lab]}({pf(ys[lab][worst[lab]])})" for _, lab in ALLOCS) + "\n")

        # 세후+파킹 (코어 정밀 + 위성 오버레이)
        print("### ⑥ 세후+파킹 (개인 · CAGR)\n")
        sat_at = aftertax_overlay(sat)
        print("| 배분 | 세전 CAGR | 세후+파킹 CAGR | ΔCAGR |")
        print("|---|---:|---:|---:|")
        for w, lab in ALLOCS:
            pre = cagr(navs[lab])
            n_at = blend(core_tp, sat_at, w)
            post = cagr(n_at)
            print(f"| {lab} | {pf(pre)} | {pf(post)} | {(post-pre)*100:+.2f}%p |")
        print()


if __name__ == "__main__":
    main()
