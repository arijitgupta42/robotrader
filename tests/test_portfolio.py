import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lse_stock_analysis.portfolio import (
    PORTFOLIO_VERSION,
    STARTING_CASH,
    Pick,
    PortfolioState,
    Quote,
    WeekInputs,
    apply_week,
    weeks_between,
)

WATER = "Water"
BANKS = "UK Retail Banks"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def q(ticker, close, market="LSE", sector=WATER, stale=False, anomaly=False):
    return Quote(ticker=ticker, market=market, currency="GBp" if market == "LSE" else "USD", primary_sector=sector,
                 close=close, stale=stale, price_anomaly=anomaly, company=f"{ticker} plc")


def pk(ticker, sector=WATER, conf=1.0, maxpos=10.0, atr=6.0, grade="A", risk=3, conv="Confirmed"):
    return Pick(ticker=ticker, sector=sector, confidence=conf, convergence_type=conv, setup_grade=grade,
                risk_score=risk, max_position_size_pct=maxpos, atr_stop_pct=atr)


def wk(week, picks=(), quotes=(), signals=None, fx=1.25, scout_ok=True):
    if signals is None:
        signals = sorted({p.sector for p in picks})
    return WeekInputs(
        week=week, scout_ok=scout_ok,
        signals=[{"sector": s, "confidence": 0.7, "convergence_type": "Confirmed"} for s in signals],
        picks=list(picks), quotes={x.ticker: x for x in quotes}, gbpusd=fx,
        scout_error=None if scout_ok else "zero_signals",
    )


def skipped(result):
    return {n["ticker"]: n["reason"] for n in result.notes if n["kind"] == "skipped"}


def bought(week="2026-W44", **pick_kwargs):
    """Opening week that buys one LSE stock 'AAA' at 200p (£50 at 10% x 0.5 confidence)."""
    pick_kwargs.setdefault("conf", 0.5)
    return apply_week(None, wk(week, [pk("AAA", **pick_kwargs)], [q("AAA", 200.0)]))


# ---------------------------------------------------------------------------
# Opening week and buying
# ---------------------------------------------------------------------------

def test_opening_week_with_no_picks_holds_starting_cash():
    r = apply_week(None, wk("2026-W44"))
    assert STARTING_CASH == 1000.0
    assert r.state.cash == 1000.0
    assert r.state.positions == []
    assert r.trades == []
    assert r.valuation == {"cash": 1000.0, "holdings": 0.0, "total": 1000.0, "benchmark": 1000.0}
    assert r.state.portfolio_version == PORTFOLIO_VERSION
    assert r.state.week == "2026-W44"


def test_lse_buy_converts_pence_sizes_by_confidence_and_pays_stamp_duty():
    r = bought()
    [t] = r.trades
    assert (t.side, t.ticker, t.reason) == ("buy", "AAA", "pick")
    assert t.price == 200.0 and t.fx is None
    assert t.value_gbp == pytest.approx(50.0)            # 10% x 0.5 of £1,000
    assert t.quantity == pytest.approx(25.0)             # £50 at £2.00
    assert t.cost_gbp == pytest.approx(0.25)             # 0.5% stamp duty
    assert t.signal == {"sector": WATER, "confidence": 0.5, "convergence_type": "Confirmed"}
    assert r.state.cash == pytest.approx(949.75)
    [p] = r.state.positions
    assert p.stop_price == pytest.approx(188.0)          # 200p x (1 - 6%)
    assert p.entry_week == p.clock_start_week == "2026-W44"
    assert r.valuation["total"] == pytest.approx(999.75)


def test_position_weight_is_capped_at_20_percent():
    r = apply_week(None, wk("2026-W44", [pk("AAA", maxpos=35.0, conf=1.0)], [q("AAA", 100.0)]))
    assert r.trades[0].value_gbp == pytest.approx(200.0)


