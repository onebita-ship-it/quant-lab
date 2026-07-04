"""v3 추세 필터 개선 검증 — v1 / v2 단순필터 / v3 개선안 비교 + 스트레스 테스트.

핵심 질문: v3의 (A)기울기필터 (B)확정스트릭 (C)손실후쿨다운 이
GFC(2007-10~2009-03)의 v2 약점(승률 0%, v1보다 나쁨)을 실제로 고치는가?
그러면서 닷컴·전체구간 성과는 지키는가?

모든 추세형 변형은 분할40/익절15%/쿼터손절 고정(v2 권장 소진처리와 동일).
마크다운 표를 stdout으로 출력.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying as v1  # noqa: E402
from strategies import infinite_buying_v3 as v3  # noqa: E402

DATA_DIR = ROOT / "data"


def load_close(ticker: str) -> pd.Series:
    df = pd.read_csv(DATA_DIR / f"{ticker}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def base_v3(**kw):
    return v3.Params(divisions=40, take_profit_pct=0.15, exhaust_action="quarter",
                     use_trend_filter=True, **kw)


# 라벨, 신호종류('none'|'simple'|'slope'|'slope_streak'), Params, 엔진
def build_variants():
    return [
        ("v1 기준 (추세X, sell, 100%)",
         "none", v1.Params(divisions=40, take_profit_pct=0.15, exhaust_action="sell"), v1),
        ("v2 단순 200MA (100%)",
         "simple", base_v3(reentry_cooldown_days=0), v3),
        ("v3 +기울기필터 (100%)",
         "slope", base_v3(reentry_cooldown_days=0), v3),
        ("v3 +기울기+확정스트릭5 (100%)",
         "slope_streak", base_v3(reentry_cooldown_days=0), v3),
        ("v3 풀개선 +쿨다운20 (100%)",
         "slope_streak", base_v3(reentry_cooldown_days=20), v3),
        ("v3 풀개선 +현금75%",
         "slope_streak", base_v3(reentry_cooldown_days=20, allocation=0.75), v3),
    ]


def make_signal(kind, qqq, index):
    if kind == "none":
        return None
    if kind == "simple":
        return v3.trend_signal_v3(qqq, index, require_rising=False, confirm_days=1)
    if kind == "slope":
        return v3.trend_signal_v3(qqq, index, require_rising=True, confirm_days=1)
    if kind == "slope_streak":
        return v3.trend_signal_v3(qqq, index, require_rising=True, confirm_days=5)
    raise ValueError(kind)


def run_variant(engine, p, kind, close, qqq):
    if engine is v1:
        res = engine.run(close, p)
    else:
        res = engine.run(close, p, trend_ok=make_signal(kind, qqq, close.index))
    return compute(res.equity, res.cycles)


def pct(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def table(close, qqq, cols_full=True, title=None):
    if title:
        print(title)
    if cols_full:
        print("| 전략 변형 | CAGR | MDD | 샤프 | 총수익 | 승률 | 사이클수 | 최장하락(일) |")
        print("|-----------|-----:|----:|-----:|-------:|-----:|--------:|------------:|")
    else:
        print("| 전략 변형 | 총수익 | MDD | 샤프 | 승률 | 사이클수 |")
        print("|-----------|-------:|----:|-----:|-----:|--------:|")
    for label, kind, p, engine in build_variants():
        m = run_variant(engine, p, kind, close, qqq)
        wr = pct(m.get("WinRate", float("nan")))
        if cols_full:
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
    table(close, qqq, cols_full=True)

    for start, end, name in [("2000-01-01", "2002-12-31", "닷컴버블"),
                             ("2007-10-01", "2009-03-31", "글로벌 금융위기")]:
        sl = close.loc[start:end]
        print(f"\n## {name} ({sl.index[0].date()} ~ {sl.index[-1].date()}, {len(sl)}일)\n")
        table(sl, qqq, cols_full=False)


if __name__ == "__main__":
    main()
