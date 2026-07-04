"""무한매수법 v3 — v2의 추세 필터 GFC(급락+베어랠리) 약점 개선.

[v2 문제] 200일선 '단일' 필터는 2008 금융위기처럼 급락과 격렬한 베어마켓 랠리가
교차하는 장에서, 랠리 때 잠깐 200일선 위로 올라온 고점 부근에서만 진입을 허용 →
그 소수 진입이 곧바로 이어진 급락에 전부 물려 승률 0%, v1보다 나쁜 결과.

[v3 개선] 3가지 (모두 옵션):
  (A) MA 기울기 필터  — 진입은 '가격>200MA' 이고 '200MA가 상승 중'(slope>=0)일 때만.
      하락하는 200MA 아래의 브리프 랠리는 조건을 못 채워 진입이 차단된다.
  (B) 진입 확정 스트릭 — 추세 조건이 K거래일 연속 만족해야 진입 (하루짜리 튐 무시).
  (C) 손실 후 재진입 쿨다운 — 사이클이 손실(pnl<=0)로 끝나면 N거래일 신규 진입 금지.
      익절로 끝난 사이클은 쿨다운 없음(다음 기회 즉시 참여).

(A)(B)는 trend_signal_v3()에서 사전 계산해 trend_ok로 주입, (C)는 run() 엔진의 상태로 처리.
쿼터손절(quarter)·현금비중(allocation)은 v2 그대로 계승.
"""
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
    # --- v3 신규 ---
    reentry_cooldown_days: int = 0  # 손실 사이클 종료 후 신규 진입 금지 일수


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


def trend_signal_v3(qqq: pd.Series, target_index, window: int = 200,
                    require_rising: bool = True, slope_lookback: int = 20,
                    confirm_days: int = 1) -> pd.Series:
    """QQQ 기준 '신규 진입 허용' bool 시리즈를 target_index에 맞춰 반환.

    - require_rising: 200MA가 slope_lookback일 전보다 높아야(상승 추세) 진입 허용.
    - confirm_days: 조건이 K일 연속 참이어야 최종 허용 (브리프 랠리 무시).
    MA 계산은 항상 full qqq에서 수행 후 target_index로 reindex(구간 슬라이스 시에도 정확).
    초기 MA 미산출 구간은 진입 허용(True).
    """
    ma = qqq.rolling(window).mean()
    base = qqq.ge(ma)
    if require_rising:
        base = base & ma.ge(ma.shift(slope_lookback))
    if confirm_days > 1:
        base = base.rolling(confirm_days).sum().ge(confirm_days)
    base = base.where(ma.notna(), True)  # warmup 구간은 허용
    return base.reindex(target_index).ffill().fillna(True)


def run(close: pd.Series, p: Params, trend_ok: pd.Series | None = None) -> Result:
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

    block_until = 0  # day_i < block_until 이면 신규 진입 금지(쿨다운)
    use_trend = p.use_trend_filter and trend_ok is not None

    def eq(price):
        return idle_cash + cash + shares * price

    for day_i, (date, price) in enumerate(close.items()):
        # 1) 익절 판정 (분할청산 중 제외)
        if shares > 0 and cycle is not None and not liquidating:
            value = shares * price * sell_cost
            if value >= invested * (1 + p.take_profit_pct):
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "take_profit"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
                equity.append((date, eq(price)))
                continue  # 익절 후엔 쿨다운 없음

        # 2) 회분 소진 처리
        if cycle and buys_done >= p.divisions:
            if p.exhaust_action == "sell":
                value = shares * price * sell_cost
                pnl = value / invested - 1 if invested else 0.0
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "exhausted"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
                if pnl <= 0:
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
                    if pnl <= 0:
                        block_until = day_i + p.reentry_cooldown_days
                equity.append((date, eq(price)))
                continue
            # "hold"면 매수 없이 익절 대기

        # 3) 정규 매수. 신규 사이클은 추세 필터 통과 + 쿨다운 해제 시에만 시작.
        if buys_done < p.divisions and cash > 1e-9:
            if cycle is None:
                allowed = True
                if use_trend:
                    allowed = bool(trend_ok.get(date, True))
                if allowed and day_i < block_until:
                    allowed = False  # 손실 후 재진입 쿨다운 중
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