def test_us_buy_converts_dollars_and_pays_the_fx_fee():
    r = apply_week(None, wk("2026-W44", [pk("UUU", maxpos=10.0)], [q("UUU", 125.0, market="US")], fx=1.25))
    [t] = r.trades
    assert t.fx == 1.25
    assert t.quantity == pytest.approx(1.0)              # $125 / 1.25 = £100
    assert t.cost_gbp == pytest.approx(0.30)             # 0.3% FX fee, no stamp duty
    assert r.state.cash == pytest.approx(899.70)


def test_buys_go_in_confidence_order_keep_a_cash_floor_and_stop_when_cash_runs_out():
    picks = [pk(f"S{i}", sector=f"Sector{i}", maxpos=20.0, conf=1.0) for i in range(4)]
    picks += [pk("BIG", sector="SectorB", maxpos=20.0, conf=0.9), pk("SMALL", sector="SectorS", maxpos=10.0, conf=0.5)]
    picks.reverse()                                       # input order must not matter
    quotes = [q(p.ticker, 100.0, sector=p.sector) for p in picks]
    r = apply_week(None, wk("2026-W44", picks, quotes))
    assert [t.ticker for t in r.trades] == ["S0", "S1", "S2", "S3"]
    assert r.state.cash == pytest.approx(1000 - 4 * 201.0)
    # BIG does not fit above the 5% floor; SMALL would fit but ranks lower, so it is dropped too
    assert skipped(r) == {"BIG": "not enough cash", "SMALL": "not enough cash"}
    assert r.state.cash >= 0.05 * 1000 - 1e-9


def test_at_most_eight_positions():
    picks = [pk(f"S{i}", sector=f"Sector{i}", maxpos=5.0, conf=1.0, risk=i) for i in range(9)]
    r = apply_week(None, wk("2026-W44", picks, [q(p.ticker, 100.0, sector=p.sector) for p in picks]))
    assert len(r.state.positions) == 8
    assert skipped(r) == {"S8": "position limit reached"}


def test_trades_under_25_pounds_are_skipped():
    r = apply_week(None, wk("2026-W44", [pk("AAA", maxpos=4.0, conf=0.5)], [q("AAA", 100.0)]))
    assert r.trades == []
    assert skipped(r) == {"AAA": "under £25"}


def test_pick_without_a_usable_price_is_skipped():
    r = apply_week(None, wk("2026-W44", [pk("AAA"), pk("BBB")], [q("AAA", 100.0, stale=True)]))
    assert r.trades == []
    assert skipped(r) == {"AAA": "no usable price", "BBB": "no usable price"}


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------

def test_stop_sells_at_or_below_the_stop_price():
    r = apply_week(bought().state, wk("2026-W45", quotes=[q("AAA", 188.0)], signals=[WATER]))
    [t] = r.trades
    assert (t.side, t.reason) == ("sell", "stop")
    assert t.value_gbp == pytest.approx(47.0)
    assert t.cost_gbp == 0.0                              # no stamp duty on sells
    assert t.pnl_gbp == pytest.approx(47.0 - 50.25)
    assert r.state.positions == []
    assert r.state.cash == pytest.approx(949.75 + 47.0)


def test_above_the_stop_is_kept():
    r = apply_week(bought().state, wk("2026-W45", quotes=[q("AAA", 188.5)], signals=[WATER]))
    assert r.trades == [] and len(r.state.positions) == 1


def _hold(state, weeks, picks=(), signals=(WATER,)):
    results = []
    for w in weeks:
        r = apply_week(state, wk(w, picks, [q("AAA", 200.0)], signals=list(signals)))
        results.append(r)
        state = r.state
    return results


def test_sold_after_six_weeks_held():
    rs = _hold(bought().state, ["2026-W45", "2026-W46", "2026-W47", "2026-W48", "2026-W49", "2026-W50"])
    assert all(r.trades == [] for r in rs[:-1])
    [t] = rs[-1].trades
    assert t.reason == "max_hold"


