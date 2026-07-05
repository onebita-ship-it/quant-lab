"""공개 유명 전략 8종 구현 — 우리 최종안(80/20)과 동일 조건 비교.

1 HFEA (UPRO55/TMF45, 분기 리밸런스)
2 Gayed 레버리지 로테이션 (SPY>200MA→UPRO, 아래→TLT)
3 듀얼모멘텀 GEM (US/해외/채권, 12개월 모멘텀, 월 리밸런스)
4 60/40 (SPY60/AGG40, 분기 리밸런스)
5 영구 포트폴리오 (SPY/TLT/GLD/현금 25%씩, 연 리밸런스)
6 정석 무한매수법 v1 (TQQQ, 40분할·익절10%·소진매도)
7 QQQ 단순보유
8 우리 최종안 80/20 (코어+위성)

전부 같은 데이터(1999~2026, `*_SYNTH`)·같은 비용(편도 0.12%)·같은 스파인.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests import portfolio_backtest as PB  # noqa: E402
from strategies import infinite_buying as v1  # noqa: E402

DATA = ROOT / "data"
COST = 0.0012


def load(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


def R(t):  # _SYNTH 우선
    for c in (f"{t}_SYNTH", t):
        if (DATA / f"{c}.csv").exists():
            return load(c)
    raise FileNotFoundError(t)


def norm(s):
    return s / s.iloc[0]


def _periods(spine, freq):
    s = pd.Series(spine, index=spine)
    if freq == "Q":
        key = [spine.year, spine.quarter]
    elif freq == "A":
        key = spine.year
    else:  # M
        key = [spine.year, spine.month]
    return set(pd.DatetimeIndex(s.groupby(key).last().values))


def static_portfolio(weights, prices, spine, freq="Q", cost=COST):
    assets = list(weights)
    nav = {a: norm(prices[a].reindex(spine).ffill()) for a in assets}
    rd = _periods(spine, freq)
    units = {a: weights[a] / nav[a].iloc[0] for a in assets}
    V = 1.0
    out = []
    for d in spine:
        vals = {a: units[a] * nav[a][d] for a in assets}
        V = sum(vals.values())
        if d in rd:
            turn = sum(abs(V * weights[a] - vals[a]) for a in assets) / 2
            V -= turn * cost
            units = {a: V * weights[a] / nav[a][d] for a in assets}
        out.append((d, V))
    s = pd.Series(dict(out)); s.index = pd.DatetimeIndex(s.index)
    return s


def gayed(prices, spine, cost=COST):
    spx = prices["SPY"].reindex(spine).ffill(); ma = spx.rolling(200).mean()
    on = (spx >= ma).ffill().fillna(False).shift(1).fillna(False)
    ur = norm(prices["UPRO"].reindex(spine)).pct_change().fillna(0.0)
    tr = norm(prices["TLT"].reindex(spine)).pct_change().fillna(0.0)
    V, prev, out = 1.0, None, []
    for d in spine:
        want = "UPRO" if bool(on[d]) else "TLT"
        if prev is not None and want != prev:
            V *= (1 - cost)
        V *= (1 + (ur[d] if want == "UPRO" else tr[d]))
        out.append((d, V)); prev = want
    s = pd.Series(dict(out)); s.index = pd.DatetimeIndex(s.index)
    return s


def gem(prices, spine, cost=COST):
    us = norm(prices["SPY"].reindex(spine).ffill())
    intl = norm(prices["EFA"].reindex(spine).ffill())
    tb = norm(prices["SGOV"].reindex(spine).ffill())
    rets = {k: prices[k].reindex(spine).ffill().pct_change().fillna(0.0)
            for k in ["SPY", "EFA", "AGG", "SGOV"]}
    tomap = {"US": "SPY", "INTL": "EFA", "BOND": "AGG"}
    me = _periods(spine, "M")

    def mom(s, d):
        h = s.loc[:d]
        return h.iloc[-1] / h.iloc[-253] - 1 if len(h) > 253 else -9.0

    V, held, out = 1.0, "SGOV", []
    for d in spine:
        V *= (1 + rets[held][d])
        if d in me:
            if mom(us, d) > mom(tb, d):          # 절대 모멘텀(vs T-bill)
                tgt = "SPY" if mom(us, d) >= mom(intl, d) else "EFA"
            else:
                tgt = "AGG"
            if tgt != held:
                V *= (1 - cost); held = tgt
        out.append((d, V))
    s = pd.Series(dict(out)); s.index = pd.DatetimeIndex(s.index)
    return s


def build_all(spine):
    prices = {k: R(k) for k in ["UPRO", "TMF", "SPY", "AGG", "TLT", "GLD", "SGOV", "EFA"]}
    navs = {}
    navs["HFEA (UPRO55/TMF45)"] = static_portfolio({"UPRO": 0.55, "TMF": 0.45}, prices, spine, "Q")
    navs["Gayed 로테이션"] = gayed(prices, spine)
    navs["듀얼모멘텀 GEM"] = gem(prices, spine)
    navs["60/40"] = static_portfolio({"SPY": 0.60, "AGG": 0.40}, prices, spine, "Q")
    navs["영구 포트폴리오"] = static_portfolio(
        {"SPY": 0.25, "TLT": 0.25, "GLD": 0.25, "SGOV": 0.25}, prices, spine, "A")
    navs["정석 무한매수법 v1"] = norm(v1.run(
        R("TQQQ").reindex(spine).ffill(), v1.Params(divisions=40, take_profit_pct=0.10,
                                                    exhaust_action="sell")).equity)
    navs["QQQ 단순보유"] = norm(load("QQQ").reindex(spine))
    core, _, sats = PB.build_sleeves(spine)
    navs["우리 최종안 80/20"] = PB.blend(core, sats["위성(SOXL포함)"], 0.80)
    return {k: norm(v) for k, v in navs.items()}
