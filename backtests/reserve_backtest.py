"""최종 견고안 확정 스위트 (2) — 리저브(예비현금) 편입 규칙 비교.

규칙: 초기자본의 deploy_frac(50%/67%)만 전략에 투입하고 나머지는 리저브(현금)로 보유.
      포트폴리오 총자산이 고점(HWM) 대비 -30% / -50% 도달 시, 그 시점 **리저브의 절반씩**을
      전략 운용현금으로 추가 투입(dry powder 물타기). 발동 날짜를 기록한다.

비교군: 100%(리저브 없음) / 50% 정적 / 50%+리저브 / 67% 정적 / 67%+리저브.
전략은 최종 견고안(기울기+스트릭5·분할40·익절15%·쿼터손절, 쿨다운·vol OFF).
전 구간 + 스트레스(닷컴/GFC/2022). 신호는 QQQ에서 계산.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA_DIR = ROOT / "data"

FEE, SLIP = 0.0007, 0.0005
DIVISIONS, TP, QDAYS = 40, 0.15, 4


def load_close(t):
    df = pd.read_csv(DATA_DIR / f"{t}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def run_reserve(close, trend_ok, deploy_frac=1.0, triggers=(), total=40_000.0):
    """최종 견고안 + 리저브 편입. 반환: (equity, cycles, deploy_dates)."""
    buy_c, sell_c = 1 + FEE + SLIP, 1 - FEE - SLIP
    cash = total * deploy_frac
    reserve = total * (1.0 - deploy_frac)
    shares = invested = one_buy = 0.0
    buys = 0
    cycle = None
    cycles, equity, deploy_dates = [], [], []
    liq = False; liq_per = 0.0; liq_left = 0
    peak = -1.0
    fired = [False] * len(triggers)

    for date, price in close.items():
        # 리저브 편입 판정 (총자산 HWM 대비 낙폭)
        tot_eq = reserve + cash + shares * price
        peak = max(peak, tot_eq)
        dd = tot_eq / peak - 1 if peak > 0 else 0.0
        for k, thr in enumerate(triggers):
            if not fired[k] and reserve > 1e-9 and dd <= thr:
                inject = reserve * 0.5           # 리저브 절반씩
                cash += inject; reserve -= inject
                fired[k] = True
                deploy_dates.append((date, thr, inject))

        # 1) 익절
        if shares > 0 and cycle is not None and not liq:
            val = shares * price * sell_c
            if val >= invested * (1 + TP):
                cash += val
                cycle.end, cycle.proceeds, cycle.reason = date, val, "take_profit"
                cycles.append(cycle)
                shares = invested = 0.0; buys = 0; cycle = None
                equity.append((date, reserve + cash + shares * price)); continue

        # 2) 소진 → 쿼터손절
        if cycle and buys >= DIVISIONS:
            if not liq:
                liq = True; liq_per = shares / QDAYS; liq_left = QDAYS
            sell_sh = min(liq_per, shares)
            cash += sell_sh * price * sell_c
            shares -= sell_sh
            cycle.proceeds += sell_sh * price * sell_c
            liq_left -= 1
            if liq_left <= 0 or shares <= 1e-9:
                if shares > 1e-9:
                    cash += shares * price * sell_c
                    cycle.proceeds += shares * price * sell_c
                shares = 0.0
                cycle.end, cycle.reason = date, "exhausted"
                cycles.append(cycle)
                invested = 0.0; buys = 0; cycle = None
                liq = False; liq_per = 0.0; liq_left = 0
            equity.append((date, reserve + cash + shares * price)); continue

        # 3) 매수 (신규는 추세 필터 통과 시)
        if buys < DIVISIONS and cash > 1e-9:
            if cycle is None:
                if not bool(trend_ok.get(date, True)):
                    equity.append((date, reserve + cash + shares * price)); continue
                cycle = v6.Cycle(start=date)
                one_buy = cash / DIVISIONS
            spend = min(one_buy, cash)
            qty = spend / (price * buy_c)
            cash -= spend; shares += qty; invested += spend; buys += 1
            cycle.invested = invested; cycle.days += 1

        equity.append((date, reserve + cash + shares * price))

    if cycle is not None:
        cycle.end, cycle.reason = close.index[-1], "eof"
        cycle.proceeds += shares * close.iloc[-1] * sell_c
        cycles.append(cycle)
    eq = pd.Series(dict(equity)).sort_index(); eq.index = pd.DatetimeIndex(eq.index)
    return eq, cycles, deploy_dates


def trend_of(qqq, index):
    return v6.trend_signal_v6(qqq, index, require_rising=True, confirm_days=5)


CONFIGS = [
    ("100% (리저브 없음)", 1.00, ()),
    ("50% 정적 (리저브 미투입)", 0.50, ()),
    ("50% + 리저브(-30/-50%)", 0.50, (-0.30, -0.50)),
    ("67% 정적 (리저브 미투입)", 0.67, ()),
    ("67% + 리저브(-30/-50%)", 0.67, (-0.30, -0.50)),
]


def pctf(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def main():
    tqqq = load_close("TQQQ_SYNTH"); qqq = load_close("QQQ")

    print("# 4. 리저브 백테스트 (TQQQ_SYNTH, 최종 견고안)\n")
    print("## 전체구간 (1999~2026)\n")
    print("| 구성 | CAGR | MDD | 샤프 | 총수익 | 리저브 발동 |")
    print("|---|---:|---:|---:|---:|---|")
    full_deploys = {}
    for label, frac, trig in CONFIGS:
        eq, cyc, dep = run_reserve(tqqq, trend_of(qqq, tqqq.index), frac, trig)
        m = compute(eq, cyc)
        dd = "; ".join(f"{d.date()}({int(t*100)}%)" for d, t, _ in dep) if dep else "—"
        full_deploys[label] = dep
        print(f"| {label} | {pctf(m['CAGR'])} | {pctf(m['MDD'])} | {m['Sharpe']:.2f} | "
              f"{pctf(m['TotalReturn'])} | {dd} |")

    print("\n## 스트레스 구간 (총수익 / MDD, 리저브 발동일)\n")
    for s, e, name in [("2000-01-01", "2002-12-31", "닷컴버블"),
                       ("2007-10-01", "2009-03-31", "글로벌 금융위기"),
                       ("2022-01-01", "2022-12-31", "2022 약세장")]:
        sl = tqqq.loc[s:e]
        print(f"### {name} ({sl.index[0].date()} ~ {sl.index[-1].date()})\n")
        print("| 구성 | 총수익 | MDD | 리저브 발동 |")
        print("|---|---:|---:|---|")
        for label, frac, trig in CONFIGS:
            eq, cyc, dep = run_reserve(sl, trend_of(qqq, sl.index), frac, trig)
            m = compute(eq, cyc)
            dd = "; ".join(f"{d.date()}({int(t*100)}%)" for d, t, _ in dep) if dep else "—"
            print(f"| {label} | {pctf(m['TotalReturn'])} | {pctf(m['MDD'])} | {dd} |")
        print()


if __name__ == "__main__":
    main()
