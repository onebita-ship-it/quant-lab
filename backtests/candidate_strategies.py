"""후보 전략 6종(+변형) — Gayed LRS · HFEA · VAA · ADM · GEM · 영구 포트폴리오.

우리 80/20 · QQQ 단순보유와 동일 조건(1999~2026, 편도 0.12%, `*_SYNTH`) 비교.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtests.benchmark_strategies as B  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

COST = B.COST


def norm(s):
    return B.norm(s)


# ---- 모멘텀 지표 ----
def w13612(p, d):
    h = p.loc[:d]
    if len(h) < 253:
        return -9.0
    r1 = h.iloc[-1] / h.iloc[-22] - 1; r3 = h.iloc[-1] / h.iloc[-64] - 1
    r6 = h.iloc[-1] / h.iloc[-127] - 1; r12 = h.iloc[-1] / h.iloc[-253] - 1
    return 12 * r1 + 4 * r3 + 2 * r6 + r12


def adm_score(p, d):
    h = p.loc[:d]
    if len(h) < 127:
        return -9.0
    return (h.iloc[-1] / h.iloc[-22] - 1 + h.iloc[-1] / h.iloc[-64] - 1 +
            h.iloc[-1] / h.iloc[-127] - 1) / 3.0


def r1m(p, d):
    h = p.loc[:d]
    return h.iloc[-1] / h.iloc[-22] - 1 if len(h) > 22 else -9.0


def mom12(p, d):
    h = p.loc[:d]
    return h.iloc[-1] / h.iloc[-253] - 1 if len(h) > 253 else -9.0


def monthly_rotation(spine, prices, select_fn, cost=COST):
    """월말 select_fn(d)→티커 보유. 반환 (NAV, turnover_연간전환수)."""
    rets = {k: prices[k].reindex(spine).ffill().pct_change().fillna(0.0) for k in prices}
    me = B._periods(spine, "M")
    V, held, out, switches = 1.0, None, [], 0
    for d in spine:
        if held is not None:
            V *= (1 + rets[held][d])
        if d in me:
            tgt = select_fn(d)
            if tgt != held:
                if held is not None:
                    V *= (1 - cost)
                switches += 1
                held = tgt
        out.append((d, V))
    s = pd.Series(dict(out)); s.index = pd.DatetimeIndex(s.index)
    years = (spine[-1] - spine[0]).days / 365.25
    return s, switches / years


# ---- Gayed LRS ----
def gayed_lrs(spine, prices, use_our_filter=False, cost=COST):
    spx = prices["SPY"].reindex(spine).ffill()
    if use_our_filter:
        on = v6.trend_signal_v6(spx, spine, require_rising=True, slope_lookback=20, confirm_days=5)
        on = on.reindex(spine).astype(bool)
    else:
        ma = spx.rolling(200).mean()
        on = (spx >= ma).ffill().fillna(False)
    on = on.shift(1).fillna(False)
    ur = norm(prices["UPRO"].reindex(spine)).pct_change().fillna(0.0)
    sr = norm(prices["SGOV"].reindex(spine)).pct_change().fillna(0.0)
    V, prev, out, sw = 1.0, None, [], 0
    for d in spine:
        want = "UPRO" if bool(on[d]) else "SGOV"
        if prev is not None and want != prev:
            V *= (1 - cost); sw += 1
        V *= (1 + (ur[d] if want == "UPRO" else sr[d]))
        out.append((d, V)); prev = want
    s = pd.Series(dict(out)); s.index = pd.DatetimeIndex(s.index)
    return s, sw / ((spine[-1] - spine[0]).days / 365.25)


# ---- VAA ----
def vaa(spine, prices, cost=COST):
    off = ["SPY", "EFA", "EEM", "AGG"]; deff = ["LQD", "IEF", "SHY"]

    def sel(d):
        os = {a: w13612(prices[a], d) for a in off}
        if all(v > 0 for v in os.values()):
            return max(os, key=os.get)
        ds = {a: w13612(prices[a], d) for a in deff}
        return max(ds, key=ds.get)
    return monthly_rotation(spine, {k: prices[k] for k in off + deff}, sel, cost)


# ---- ADM ----
def adm(spine, prices, leverage=False, cost=COST):
    sub = "UPRO" if leverage else "SPY"        # SPY→UPRO 치환
    dfn = "TMF" if leverage else "TLT"         # TLT→TMF 치환
    pool = {sub: prices[sub], "SCZ": prices["SCZ"], dfn: prices[dfn], "TIP": prices["TIP"]}

    def sel(d):
        a_spy, a_scz = adm_score(prices["SPY"], d), adm_score(prices["SCZ"], d)
        if max(a_spy, a_scz) > 0:
            return sub if a_spy >= a_scz else "SCZ"
        return dfn if r1m(prices["TLT"], d) >= r1m(prices["TIP"], d) else "TIP"
    return monthly_rotation(spine, pool, sel, cost)


# ---- GEM ----
def gem(spine, prices, cost=COST):
    pool = {"SPY": prices["SPY"], "VEU": prices["VEU"], "AGG": prices["AGG"]}

    def sel(d):
        if mom12(prices["SPY"], d) > mom12(prices["SGOV"], d):
            return "SPY" if mom12(prices["SPY"], d) >= mom12(prices["VEU"], d) else "VEU"
        return "AGG"
    return monthly_rotation(spine, pool, sel, cost)


def build_all(spine):
    keys = ["SPY", "UPRO", "TMF", "TLT", "AGG", "GLD", "SGOV", "EFA", "EEM", "LQD",
            "IEF", "SHY", "SCZ", "TIP", "VEU"]
    P = {k: B.R(k) for k in keys}
    out = {}
    out["Gayed LRS (200MA)"] = gayed_lrs(spine, P, use_our_filter=False)
    out["Gayed LRS (우리 기울기+스트릭5)"] = gayed_lrs(spine, P, use_our_filter=True)
    out["HFEA (UPRO55/TMF45)"] = (B.static_portfolio({"UPRO": .55, "TMF": .45}, P, spine, "Q"), 4.0)
    out["VAA (13612W)"] = vaa(spine, P)
    out["ADM (원전)"] = adm(spine, P, leverage=False)
    out["ADM (3배 치환)"] = adm(spine, P, leverage=True)
    out["GEM"] = gem(spine, P)
    out["영구 포트폴리오"] = (B.static_portfolio(
        {"SPY": .25, "TLT": .25, "GLD": .25, "SGOV": .25}, P, spine, "A"), 1.0)
    core, _, sats = PB.build_sleeves(spine)
    out["우리 최종안 80/20"] = (PB.blend(core, sats["위성(SOXL포함)"], 0.80), 1.0)
    out["QQQ 단순보유"] = (norm(B.load("QQQ").reindex(spine)), 0.0)
    return {k: (norm(v[0]), v[1]) for k, v in out.items()}
