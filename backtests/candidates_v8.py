"""v8 — 새 목표(연 20%+, 하루 10분, 국면 나쁘면 중단, 폭락 시 추가투입)용 전략 후보 5종.

전부 '일봉 종가 신호'만 사용(하루 10분 제약). 신호 = QQQ 200일선 기울기+스트릭5 ON/OFF.

  S1 베이스라인   : 현 최종 견고안(무한매수 40분할·익절15%·쿼터손절).
  S2 추세 스위칭  : ON=TQQQ 전량 / OFF=SGOV. 익절 없음. 전환수·전환당 손익(휩쏘) 집계.
  S3 하이브리드   : ON 동안 40분할 매수(부드럽게), 익절 대신 'OFF 전환 시 전량 청산'(상방 캡 제거).
  S4 로테이션     : 월말 6개월 모멘텀 1위 보유(TQQQ/UPRO/SOXL/SGOV). 추세 OFF면 무조건 SGOV.
  S5 숏 검증      : S2에서 OFF 구간을 SGOV 대신 'SQQQ 25% + SGOV 75%'로. 숏이 더해지는지 판정.

비용: 편도 수수료 0.07% + 슬리피지 0.05%(전환 왕복시 양다리 부과). 현금(SGOV)은 자체 수익률.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from backtests import tax_parking_backtest as TP  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA = ROOT / "data"
FEE, SLIP = 0.0007, 0.0005
BUY_C, SELL_C = 1 + FEE + SLIP, 1 - FEE - SLIP
MOM_LOOKBACK = 126   # 6개월
STRESS = [("2000-01-01", "2002-12-31", "닷컴버블"),
          ("2007-10-01", "2009-03-31", "글로벌 금융위기"),
          ("2020-02-01", "2020-06-30", "COVID"),
          ("2022-01-01", "2022-12-31", "2022 약세장")]


def load(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


# ------------------------------------------------------------------ 전략들
def s1_baseline(prices, trend):
    eq, cyc, _ = TP.run_engine(prices["TQQQ"], trend, 1.00, (),
                               opts=TP.Opts("none", False, False, False))
    return eq, {"cycles": cyc}


def _switch(V, ret):
    return V * (1 + ret)


def s2_trend_switch(prices, trend, off_assets=(("SGOV", 1.0),)):
    """ON=TQQQ / OFF=off_assets(자산,비중). 반환 equity + 전환 통계.

    룩어헤드 방지: 그날 종가 신호는 '내일'의 보유를 정한다. 오늘 수익은 '오늘 진입 시점에
    들고 있던' 자산으로 적용한 뒤, 종가에 신호를 보고 전환한다(S3와 동일 규약).
    """
    idx = prices["TQQQ"].index
    rets = {k: prices[k].pct_change().fillna(0.0) for k in prices}
    V = 1.0
    held = None            # None / 'TQQQ' / 'OFF'
    eq = []
    entry_V = None; stints = []      # ON 구간(스틴트) 손익
    switches = 0
    for d in idx:
        # 1) 오늘 진입 시 들고 있던 자산으로 오늘 수익 적용
        if held == "TQQQ":
            V = _switch(V, rets["TQQQ"].get(d, 0.0))
        elif held == "OFF":
            V = _switch(V, sum(w * rets[a].get(d, 0.0) for a, w in off_assets))
        # 2) 종가 신호 → 내일 보유 결정
        want = "TQQQ" if bool(trend.get(d, False)) else "OFF"
        if held is None:
            V *= 1.0 / BUY_C
            held = want
            if want == "TQQQ":
                entry_V = V
        elif want != held:
            V *= SELL_C / BUY_C
            switches += 1
            if held == "TQQQ" and entry_V is not None:
                stints.append(V / entry_V - 1)
                entry_V = None
            held = want
            if want == "TQQQ":
                entry_V = V
        eq.append((d, V))
    if held == "TQQQ" and entry_V is not None:
        stints.append(V / entry_V - 1)
    e = pd.Series(dict(eq)); e.index = pd.DatetimeIndex(e.index)
    st = np.array(stints) if stints else np.array([0.0])
    info = {"switches": switches, "stints": len(stints),
            "whipsaw_rate": float(np.mean(st < 0)), "avg_stint": float(np.mean(st)),
            "worst_stint": float(np.min(st)), "median_stint": float(np.median(st))}
    return e, info


def s3_hybrid(prices, trend):
    """ON 동안 40분할 매수, OFF 전환 시 전량 청산. 미투입/OFF 현금은 SGOV."""
    tqqq = prices["TQQQ"]; sgov_r = prices["SGOV"].pct_change().fillna(0.0)
    tq_r = tqqq.pct_change().fillna(0.0)
    idx = tqqq.index
    cash = 1.0; tq_val = 0.0     # 포지션 평가액(비율단위)
    buys = 0; one_buy = 0.0
    on_prev = False
    eq = []
    cyc = []; cyc_start = None; cyc_cost = 0.0
    for d in idx:
        on = bool(trend.get(d, False))
        # 일일 마킹
        cash *= (1 + sgov_r.get(d, 0.0))
        tq_val *= (1 + tq_r.get(d, 0.0))
        if on:
            if not on_prev:      # 새 ON 구간 시작
                buys = 0; one_buy = cash / 40.0; cyc_start = d; cyc_cost = 0.0
            if buys < 40 and cash > 1e-12:
                spend = min(one_buy, cash)
                cash -= spend
                tq_val += spend / BUY_C     # 매수비용
                cyc_cost += spend
                buys += 1
        else:
            if on_prev and tq_val > 1e-12:   # OFF 전환 → 전량 청산
                cash += tq_val * SELL_C
                if cyc_start is not None:
                    cyc.append(_Cyc(cyc_start, d, cyc_cost))
                tq_val = 0.0
        eq.append((d, cash + tq_val))
        on_prev = on
    if tq_val > 1e-12 and cyc_start is not None:
        cyc.append(_Cyc(cyc_start, idx[-1], cyc_cost))
    e = pd.Series(dict(eq)); e.index = pd.DatetimeIndex(e.index)
    return e, {"episodes": len(cyc)}


class _Cyc:
    def __init__(self, s, e, cost):
        self.start, self.end, self.cost = s, e, cost


def s4_rotation(prices, trend):
    """월말 6개월 모멘텀 1위 보유. 추세 OFF면 SGOV. 후보=TQQQ/UPRO/SOXL/SGOV."""
    cand = ["TQQQ", "UPRO", "SOXL", "SGOV"]
    idx = prices["TQQQ"].index
    rets = {k: prices[k].pct_change().fillna(0.0) for k in cand}
    # 월말 리밸런싱일
    month_end = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).last().values
    month_end = set(pd.DatetimeIndex(month_end))
    V = 1.0; held = None; eq = []; picks = []
    for d in idx:
        # 1) 오늘 진입 시 보유 자산으로 오늘 수익 적용(월말 결정은 내일부터 반영)
        if held is not None:
            V = _switch(V, rets[held].get(d, 0.0))
        # 2) 월말 종가: 모멘텀·신호로 내일 보유 결정
        if d in month_end:
            on = bool(trend.get(d, False))
            if not on:
                target = "SGOV"
            else:
                best, bestm = "SGOV", -1e9
                for a in cand:
                    hist = prices[a].loc[:d]
                    if len(hist) <= MOM_LOOKBACK or np.isnan(hist.iloc[-1]):
                        continue
                    m = hist.iloc[-1] / hist.iloc[-1 - MOM_LOOKBACK] - 1
                    if m > bestm:
                        bestm, best = m, a
                target = best
            if held is None:
                V *= 1.0 / BUY_C; held = target
            elif target != held:
                V *= SELL_C / BUY_C; held = target
            picks.append(target)
        eq.append((d, V))
    e = pd.Series(dict(eq)); e.index = pd.DatetimeIndex(e.index)
    from collections import Counter
    return e, {"picks": dict(Counter(picks))}


def s5_short(prices, trend):
    return s2_trend_switch(prices, trend, off_assets=(("SQQQ", 0.25), ("SGOV", 0.75)))


# ------------------------------------------------------------------ 지표
def on_annualized(eq, trend):
    """신호 ON 구간만의 연환산 수익률(투입 중 기대수익)."""
    r = eq.pct_change().fillna(0.0)
    mask = pd.Series([bool(trend.get(d, False)) for d in eq.index], index=eq.index)
    on_r = r[mask]
    n = len(on_r)
    if n < 2:
        return float("nan")
    growth = float((1 + on_r).prod())
    return growth ** (252.0 / n) - 1


def yearly(eq):
    s = eq.sort_index()
    yl = s.groupby(s.index.year).last()
    out = {}; prev = s.iloc[0]
    for y in sorted(s.index.year.unique()):
        out[y] = yl[y] / prev - 1; prev = yl[y]
    return out


def seg_metrics(eq, s=None, e=None):
    sub = eq.loc[s:e] if (s or e) else eq
    return compute(sub)


def cagr(eq):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
