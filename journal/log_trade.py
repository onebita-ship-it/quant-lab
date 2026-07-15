"""체결 기록 — 규칙 준수 여부 + 슬리피지를 자동 대조하고 상태를 갱신한다.

사용:
  # 정규 매수 (진행 중 사이클)
  python journal/log_trade.py --action buy --shares 0.53 --price 75.10
  # 새 사이클 첫 매수
  python journal/log_trade.py --action buy --shares 0.53 --price 75.10 --new-cycle
  # 익절(전량 매도)
  python journal/log_trade.py --action take_profit --shares 21.4 --price 92.30
  # 쿼터손절 1일분
  python journal/log_trade.py --action quarter --shares 5.35 --price 40.10
  # 리저브 편입(고점대비 -30%/-50% 발동 시)
  python journal/log_trade.py --action deploy_reserve
  # 계좌 연 1회 리밸런스(42.5/42.5/15 복원) 기록 — 룰북 ⑧
  python journal/log_trade.py --action rebalance

옵션: --date YYYY-MM-DD(기본 최신 거래일), --fee 실제수수료$(기본 가정치), --dry(미저장)
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal import common as C  # noqa: E402

TRADE_COLS = ["date", "action", "shares", "price", "ref_close", "slippage_bps",
              "cash_flow", "cycle_id", "compliant", "checks", "note"]


def ref_close(tqqq, date):
    import pandas as pd
    if date is None:
        return float(tqqq.iloc[-1]), tqqq.index[-1].date()
    d = pd.Timestamp(date)
    s = tqqq.loc[:d]
    if len(s) == 0:
        return float(tqqq.iloc[-1]), tqqq.index[-1].date()
    return float(s.iloc[-1]), s.index[-1].date()


def append_trade(row):
    exists = C.TRADES_PATH.exists()
    with open(C.TRADES_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLS)
        if not exists:
            w.writeheader()
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", required=True,
                    choices=["buy", "take_profit", "quarter", "deploy_reserve", "rebalance"])
    ap.add_argument("--shares", type=float, default=0.0)
    ap.add_argument("--price", type=float, default=0.0)
    ap.add_argument("--date", default=None)
    ap.add_argument("--fee", type=float, default=None, help="실제 수수료$ (기본: 가정치)")
    ap.add_argument("--new-cycle", action="store_true")
    ap.add_argument("--dry", action="store_true", help="상태 저장 안 함")
    args = ap.parse_args()

    cfg = C.load_config()
    st = C.load_state(cfg)
    qqq = C.load_price(cfg["signal_ticker"])
    tqqq = C.load_price(cfg["trade_ticker"])
    rc, rdate = ref_close(tqqq, args.date)
    date_str = str(args.date or rdate)
    div, tp = cfg["divisions"], cfg["take_profit_pct"]
    checks, notes = {}, []

    # ---- 계좌 연 1회 리밸런스 (룰북 ⑧) ----
    if args.action == "rebalance":
        prev = st.get("last_rebalance") or st.get("inception_date")
        st["last_rebalance"] = date_str
        alloc = cfg.get("allocation", {"core": 0.425, "satellite": 0.425, "gold": 0.15})
        notes.append(f"계좌 연례 리밸런스 → 목표 배분 복원 "
                     f"코어 {alloc['core']:.1%}/위성 {alloc['satellite']:.1%}/"
                     f"금 {alloc['gold']:.1%}"
                     + (f" (직전 기준일 {prev})" if prev else " (첫 리밸런스 기록)"))
        row = {"date": date_str, "action": "rebalance", "shares": 0, "price": 0,
               "ref_close": round(rc, 4), "slippage_bps": "", "cash_flow": 0,
               "cycle_id": st["current_cycle_id"], "compliant": True,
               "checks": "", "note": " / ".join(notes)}
        _finish(st, cfg, row, args.dry, rc)
        return

    # ---- 리저브 편입 ----
    if args.action == "deploy_reserve":
        eq = C.equity(st, rc)
        dd = eq / st["peak_equity"] - 1 if st["peak_equity"] else 0.0
        fired = False
        for thr in cfg["reserve_triggers"]:
            key = f"{int(thr*100)}%"
            if key not in st["reserve_tiers_fired"] and st["reserve"] > 1e-6 and dd <= thr:
                inject = round(st["reserve"] * 0.5, 2)
                st["cash"] += inject
                st["reserve"] -= inject
                st["reserve_tiers_fired"].append(key)
                notes.append(f"리저브 {key} 발동 → {C.fmt_won(inject)} 운용현금 편입")
                fired = True
                break
        checks["리저브발동조건(고점대비)"] = fired
        row = {"date": date_str, "action": "deploy_reserve", "shares": 0, "price": 0,
               "ref_close": round(rc, 4), "slippage_bps": "", "cash_flow": 0,
               "cycle_id": st["current_cycle_id"], "compliant": fired,
               "checks": ";".join(f"{k}={v}" for k, v in checks.items()),
               "note": " / ".join(notes) if notes else "발동 조건 미충족(변경 없음)"}
        _finish(st, cfg, row, args.dry, rc)
        return

    fee = args.fee if args.fee is not None else round(args.shares * args.price * cfg["fee_pct"], 2)
    slip_bps = (args.price - rc) / rc * 10000 if rc else 0.0

    if args.action == "buy":
        # 계좌 개시일 기록(최초 매수 1회) — 연례 리밸런스·금 매수 기준일(룰북 ⑧)
        if not st.get("inception_date"):
            st["inception_date"] = date_str
            notes.append(f"📌 계좌 개시일 기록: {date_str} (연례 리밸런스 시계 시작)")
        # 진입/금액 준수 판정
        if args.new_cycle:
            sig = C.latest_signal(qqq, cfg, asof=args.date)
            checks["신규진입_추세충족"] = sig["entry_ok"]
            if not sig["entry_ok"]:
                notes.append("⚠️ 추세 미충족인데 새 사이클 진입 — 규칙 위반")
            st["cycle_active"] = True
            st["cycle_start"] = date_str
            st["cycle_seq"] += 1
            st["current_cycle_id"] = st["cycle_seq"]
            st["cycle_proceeds"] = 0.0
            st["one_buy"] = round(st["cash"] / div, 2)
            st["buys_done"] = 0
        prescribed = st["one_buy"]
        spend = args.shares * args.price + fee
        checks["1회분금액_일치(±5%)"] = abs(spend - prescribed) <= 0.05 * prescribed if prescribed else False
        checks["회차한도_분할내"] = st["buys_done"] < div
        if st["buys_done"] >= div:
            notes.append("⚠️ 이미 40회분 소진 — 추가 매수는 규칙상 없음")
        st["cash"] -= spend
        st["shares"] += args.shares
        st["invested"] += spend
        st["buys_done"] += 1
        cash_flow = -spend
        notes.append(f"매수 {args.shares}주 @{args.price} (지출 {C.fmt_won(spend)}, "
                     f"지정 1회분 {C.fmt_won(prescribed)})")

    elif args.action == "take_profit":
        val_at_fill = args.shares * args.price * C.sell_cost(cfg)
        checks["익절조건(+{:.0%})".format(tp)] = val_at_fill >= st["invested"] * (1 + tp)
        checks["전량매도"] = abs(args.shares - st["shares"]) <= max(1e-6, 0.01 * st["shares"])
        proceeds = args.shares * args.price - fee
        st["cash"] += proceeds
        st["cycle_proceeds"] += proceeds
        st["shares"] -= args.shares
        pnl = st["cycle_proceeds"] / st["invested"] - 1 if st["invested"] else 0.0
        _close_cycle(st, date_str, "take_profit", pnl)
        cash_flow = proceeds
        notes.append(f"익절 전량매도 → 사이클 손익 {pnl:+.1%}")

    elif args.action == "quarter":
        checks["소진후청산(40회분)"] = st["buys_done"] >= div
        if not st["liquidating"]:
            st["liquidating"] = True
            st["liq_per_day"] = round(st["shares"] / cfg["quarter_days"], 6)
            st["liq_left"] = cfg["quarter_days"]
        checks["1/4수량_일치(±5%)"] = abs(args.shares - st["liq_per_day"]) <= 0.05 * st["liq_per_day"] if st["liq_per_day"] else False
        proceeds = args.shares * args.price - fee
        st["cash"] += proceeds
        st["cycle_proceeds"] += proceeds
        st["shares"] -= args.shares
        st["liq_left"] -= 1
        cash_flow = proceeds
        if st["liq_left"] <= 0 or st["shares"] <= 1e-6:
            pnl = st["cycle_proceeds"] / st["invested"] - 1 if st["invested"] else 0.0
            _close_cycle(st, date_str, "exhausted", pnl)
            notes.append(f"쿼터손절 청산 완료 → 사이클 손익 {pnl:+.1%}")
        else:
            notes.append(f"쿼터손절 {args.shares}주 매도 (남은 {st['liq_left']}일)")

    compliant = all(v for v in checks.values())
    if abs(slip_bps) > 50:
        notes.append(f"⚠️ 슬리피지 과다 {slip_bps:+.0f}bp")
    row = {"date": date_str, "action": args.action, "shares": args.shares, "price": args.price,
           "ref_close": round(rc, 4), "slippage_bps": round(slip_bps, 1),
           "cash_flow": round(cash_flow, 2), "cycle_id": st["current_cycle_id"],
           "compliant": compliant,
           "checks": ";".join(f"{k}={v}" for k, v in checks.items()),
           "note": " / ".join(notes)}
    _finish(st, cfg, row, args.dry, rc)


def _close_cycle(st, date_str, reason, pnl):
    st["cycles_closed"].append({
        "id": st["current_cycle_id"], "start": st["cycle_start"], "end": date_str,
        "invested": round(st["invested"], 2), "proceeds": round(st["cycle_proceeds"], 2),
        "pnl_pct": round(pnl, 4), "reason": reason,
    })
    st["shares"] = 0.0
    st["invested"] = 0.0
    st["buys_done"] = 0
    st["one_buy"] = 0.0
    st["cycle_active"] = False
    st["cycle_proceeds"] = 0.0
    st["liquidating"] = False
    st["liq_left"] = 0
    st["liq_per_day"] = 0.0


def _finish(st, cfg, row, dry, price):
    # 고점 갱신
    eq = C.equity(st, price)
    st["peak_equity"] = round(max(st["peak_equity"], eq), 2)
    print("─" * 58)
    print(f" 기록: {row['action']}  {row['date']}")
    for kv in row["checks"].split(";"):
        if kv:
            k, v = kv.rsplit("=", 1)
            print(f"   {'✅' if v == 'True' else '❌'} {k}")
    if row["slippage_bps"] != "":
        print(f"   슬리피지: {row['slippage_bps']:+} bp (기준 종가 {row['ref_close']})")
    print(f"   준수: {'✅ 준수' if row['compliant'] else '❌ 위반 확인 요망'}")
    print(f"   메모: {row['note']}")
    print(f"   → 총자산 {C.fmt_won(eq)} (현금 {C.fmt_won(st['cash'])}, "
          f"보유 {st['shares']:.4f}주, 리저브 {C.fmt_won(st['reserve'])})")
    if dry:
        print("   [dry] 저장 안 함")
        return
    append_trade(row)
    C.save_state(st)
    print("   [저장됨] state.json, trades.csv")


if __name__ == "__main__":
    main()
