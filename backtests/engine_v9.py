"""v9 — 2엔진 통합 전략 (재량 유니버스 기반).

유니버스: config/universe.txt (사람이 분기마다 관리, 티커별 core/satellite + 원지수 표시).

엔진 A (추세+모멘텀 로테이션):
  유니버스 중 '각자 원지수 추세신호(200일선 기울기+스트릭5)가 ON'인 자산만 후보로,
  3·6개월 블렌드 모멘텀 1위를 월말 리밸런스로 보유. ON 자산이 없으면 SGOV.
  → 새 티커를 유니버스에 넣어도 그 원지수 신호가 ON 되기 전엔 매수 안 됨(구조적 게이트).

엔진 B (코어 폭락 계단투입):
  코어 자산의 원지수가 최근1년 고점 대비 -30/-40/-50% 도달 시, SGOV로 대기 중인 현금을
  3등분 계단 투입(각 티어 QQQ 절반 + TQQQ 절반). 어느 자산이든 추세신호가 재점등하면
  엔진 B 보유분을 청산하고 엔진 A로 복귀.

비용: 편도 수수료 0.07% + 슬리피지 0.05%. 현금은 SGOV 수익률로 이자.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA = ROOT / "data"
CONFIG = ROOT / "config" / "universe.txt"
FEE, SLIP = 0.0007, 0.0005
BUY_C, SELL_C = 1 + FEE + SLIP, 1 - FEE - SLIP
CRASH_TIERS = (-0.30, -0.40, -0.50)


def load(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


def resolve_price(ticker):
    for cand in (f"{ticker}_SYNTH", ticker):
        if (DATA / f"{cand}.csv").exists():
            return load(cand)
    raise FileNotFoundError(ticker)


def load_universe(path=CONFIG, exclude=()):
    """(assets, meta) 반환. meta[ticker]={class, index}."""
    assets, meta = [], {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        tk, cls, idx = parts[0], parts[1], parts[2]
        if tk in exclude:
            continue
        assets.append(tk)
        meta[tk] = {"class": cls, "index": idx}
    return assets, meta


def trailing_dd(index_series, spine, window=252):
    roll = index_series.rolling(window, min_periods=1).max()
    dd = index_series / roll - 1
    return dd.reindex(spine).ffill()


def blended_mom(price, short=63, long=126):
    return 0.5 * (price / price.shift(short) - 1) + 0.5 * (price / price.shift(long) - 1)


def month_ends(spine):
    s = pd.Series(spine, index=spine)
    return set(pd.DatetimeIndex(s.groupby([spine.year, spine.month]).last().values))


def run_2engine(spine, prices, signals, moms, meta, core_dd,
                use_engineB=True, reverse_mom=False):
    """2엔진 통합 백테스트. 반환 (equity, info)."""
    assets = list(meta)
    me = month_ends(spine)
    sgov_r = prices["SGOV"].pct_change().fillna(0.0)
    cash = 1.0
    pos = {}                      # {asset: units}
    held = None                   # 엔진A 현재 보유 자산
    mode = None                   # 'A' | 'B'
    bear_pool = None
    tiers = set()
    eq = []
    b_deploys = 0
    picks = []
    hlog = []                     # 일별 보유 라벨 (엔진A 자산 | 'B' | 'SGOV')

    def price_at(a, d):
        v = prices[a].get(d, np.nan)
        return v

    def liquidate(d):
        nonlocal cash, pos
        for a, u in list(pos.items()):
            p = price_at(a, d)
            if u > 0 and not np.isnan(p):
                cash += u * p * SELL_C
        pos = {}

    def buy_all(a, d):
        nonlocal cash, pos
        p = price_at(a, d)
        if np.isnan(p) or cash <= 0:
            return
        pos[a] = pos.get(a, 0.0) + cash / (p * BUY_C)
        cash = 0.0

    for d in spine:
        cash *= (1 + sgov_r.get(d, 0.0))     # 현금(SGOV) 이자
        on = [a for a in assets if bool(signals[a].get(d, False))
              and not np.isnan(moms[a].get(d, np.nan))]
        any_on = len(on) > 0

        if any_on:
            if mode == "B":               # 폭락 국면 → 재점등: 엔진B 청산
                liquidate(d)
                held = None
            mode = "A"; bear_pool = None; tiers = set()
            need_rebal = (d in me) or (held not in on)
            if need_rebal:
                scores = {a: moms[a].get(d, -1e9) for a in on}
                target = (min if reverse_mom else max)(scores, key=scores.get)
                if target != held:
                    liquidate(d)
                    buy_all(target, d)
                    held = target
                picks.append(target)
        else:
            mode = "B"
            if use_engineB:
                if bear_pool is None:      # 베어 진입: 엔진A 청산 → 현금 풀
                    liquidate(d); held = None
                    bear_pool = cash
                dd = core_dd.get(d, 0.0)
                for t in CRASH_TIERS:
                    if t not in tiers and dd <= t and cash > 1e-9:
                        spend = min(bear_pool / 3.0, cash)
                        half = spend / 2.0
                        for a in ("QQQ", "TQQQ"):
                            p = price_at(a, d)
                            if not np.isnan(p):
                                pos[a] = pos.get(a, 0.0) + half / (p * BUY_C)
                        cash -= spend
                        tiers.add(t); b_deploys += 1
            else:                          # 엔진A 단독: 그냥 SGOV
                if pos:
                    liquidate(d); held = None

        v = cash + sum(u * price_at(a, d) for a, u in pos.items()
                       if not np.isnan(price_at(a, d)))
        eq.append((d, v))
        hlog.append((d, (held if held and pos else ("B" if pos else "SGOV"))))

    e = pd.Series(dict(eq)); e.index = pd.DatetimeIndex(e.index)
    held_s = pd.Series(dict(hlog)); held_s.index = pd.DatetimeIndex(held_s.index)
    from collections import Counter
    return e, {"engineB_deploys": b_deploys, "picks": dict(Counter(picks)),
               "held": held_s}


def build_inputs(assets, meta, spine, mom_short=63, mom_long=126,
                 slope_lookback=20, confirm_days=5):
    """자산별 가격·신호·모멘텀 + 코어 원지수 낙폭."""
    prices = {a: resolve_price(a).reindex(spine) for a in assets}
    prices["SGOV"] = resolve_price("SGOV").reindex(spine)
    prices["QQQ"] = load("QQQ").reindex(spine)
    prices["TQQQ"] = resolve_price("TQQQ").reindex(spine)
    idx_cache = {}
    signals, moms = {}, {}
    for a in assets:
        idxname = meta[a]["index"]
        if idxname not in idx_cache:
            idx_cache[idxname] = load(idxname)
        sig = v6.trend_signal_v6(idx_cache[idxname], spine, require_rising=True,
                                 slope_lookback=slope_lookback, confirm_days=confirm_days)
        signals[a] = sig
        moms[a] = blended_mom(prices[a], mom_short, mom_long)
    # 코어 원지수 낙폭(최근1년 고점) 중 최악
    core_idx = [meta[a]["index"] for a in assets if meta[a]["class"] == "core"]
    dds = [trailing_dd(load(ix), spine) for ix in set(core_idx)]
    core_dd = pd.concat(dds, axis=1).min(axis=1) if dds else pd.Series(0.0, index=spine)
    return prices, signals, moms, core_dd
