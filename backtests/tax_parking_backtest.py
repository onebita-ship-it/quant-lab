"""세후 + 파킹 백테스트 — 최종 견고안(100% / 67%+리저브)을 세금·환전·배당세·파킹 반영해 비교.

추가 반영 옵션(확정 룰북 위에 얹는 '실전 비용/현금관리' 레이어):

  ① 양도소득세(개인)  : 매년 12월 말 실현손익 통산 → 250만원(≈$X, 환율 가정) 공제 →
                        초과분 22%를 **다음 해 5월** 현금에서 차감. 손실 해는 세금 0(이월 없음, 보수적).
  ② 법인 모드          : 공제 없이 (양수)실현손익에 19% → **다음 해 3월** 차감.
  ③ 환전수수료(0.15%)  : 시작 시(운용투입분)과 리저브 투입 시 각각 차감.
  ④ 배당세(연 0.15%)   : 수정종가에 배당 재투자가 반영돼 있으므로 원천징수분만 일할 차감(보유평가액 기준).
  ⑤ 파킹 수익(연 3.4%) : 사이클 미진행 현금·미사용 회분·리저브에 세후 SGOV 수익률을 일할 적용(ON/OFF).

세금은 KRW 기준(250만원 공제)이라 환율 가정이 필요 → FX_RATE로 실현손익을 KRW 환산해 계산.
전략은 최종 견고안: 기울기+스트릭5 · 분할40 · 익절15% · 쿼터손절 · 쿨다운/변동성필터 OFF.

사용:
  python backtests/tax_parking_backtest.py            # 전체구간 표 → 콘솔
"""
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA_DIR = ROOT / "data"

FEE, SLIP = 0.0007, 0.0005
DIVISIONS, TP, QDAYS = 40, 0.15, 4

# --- 비용/세금 파라미터 ---
FX_RATE = 1350.0              # KRW/USD (250만원 공제 환산용 가정)
DEDUCT_USD = 2_500_000 / FX_RATE   # 개인 양도세 기본공제 ≈ $1,851.85
IND_RATE = 0.22               # 개인 양도소득세율(지방세 포함)
CORP_RATE = 0.19              # 법인세율(과표구간 단순 가정)
FX_FEE = 0.0015               # 환전수수료 편도 0.15%
DIV_TAX_ANNUAL = 0.0015       # 배당 원천징수 연 0.15% 일할
PARK_ANNUAL = 0.034           # 파킹(세후 SGOV) 연 3.4% 일할


@dataclass
class Opts:
    tax_mode: str = "none"    # "none" | "individual" | "corporate"
    parking: bool = False
    fx_fee: bool = True
    div_tax: bool = True


