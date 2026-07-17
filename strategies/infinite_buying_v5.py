"""무한매수법 v5 — v1.0 최종 견고안에 '변동성 필터' 추가.

[동기] 추세 필터(가격>200MA·200MA 상승)는 하락장 진입은 막지만, 시장 천장의 '분산(distribution)'
국면 — 가격은 아직 상승 200선 위인데 변동성이 급등하는 구간(2000·2007·2021 고점) — 은 놓친다.
변동성 필터가 이 조기 경고 지대를 덮어 추세 필터를 보완할 수 있는지 본다.

[변동성 필터] QQQ의 window일 실현변동성이 '자기 자신의 최근 ref_window일 max_percentile'을 초과하면
신규 사이클 진입 금지. 절대 임계값 대신 적응형 백분위를 써서 국면·기간에 걸쳐 과최적화를 줄인다.
보유 중이던 사이클은 기존 규칙대로 처리(진입만 통제).

(A)기울기·(B)스트릭·쿼터손절·현금비중·(하락한정)쿨다운은 v4 그대로 계승.
vol_ok는 trend_ok와 동일하게 사전계산해 엔진에 주입하며, 신규 진입은 둘 다 통과해야 허용된다.
"""
from __future__ import annotations  # py3.9 호환(어노테이션 지연평가, 동작 불변)

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Params:
    divisions: int = 40
    take_profit_pct: float = 0.15
    exhaust_action: str = "sell"   # "sell" | "hold" | "quarter"
    fee_pct: float = 0.0007
    slippage_pct: float = 0.0005
    initial_capital: float = 40_000.0
    allocation: float = 1.0
    use_trend_filter: bool = False
    quarter_days: int = 4
    reentry_cooldown_days: int = 0
    cooldown_when_down_only: bool = True
    use_vol_filter: bool = False    # v5: 변동성 필터 사용 여부


@dataclass
class Cycle:
    start: pd.Timestamp
    end: pd.Timestamp = None
    invested: float = 0.0
    proceeds: float = 0.0
    days: int = 0
    reason: str = ""

    @property
    def pnl_pct(self) -> float:
        return self.proceeds / self.invested - 1 if self.invested else 0.0


@dataclass
class Result:
    equity: pd.Series = None
    cycles: list = field(default_factory=list)


def trend_signal_v5(qqq: pd.Series, target_index, window: int = 200,
                    require_rising: bool = True, slope_lookback: int = 20,
                    confirm_days: int = 1) -> pd.Series:
    """신규 진입 허용(추세 필터). v3/v4와 동일 로직."""
    ma = qqq.rolling(window).mean()
    base = qqq.ge(ma)
    if require_rising:
        base = base & ma.ge(ma.shift(slope_lookback))
    if confirm_days > 1:
        base = base.rolling(confirm_days).sum().ge(confirm_days)
    base = base.where(ma.notna(), True)
    return base.reindex(target_index).ffill().fillna(True)


def regime_down_signal(qqq: pd.Series, target_index,
                       fast: int = 50, slow: int = 200) -> pd.Series:
    """하락 국면(데드크로스) 플래그. v4와 동일."""
    ma_f = qqq.rolling(fast).mean()
    ma_s = qqq.rolling(slow).mean()
    down = ma_f.lt(ma_s)
    down = down.where(ma_s.notna(), False)
    return down.reindex(target_index).ffill().fillna(False)


def vol_filter_signal(qqq: pd.Series, target_index, window: int = 20,
                      ref_window: int = 252, max_percentile: float = 0.80) -> pd.Series:
    """변동성 진입 허용 플래그: window일 실현변동성이 최근 ref_window일의 max_percentile
    이하일 때만 True(진입 허용). 초과(고변동성)면 False(진입 금지).
    ref_window 미충족 워밍업 구간은 허용(True).
    """
    vol = qqq.pct_change().rolling(window).std()
    thresh = vol.rolling(ref_window).quantile(max_percentile)
    ok = vol.le(thresh)
    ok = ok.where(thresh.notna(), True)
    return ok.reindex(target_index).ffill().fillna(True)


