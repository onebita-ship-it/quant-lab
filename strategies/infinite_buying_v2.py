"""라우어 무한매수법 v2 — v1에 세 가지 개선을 추가.

v1 대비 변경점:
  (1) 추세 필터: QQQ 종가가 200일 이동평균 아래면 '새 사이클' 진입 금지.
      보유 중이던 사이클은 기존 규칙(추가매수/익절/소진)대로 그대로 처리.
      → run()에 trend_ok(날짜별 bool 시리즈)를 넘겨 사용. p.use_trend_filter=True일 때만 적용.
  (2) 쿼터손절: 회분 소진 시 전량 매도 대신 보유량을 4일에 걸쳐 1/4씩 분할 매도.
      exhaust_action="quarter" 로 선택. (기존 "sell", "hold" 도 유지)
  (3) 현금비중: 자산의 allocation(1.0/0.75/0.5)만 전략에 투입하고
      나머지는 무수익 현금으로 보유. 평가액 = 유휴현금 + 전략현금 + 주식평가액.

수수료/슬리피지는 매수·매도 양쪽에 반영 (v1과 동일).
사이클 단위 기록을 남겨 승률·사이클 수익률 분포를 계산.
"""
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Params:
    divisions: int = 40
    take_profit_pct: float = 0.15
    exhaust_action: str = "sell"   # "sell" | "hold" | "quarter"
    fee_pct: float = 0.0007        # 편도 수수료 0.07%
    slippage_pct: float = 0.0005   # 편도 슬리피지 0.05%
    initial_capital: float = 40_000.0
    # --- v2 신규 ---
    allocation: float = 1.0        # 전략 투입 비중 (나머지는 유휴 현금)
    use_trend_filter: bool = False # QQQ 200일선 추세 필터 사용 여부
    quarter_days: int = 4          # 쿼터손절 분할 매도 일수


@dataclass
class Cycle:
    start: pd.Timestamp
    end: pd.Timestamp = None
    invested: float = 0.0
    proceeds: float = 0.0
    days: int = 0
    reason: str = ""  # "take_profit" | "exhausted" | "eof"

    @property
    def pnl_pct(self) -> float:
        return self.proceeds / self.invested - 1 if self.invested else 0.0


@dataclass
class Result:
    equity: pd.Series = None
    cycles: list = field(default_factory=list)


def run(close: pd.Series, p: Params, trend_ok: pd.Series | None = None) -> Result:
    """close: 일별 종가. trend_ok: 날짜별 '신규 진입 허용' bool(추세 필터용).

    반환: 일별 평가액(유휴현금 포함)과 사이클 목록.
    """
    buy_cost = 1 + p.fee_pct + p.slippage_pct
    sell_cost = 1 - p.fee_pct - p.slippage_pct

    idle_cash = p.initial_capital * (1.0 - p.allocation)  # 전략에 넣지 않고 현금 보유
    cash = p.initial_capital * p.allocation               # 전략 운용 현금
    shares = 0.0
    invested = 0.0          # 현재 사이클 투입 원가 (수수료 포함)
    buys_done = 0
    one_buy = 0.0
    cycle = None
    cycles: list[Cycle] = []
    equity = []

    # 쿼터손절(분할 청산) 진행 상태
    liquidating = False
    liq_per_day = 0.0
    liq_days_left = 0

    use_trend = p.use_trend_filter and trend_ok is not None

    def eq(price):
        return idle_cash + cash + shares * price

    for date, price in close.items():
        # 1) 익절 판정 (분할청산 중에는 건너뜀 — 이미 청산 스케줄이 돌고 있음)
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
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "exhausted"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
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
                    # 남은 잔량까지 정리하고 사이클 종료
                    if shares > 1e-9:
                        rest = shares * price * sell_cost
                        cash += rest
                        cycle.proceeds += rest
                    shares = 0.0  # 부동소수점 잔량까지 완전 청산
                    cycle.end, cycle.reason = date, "exhausted"
                    cycles.append(cycle)
                    invested, buys_done, cycle = 0.0, 0, None
                    liquidating, liq_per_day, liq_days_left = False, 0.0, 0
                equity.append((date, eq(price)))
                continue
            # "hold"면 매수 없이 익절 대기 (아래로 진행하되 매수는 안 됨)

        # 3) 정규 매수 (1회분). 신규 사이클은 추세 필터 통과 시에만 시작.
        if buys_done < p.divisions and cash > 1e-9:
            if cycle is None:
                allowed = True
                if use_trend:
                    allowed = bool(trend_ok.get(date, True))
                if not allowed:
                    equity.append((date, eq(price)))  # 진입 금지 — 대기
                    continue
                cycle = Cycle(start=date)
                one_buy = cash / p.divisions  # 사이클 시작 시점 전략현금 등분
            spend = min(one_buy, cash)
            qty = spend / (price * buy_cost)
            cash -= spend
            shares += qty
            invested += spend
            buys_done += 1
            cycle.invested = invested
            cycle.days += 1

        equity.append((date, eq(price)))

    # 마지막 미종료 사이클은 종가 청산으로 기록 (통계용)
    if cycle is not None:
        value = shares * close.iloc[-1] * sell_cost
        cycle.end, cycle.reason = close.index[-1], "eof"
        cycle.proceeds += value
        cycles.append(cycle)

    eq_ser = pd.Series(dict(equity)).sort_index()
    eq_ser.index = pd.DatetimeIndex(eq_ser.index)
    return Result(equity=eq_ser, cycles=cycles)