def test_repick_restarts_the_clock_without_topping_up():
    state = _hold(bought().state, ["2026-W45", "2026-W46", "2026-W47"])[-1].state
    r = apply_week(state, wk("2026-W48", [pk("AAA", conf=0.9)], [q("AAA", 200.0)]))
    assert r.trades == []
    assert r.state.positions[0].quantity == pytest.approx(25.0)
    assert r.state.positions[0].clock_start_week == "2026-W48"
    rs = _hold(r.state, ["2026-W49", "2026-W50", "2026-W51", "2026-W52", "2026-W53"])
    assert all(x.trades == [] for x in rs)                # 2026 has 53 ISO weeks
    [t] = _hold(rs[-1].state, ["2027-W01"])[0].trades
    assert t.reason == "max_hold"


def test_sold_when_sector_absent_two_weeks_running():
    r45 = apply_week(bought().state, wk("2026-W45", quotes=[q("AAA", 200.0)], signals=[BANKS]))
    assert r45.trades == [] and r45.state.positions[0].sector_absent_weeks == 1
    r46 = apply_week(r45.state, wk("2026-W46", quotes=[q("AAA", 200.0)], signals=[BANKS]))
    [t] = r46.trades
    assert t.reason == "sector_absent"


def test_one_week_absence_is_forgiven_when_the_sector_returns():
    state = bought().state
    for w, sig in [("2026-W45", BANKS), ("2026-W46", WATER), ("2026-W47", BANKS)]:
        r = apply_week(state, wk(w, quotes=[q("AAA", 200.0)], signals=[sig]))
        assert r.trades == []
        state = r.state
    assert state.positions[0].sector_absent_weeks == 1


def test_sell_frees_cash_for_buys_in_the_same_week():
    state = apply_week(None, wk("2026-W44", [pk(f"S{i}", sector=f"Sector{i}", maxpos=20.0) for i in range(4)],
                                [q(f"S{i}", 100.0, sector=f"Sector{i}") for i in range(4)])).state
    r = apply_week(state, wk("2026-W45", [pk("NEW", sector="SectorN", maxpos=20.0)],
                             [q("S0", 90.0, sector="Sector0")] + [q(f"S{i}", 100.0, sector=f"Sector{i}") for i in (1, 2, 3)]
                             + [q("NEW", 100.0, sector="SectorN")],
                             signals=["Sector0", "Sector1", "Sector2", "Sector3", "SectorN"]))
    assert [(t.side, t.ticker) for t in r.trades] == [("sell", "S0"), ("buy", "NEW")]


# ---------------------------------------------------------------------------
# Bad weeks
# ---------------------------------------------------------------------------

def test_stale_or_missing_price_keeps_last_value_and_cannot_be_sold():
    state = bought().state
    for quotes in ([q("AAA", 150.0, stale=True)], []):
        r = apply_week(state, wk("2026-W45", quotes=quotes, signals=[BANKS]))
        assert r.trades == []
        [p] = r.state.positions
        assert p.stale and p.last_price == 200.0
        assert r.valuation["holdings"] == pytest.approx(50.0)
        assert any(n["kind"] == "stale_price" and n["ticker"] == "AAA" for n in r.notes)


def test_missing_fx_blocks_us_trades_but_not_lse():
    state = apply_week(None, wk("2026-W44", [pk("UUU", maxpos=10.0)], [q("UUU", 125.0, market="US")])).state
    r = apply_week(state, wk("2026-W45", [pk("UU2", sector=BANKS), pk("LLL", sector=BANKS, conf=0.5)],
                             [q("UUU", 50.0, market="US"), q("UU2", 10.0, market="US", sector=BANKS),
                              q("LLL", 100.0, sector=BANKS)], signals=[WATER, BANKS], fx=None))
    assert [(t.side, t.ticker) for t in r.trades] == [("buy", "LLL")]   # UUU is below its stop but cannot be sold
    assert skipped(r)["UU2"] == "no exchange rate"
    [p] = [p for p in r.state.positions if p.ticker == "UUU"]
    assert p.fx_stale and p.last_fx == 1.25
    assert p.value_gbp == pytest.approx(40.0)              # $50 at the last known 1.25
    assert any(n["kind"] == "missing_fx" for n in r.notes)


