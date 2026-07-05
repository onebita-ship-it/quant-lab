"""월말 요약 — 규칙 준수율 · 추적오차(실행 드리프트) · 성과.

사용:
  python journal/monthly_report.py                # trades.csv의 최신 달
  python journal/monthly_report.py --month 2020-03
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal import common as C  # noqa: E402


def load_trades():
    if not C.TRADES_PATH.exists():
        return []
    with open(C.TRADES_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_f(x, d=0.0):
    try:
        return float(x)
    except (ValueError, TypeError):
        return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=None, help="YYYY-MM (기본: 최신 달)")
    args = ap.parse_args()

    cfg = C.load_config()
    st = C.load_state(cfg)
    trades = load_trades()
    if not trades:
        print("기록된 체결이 없습니다 (journal/trades.csv). log_trade.py로 먼저 기록하세요.")
        return

    months = sorted({t["date"][:7] for t in trades})
    month = args.month or months[-1]
    rows = [t for t in trades if t["date"].startswith(month)]
    if not rows:
        print(f"{month}에 기록된 체결이 없습니다. (존재하는 달: {', '.join(months)})")
        return

    print("=" * 60)
    print(f" 월말 리포트  |  {month}")
    print("=" * 60)

    # --- 1. 규칙 준수율 ---
    total = len(rows)
    compliant = sum(1 for t in rows if t["compliant"] == "True")
    violations = [t for t in rows if t["compliant"] != "True"]
    print(f"\n[1. 규칙 준수율]  {compliant}/{total} = {compliant/total:.0%}")
    if violations:
        print("  위반 내역:")
        for t in violations:
            print(f"   - {t['date']} {t['action']}: {t['checks']} | {t['note']}")
    else:
        print("  ✅ 전 건 규칙 준수")

    # --- 2. 추적오차(실행 드리프트) ---
    slip_bps = [to_f(t["slippage_bps"]) for t in rows if t["slippage_bps"] not in ("", None)]
    drag = 0.0     # 실제 체결이 기준종가 대비 불리했던 금액($)
    assumed = 0.0  # 룰 가정 슬리피지 예산($, 편도 slippage_pct)
    for t in rows:
        sh, pr, ref = to_f(t["shares"]), to_f(t["price"]), to_f(t["ref_close"])
        if sh == 0 or ref == 0:
            continue
        if t["action"] == "buy":
            drag += sh * (pr - ref)          # 더 비싸게 샀으면 +드래그
        elif t["action"] in ("take_profit", "quarter"):
            drag += sh * (ref - pr)          # 더 싸게 팔았으면 +드래그
        assumed += sh * ref * cfg["slippage_pct"]
    avg_bps = sum(slip_bps) / len(slip_bps) if slip_bps else 0.0
    cap = cfg["total_capital"]
    print(f"\n[2. 추적오차 (실행 드리프트)]")
    print(f"  평균 슬리피지 {avg_bps:+.1f} bp (룰 가정 {cfg['slippage_pct']*1e4:.0f} bp)")
    print(f"  실제 체결 드래그 {C.fmt_won(drag)}  vs  가정 예산 {C.fmt_won(assumed)}")
    print(f"  초과 드리프트 {C.fmt_won(drag - assumed)}  (자본 대비 {(drag-assumed)/cap:+.2%})")

    # --- 3. 성과 (이 달에 종료된 사이클 기준) ---
    closed = [c for c in st["cycles_closed"] if str(c.get("end", "")).startswith(month)]
    realized = sum(c["invested"] * c["pnl_pct"] for c in closed)
    wins = [c for c in closed if c["pnl_pct"] > 0]
    buys = sum(1 for t in rows if t["action"] == "buy")
    sells = sum(1 for t in rows if t["action"] in ("take_profit", "quarter"))
    print(f"\n[3. 성과]")
    print(f"  체결 {total}건 (매수 {buys} / 매도 {sells})")
    print(f"  종료 사이클 {len(closed)}건, 승률 "
          f"{(len(wins)/len(closed)):.0%}" if closed else "  종료 사이클 0건")
    if closed:
        avg_pnl = sum(c["pnl_pct"] for c in closed) / len(closed)
        print(f"  실현손익 {C.fmt_won(realized)} (평균 사이클 {avg_pnl:+.1%})")
        for c in closed:
            print(f"   · 사이클#{c['id']} {c['start']}~{c['end']} {c['reason']} {c['pnl_pct']:+.1%}")

    # --- 4. 현재 상태 스냅샷 ---
    tqqq = C.load_price(cfg["trade_ticker"])
    price = float(tqqq.iloc[-1])
    eq = C.equity(st, price)
    print(f"\n[4. 현재 스냅샷]  ({cfg['trade_ticker']} {price:.2f})")
    print(f"  총자산 {C.fmt_won(eq)} | 현금 {C.fmt_won(st['cash'])} | "
          f"보유 {st['shares']:.4f}주 | 리저브 {C.fmt_won(st['reserve'])}")
    roi = eq / cfg["total_capital"] - 1
    print(f"  누적 수익률 {roi:+.1%} (초기자본 {C.fmt_won(cfg['total_capital'])} 대비)")
    print()


if __name__ == "__main__":
    main()