def load_close(t):
    df = pd.read_csv(DATA_DIR / f"{t}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def tax_on(gain_usd, mode):
    """연간 실현손익(USD) → 세액(USD). 손실 해는 0."""
    if mode == "individual":
        gain_krw = gain_usd * FX_RATE
        taxable = max(0.0, gain_krw - 2_500_000)
        return taxable * IND_RATE / FX_RATE
    if mode == "corporate":
        return max(0.0, gain_usd) * CORP_RATE
    return 0.0


def run_engine(close, trend_ok, deploy_frac=1.0, triggers=(), total=40_000.0, opts=Opts()):
    """최종 견고안 + 세금/환전/배당세/파킹. 반환: (equity, cycles, info)."""
    buy_c, sell_c = 1 + FEE + SLIP, 1 - FEE - SLIP
    park_daily = PARK_ANNUAL / 252.0
    div_daily = DIV_TAX_ANNUAL / 252.0

    # 시작 환전수수료: 운용투입분에만(리저브는 투입 시 부과)
    fx_paid = 0.0
    deployed = total * deploy_frac
    reserve = total * (1.0 - deploy_frac)
    if opts.fx_fee:
        fx_cut = deployed * FX_FEE
        deployed -= fx_cut
        fx_paid += fx_cut
    cash = deployed

    shares = invested = one_buy = 0.0
    buys = 0
    cycle = None
    cycles, equity, deploy_dates = [], [], []
    liq = False; liq_per = 0.0; liq_left = 0
    peak = -1.0
    fired = [False] * len(triggers)

    realized_by_year = {}     # 연도 → 실현손익(USD, cycle proceeds-invested)
    pending = []              # {pay_year, pay_month, amount, paid}
    cum_tax = parking_earned = div_tax_paid = 0.0
    cur_year = close.index[0].year
    pay_month = 5 if opts.tax_mode == "individual" else 3

    def finalize_year(y):
        gain = realized_by_year.get(y, 0.0)
        amt = tax_on(gain, opts.tax_mode)
        pending.append({"pay_year": y + 1, "pay_month": pay_month, "amount": amt, "paid": False})

    # 세금은 순자산(net worth)에서 나가는 외생 유출로 모델링 → 트레이딩 현금흐름을 왜곡하지
    # 않도록 '누적 세금(cum_tax)'을 세전 총자산에서 차감해 세후 곡선을 만든다. eqrec(date, price)
    # 로 모든 시점의 세후 총자산을 기록.
    def eqval(price):
        return reserve + cash + shares * price - cum_tax

    for date, price in close.items():
        # 0) 연도 롤오버 → 직전 연도 세액 확정
        if date.year != cur_year:
            for y in range(cur_year, date.year):
                finalize_year(y)
            cur_year = date.year

        # 0b) 납부기일 도래 세금 → 누적 세금에 반영(순자산 차감)
        if opts.tax_mode != "none":
            for pt in pending:
                if pt["paid"]:
                    continue
                due = (date.year > pt["pay_year"]) or \
                      (date.year == pt["pay_year"] and date.month >= pt["pay_month"])
                if due:
                    cum_tax += pt["amount"]
                    pt["paid"] = True

        # 1) 리저브 편입 판정
        tot_eq = reserve + cash + shares * price
        peak = max(peak, tot_eq)
        dd = tot_eq / peak - 1 if peak > 0 else 0.0
        for k, thr in enumerate(triggers):
            if not fired[k] and reserve > 1e-9 and dd <= thr:
                inject = reserve * 0.5
                if opts.fx_fee:
                    fx_cut = inject * FX_FEE
                    inject -= fx_cut
                    fx_paid += fx_cut
                cash += inject
                reserve -= reserve * 0.5   # 원래 절반만큼 리저브에서 차감(환전료는 손실)
                fired[k] = True
                deploy_dates.append((date, thr, inject))

        # 2) 익절
        if shares > 0 and cycle is not None and not liq:
            val = shares * price * sell_c
            if val >= invested * (1 + TP):
                cash += val
                cycle.end, cycle.proceeds, cycle.reason = date, val, "take_profit"
                cycles.append(cycle)
                realized_by_year[date.year] = realized_by_year.get(date.year, 0.0) + (val - invested)
                shares = invested = 0.0; buys = 0; cycle = None
                cash, reserve, parking_earned, div_tax_paid = _accrue(
                    cash, reserve, shares, price, park_daily, div_daily,
                    opts, parking_earned, div_tax_paid)
                equity.append((date, eqval(price))); continue

        # 3) 소진 → 쿼터손절
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
                realized_by_year[date.year] = realized_by_year.get(date.year, 0.0) + \
                    (cycle.proceeds - invested)
                invested = 0.0; buys = 0; cycle = None
                liq = False; liq_per = 0.0; liq_left = 0
            cash, reserve, parking_earned, div_tax_paid = _accrue(
                cash, reserve, shares, price, park_daily, div_daily,
                opts, parking_earned, div_tax_paid)
            equity.append((date, eqval(price))); continue

        # 4) 매수
        if buys < DIVISIONS and cash > 1e-9:
            if cycle is None:
                if not bool(trend_ok.get(date, True)):
                    cash, reserve, parking_earned, div_tax_paid = _accrue(
                        cash, reserve, shares, price, park_daily, div_daily,
                        opts, parking_earned, div_tax_paid)
                    equity.append((date, eqval(price))); continue
                cycle = v6.Cycle(start=date)
                one_buy = cash / DIVISIONS
            spend = min(one_buy, cash)
            qty = spend / (price * buy_c)
            cash -= spend; shares += qty; invested += spend; buys += 1
            cycle.invested = invested; cycle.days += 1

        cash, reserve, parking_earned, div_tax_paid = _accrue(
            cash, reserve, shares, price, park_daily, div_daily,
            opts, parking_earned, div_tax_paid)
        equity.append((date, eqval(price)))

    # EOF: 잔여 사이클 청산 + 남은 연도 세금 확정 후 최종 잔액에서 차감(미래 부채 반영)
    if cycle is not None:
        val = shares * close.iloc[-1] * sell_c
        cycle.end, cycle.reason = close.index[-1], "eof"
        cycle.proceeds += val
        cash += val
        cycles.append(cycle)
        realized_by_year[close.index[-1].year] = \
            realized_by_year.get(close.index[-1].year, 0.0) + (cycle.proceeds - invested)
        shares = 0.0

    # 남은(미확정) 연도 세금을 확정하고 미납분 전액을 최종 순자산에서 차감(미래 세부채 반영)
    if opts.tax_mode != "none":
        for y in sorted(realized_by_year):
            if not any(pt["pay_year"] == y + 1 for pt in pending):
                finalize_year(y)
        cum_tax += sum(pt["amount"] for pt in pending if not pt["paid"])
        for pt in pending:
            pt["paid"] = True

    eq = pd.Series(dict(equity)).sort_index(); eq.index = pd.DatetimeIndex(eq.index)
    # 마지막 포인트: EOF 청산 + 세부채 완납 반영한 세후 순자산
    eq.iloc[-1] = reserve + cash - cum_tax
    info = {"tax_paid": cum_tax, "parking_earned": parking_earned,
            "div_tax_paid": div_tax_paid, "fx_paid": fx_paid, "deploy_dates": deploy_dates}
    return eq, cycles, info


def _accrue(cash, reserve, shares, price, park_daily, div_daily, opts, park_acc, div_acc):
    """일할 파킹수익(현금+리저브) 가산 · 배당세(보유평가액) 차감."""
    if opts.parking:
        earn = (cash + reserve) * park_daily
        cash += earn
        park_acc += earn
    if opts.div_tax and shares > 0:
        d = shares * price * div_daily
        cash -= d
        div_acc += d
    return cash, reserve, park_acc, div_acc


def trend_of(qqq, index):
    return v6.trend_signal_v6(qqq, index, require_rising=True, confirm_days=5)


CONFIGS = [("100% 투입", 1.00, ()), ("67% + 리저브", 0.67, (-0.30, -0.50))]

SCENARIOS = [
    ("세전 (베이스)", Opts(tax_mode="none", parking=False, fx_fee=False, div_tax=False)),
    ("비용만(환전+배당세)", Opts(tax_mode="none", parking=False, fx_fee=True, div_tax=True)),
    ("세후 개인 · 파킹OFF", Opts(tax_mode="individual", parking=False)),
    ("세후 개인 · 파킹ON", Opts(tax_mode="individual", parking=True)),
    ("세후 법인 · 파킹OFF", Opts(tax_mode="corporate", parking=False)),
    ("세후 법인 · 파킹ON", Opts(tax_mode="corporate", parking=True)),
]


def pctf(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def main():
    tqqq = load_close("TQQQ_SYNTH"); qqq = load_close("QQQ")
    trend = trend_of(qqq, tqqq.index)

    print(f"# 세후+파킹 백테스트 (TQQQ_SYNTH 1999~2026, 환율 {FX_RATE:.0f} KRW/USD 가정)\n")
    base = {}
    for cfg_label, frac, trig in CONFIGS:
        print(f"## {cfg_label}\n")
        print("| 시나리오 | CAGR | ΔCAGR(세전대비) | MDD | 샤프 | 총수익 | 세금누계 | 파킹누계 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|")
        for sc_label, opts in SCENARIOS:
            eq, cyc, info = run_engine(tqqq, trend, frac, trig, opts=opts)
            m = compute(eq, cyc)
            key = (cfg_label, sc_label)
            base[key] = m["CAGR"]
            base_cagr = base[(cfg_label, "세전 (베이스)")]
            dcagr = m["CAGR"] - base_cagr
            print(f"| {sc_label} | {pctf(m['CAGR'])} | {dcagr*100:+.2f}%p | {pctf(m['MDD'])} | "
                  f"{m['Sharpe']:.2f} | {pctf(m['TotalReturn'])} | "
                  f"${info['tax_paid']:,.0f} | ${info['parking_earned']:,.0f} |")
        print()

    # 요약: 세금이 깎고 파킹이 돌려주는 %p
    print("## 세금·파킹 순효과 요약 (CAGR %p)\n")
    print("| 구성 | 세금 효과 | 파킹 효과 | 순효과 |")
    print("|---|---:|---:|---:|")
    for cfg_label, _, _ in CONFIGS:
        pre = base[(cfg_label, "비용만(환전+배당세)")]
        tax_off = base[(cfg_label, "세후 개인 · 파킹OFF")]
        tax_on_park = base[(cfg_label, "세후 개인 · 파킹ON")]
        tax_eff = (tax_off - pre) * 100
        park_eff = (tax_on_park - tax_off) * 100
        net = (tax_on_park - pre) * 100
        print(f"| {cfg_label}(개인) | {tax_eff:+.2f}%p | {park_eff:+.2f}%p | {net:+.2f}%p |")
    print()


if __name__ == "__main__":
    main()