def test_scout_failure_week_trades_nothing_and_does_not_count_as_absence():
    state = bought().state
    r = apply_week(state, wk("2026-W45", quotes=[], signals=[], scout_ok=False))
    assert r.trades == []
    assert r.state.positions[0].sector_absent_weeks == 0
    assert any(n["kind"] == "no_trade_week" and "zero_signals" in n["message"] for n in r.notes)
    assert r.valuation["total"] == pytest.approx(999.75)


def test_scout_failure_week_still_ages_positions():
    state = bought().state
    state = apply_week(state, wk("2026-W45", scout_ok=False, signals=[])).state
    rs = _hold(state, ["2026-W46", "2026-W47", "2026-W48", "2026-W49", "2026-W50"])
    assert rs[-1].trades[0].reason == "max_hold"


# ---------------------------------------------------------------------------
# Benchmark, conservation, determinism
# ---------------------------------------------------------------------------

def test_benchmark_compounds_the_equal_weight_universe_return_in_pounds():
    r44 = apply_week(None, wk("2026-W44", quotes=[q("AAA", 200.0), q("UUU", 100.0, market="US"),
                                                 q("BAD", 100.0, anomaly=True)], fx=1.25))
    r45 = apply_week(r44.state, wk("2026-W45", quotes=[q("AAA", 220.0), q("UUU", 100.0, market="US"),
                                                     q("BAD", 1000.0, anomaly=True)], fx=1.0))
    # AAA +10%; UUU £80 -> £100 = +25%; the anomaly is ignored
    assert r45.valuation["benchmark"] == pytest.approx(1000 * 1.175)
    r46 = apply_week(r45.state, wk("2026-W46", scout_ok=False, signals=[], quotes=[]))
    assert r46.valuation["benchmark"] == pytest.approx(1175.0)
    r47 = apply_week(r46.state, wk("2026-W47", quotes=[q("AAA", 242.0), q("UUU", 100.0, market="US")], fx=1.0))
    assert r47.valuation["benchmark"] == pytest.approx(1175.0 * 1.05)   # AAA +10% vs W45, UUU flat


def test_value_only_changes_by_costs_when_prices_do_not_move():
    picks = [pk("AAA", maxpos=20.0), pk("UUU", sector=BANKS, maxpos=10.0)]
    quotes = [q("AAA", 100.0), q("UUU", 125.0, market="US", sector=BANKS)]
    r44 = apply_week(None, wk("2026-W44", picks, quotes))
    costs = sum(t.cost_gbp for t in r44.trades)
    assert r44.valuation["total"] == pytest.approx(1000.0 - costs)
    r45 = apply_week(r44.state, wk("2026-W45", quotes=quotes, signals=["Other"]))
    r46 = apply_week(r45.state, wk("2026-W46", quotes=quotes, signals=["Other"]))
    assert len(r46.trades) == 2 and r46.state.positions == []
    costs += sum(t.cost_gbp for t in r46.trades)
    assert r46.state.cash == pytest.approx(1000.0 - costs)


def test_same_inputs_give_the_same_result_and_state_round_trips():
    inputs = wk("2026-W44", [pk("AAA"), pk("UUU", sector=BANKS)], [q("AAA", 200.0), q("UUU", 125.0, market="US", sector=BANKS)])
    a, b = apply_week(None, inputs), apply_week(None, inputs)
    assert a.to_dict() == b.to_dict()
    restored = PortfolioState.from_dict(a.state.to_dict())
    assert restored == a.state
    nxt = wk("2026-W45", quotes=[q("AAA", 150.0)], signals=[WATER])
    assert apply_week(restored, nxt).to_dict() == apply_week(a.state, nxt).to_dict()


def test_weeks_between_handles_year_ends():
    assert weeks_between("2026-W44", "2026-W50") == 6
    assert weeks_between("2026-W52", "2027-W01") == 2      # 2026 has a W53