def run(close: pd.Series, p: Params, trend_ok: pd.Series | None = None,
        regime_down: pd.Series | None = None, vol_ok: pd.Series | None = None) -> Result:
    buy_cost = 1 + p.fee_pct + p.slippage_pct
    sell_cost = 1 - p.fee_pct - p.slippage_pct

    idle_cash = p.initial_capital * (1.0 - p.allocation)
    cash = p.initial_capital * p.allocation
    shares = 0.0
    invested = 0.0
    buys_done = 0
    one_buy = 0.0
    cycle = None
    cycles: list[Cycle] = []
    equity = []

    liquidating = False
    liq_per_day = 0.0
    liq_days_left = 0

    block_until = 0
    use_trend = p.use_trend_filter and trend_ok is not None
    use_vol = p.use_vol_filter and vol_ok is not None

    def eq(price):
        return idle_cash + cash + shares * price

    def cooldown_active(date) -> bool:
        if p.reentry_cooldown_days <= 0:
            return False
        if not p.cooldown_when_down_only:
            return True
        if regime_down is None:
            return False
        return bool(regime_down.get(date, False))

    for day_i, (date, price) in enumerate(close.items()):
        # 1) 익절 판정
        if shares > 0 and cycle is not None and not liquidating:
            value = shares * price * sell_cost
            if value >= invested * (1 + p.take_profit_pct):
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "take_profit"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
                equity.append((date, eq(price)))
                continue

        # 2) 회분 소진 처리
        if cycle and buys_done >= p.divisions:
            if p.exhaust_action == "sell":
                value = shares * price * sell_cost
                pnl = value / invested - 1 if invested else 0.0
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "exhausted"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
                if pnl <= 0 and cooldown_active(date):
                    block_until = day_i + p.reentry_cooldown_days
                equity.append((date, eq(price)))
                continue

            if p.exhaust_action == "quarter":
                if not liquidating:
                    liquidating = True
                    liq_per_day = shares / p.quarter_days
                    liq_days_left = p.quarter_days
                sell_shares = min(liq_per_day, shares)
                proceeds = sell_shares * price * sell_cost
                cash += proceeds
                shares -= sell_shares
                cycle.proceeds += proceeds
                liq_days_left -= 1
                if liq_days_left <= 0 or shares <= 1e-9:
                    if shares > 1e-9:
                        rest = shares * price * sell_cost
                        cash += rest
                        cycle.proceeds += rest
                    shares = 0.0
                    cycle.end, cycle.reason = date, "exhausted"
                    pnl = cycle.pnl_pct
                    cycles.append(cycle)
                    invested, buys_done, cycle = 0.0, 0, None
                    liquidating, liq_per_day, liq_days_left = False, 0.0, 0
                    if pnl <= 0 and cooldown_active(date):
                        block_until = day_i + p.reentry_cooldown_days
                equity.append((date, eq(price)))
                continue
            # "hold"면 매수 없이 익절 대기

        # 3) 정규 매수. 신규 사이클은 추세+변동성 필터 통과 & 쿨다운 해제 시에만 시작.
        if buys_done < p.divisions and cash > 1e-9:
            if cycle is None:
                allowed = True
                if use_trend:
                    allowed = bool(trend_ok.get(date, True))
                if allowed and use_vol:
                    allowed = bool(vol_ok.get(date, True))
                if allowed and day_i < block_until:
                    allowed = False
                if not allowed:
                    equity.append((date, eq(price)))
                    continue
                cycle = Cycle(start=date)
                one_buy = cash / p.divisions
            spend = min(one_buy, cash)
            qty = spend / (price * buy_cost)
            cash -= spend
            shares += qty
            invested += spend
            buys_done += 1
            cycle.invested = invested
            cycle.days += 1

        equity.append((date, eq(price)))

    if cycle is not None:
        value = shares * close.iloc[-1] * sell_cost
        cycle.end, cycle.reason = close.index[-1], "eof"
        cycle.proceeds += value
        cycles.append(cycle)

    eq_ser = pd.Series(dict(equity)).sort_index()
    eq_ser.index = pd.DatetimeIndex(eq_ser.index)
    return Result(equity=eq_ser, cycles=cycles)
