"""v4 검증 — 쿨다운을 '하락 국면에서만' 켜면 v3의 CAGR 붕괴를 피하면서 방어를 유지하는가?

비교(모두 분할40/익절15%/쿼터손절/기울기+스트릭5 추세필터):
  - v1 기준(추세X)
  - v3 무쿨다운               (추천 상시 설정)
  - v3 상시쿨다운20           (CAGR 붕괴판)
  - v4 하락시만쿨다운20        (신규 개선)
  - v4 하락시만쿨다운20 +현금75%

전체구간 + 닷컴(2000-2002) + GFC(2007-2009).
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying as v1  # noqa: E402
from strategies import infinite_buying_v4 as v4  # noqa: E402

DATA_DIR = ROOT / "data"


def load_close(ticker: str) -> pd.Series:
    df = pd.read_csv(DATA_DIR / f"{ticker}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def base(**kw):
    return v4.Params(divisions=40, take_profit_pct=0.15, exhaust_action="quarter",
                     use_trend_filter=True, **kw)


# 라벨, 엔진, Params, (쿨다운 하락한정 여부)
def build_variants():
    return [
        ("v1 기준 (추세X, sell, 100%)", v1,
         v1.Params(divisions=40, take_profit_pct=0.15, exhaust_action="sell")),
        ("v3 무쿨다운 (100%)", v4,
         base(reentry_cooldown_days=0)),
        ("v3 상시쿨다운20 (100%)", v4,
         base(reentry_cooldown_days=20, cooldown_when_down_only=False)),
        ("v4 하락시만쿨다운20 (100%)", v4,
         base(reentry_cooldown_days=20, cooldown_when_down_only=True)),
        ("v4 하락시만쿨다운20 +현금75%", v4,
         base(reentry_cooldown_days=20, cooldown_when_down_only=True, allocation=0.75)),
    ]


def run_variant(engine, p, close, qqq):
    if engine is v1:
        res = engine.run(close, p)
    else:
        trend = v4.trend_signal_v4(qqq, close.index, require_rising=True, confirm_days=5)
        down = v4.regime_down_signal(qqq, close.index, fast=50, slow=200)
        res = engine.run(close, p, trend_ok=trend, regime_down=down)
    return compute(res.equity, res.cycles)


def pct(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def table(close, qqq, full=True):
    if full:
        print("| 전략 변형 | CAGR | MDD | 샤프 | 총수익 | 승률 | 사이클수 | 최장하락(일) |")
        print("|-----------|-----:|----:|-----:|-------:|-----:|--------:|------------:|")
    else:
        print("| 전략 변형 | 총수익 | MDD | 샤프 | 승률 | 사이클수 |")
        print("|-----------|-------:|----:|-----:|-----:|--------:|")
    for label, engine, p in build_variants():
        m = run_variant(engine, p, close, qqq)
        wr = pct(m.get("WinRate", float("nan")))
        if full:
            print(f"| {label} | {pct(m['CAGR'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
                  f"{pct(m['TotalReturn'])} | {wr} | {m.get('Cycles','-')} | "
                  f"{m['LongestUnderwaterDays']} |")
        else:
            print(f"| {label} | {pct(m['TotalReturn'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
                  f"{wr} | {m.get('Cycles','-')} |")


def main():
    close = load_close("TQQQ_SYNTH")
    qqq = load_close("QQQ")
    print(f"## 전체구간 ({close.index[0].date()} ~ {close.index[-1].date()})\n")
    table(close, qqq, full=True)
    for start, end, name in [("2000-01-01", "2002-12-31", "닷컴버블"),
                             ("2007-10-01", "2009-03-31", "글로벌 금융위기")]:
        sl = close.loc[start:end]
        print(f"\n## {name} ({sl.index[0].date()} ~ {sl.index[-1].date()}, {len(sl)}일)\n")
        table(sl, qqq, full=False)


if __name__ == "__main__":
    main()
