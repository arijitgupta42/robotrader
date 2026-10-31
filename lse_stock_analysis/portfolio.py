"""
The Paper Portfolio's weekly rules (see docs/adr/0003-paper-portfolio.md).

apply_week() turns last week's PortfolioState and this week's inputs (the scout's
Sector Signals, the Picks, the Universe Snapshot's closes and the GBP/USD rate)
into this week's state, its Trades and its notes.  It does no I/O and has no
randomness, so a week can be re-run and always gives the same result.

One week, in order:

1. revalue every Position at this week's close (a Position with no usable
   price, or a US Position with no exchange rate, keeps its last value and is
   marked stale);
2. update each Position's sector-absent streak and restart the clock of any
   Position that was picked again (no top-up);
3. sell Positions that hit their stop, have been held MAX_HOLD_WEEKS, or whose
   Primary Sector has been missing from the signals SECTOR_ABSENT_WEEKS in a row;
4. buy the remaining Picks in rank order (confidence, then Setup Grade, risk
   score and ticker) until cash runs out.

Without a successful scout result there are no Trades at all.  Prices are in
each stock's own units (pence for LSE, dollars for US); everything held as
money is in pounds.
"""
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from typing import Optional

# Bump whenever a rule or number in this module changes.  It is stored in every week's state,
# so the methodology review can compare the weeks before and after a change.
PORTFOLIO_VERSION = "2026-10-31.1"

OPENING_WEEK = "2026-W44"
STARTING_CASH = 1000.0

MAX_POSITION_WEIGHT = 0.20     # of total value
CASH_FLOOR = 0.05              # of total value, never spent
MAX_POSITIONS = 8
MIN_TRADE_GBP = 25.0
STAMP_DUTY = 0.005             # UK buys only
FX_FEE = 0.003                 # every US buy and sell
MAX_HOLD_WEEKS = 6
SECTOR_ABSENT_WEEKS = 2

GRADE_RANK = {"A": 0, "B": 1}


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Quote:
    """One Universe stock's close this week, from the Universe Snapshot."""

    ticker: str
    market: str                    # "LSE" or "US"
    currency: str                  # "GBp" or "USD"
    primary_sector: Optional[str]
    close: Optional[float]         # pence for LSE, dollars for US
    stale: bool = False
    price_anomaly: bool = False
    company: str = ""


@dataclass(frozen=True)
class Pick:
    """One of this week's Picks, with what the allocator needs from its signal and risk analysis."""

    ticker: str
    sector: str
    confidence: float
    convergence_type: str
    setup_grade: str
    risk_score: float
    max_position_size_pct: float   # percent of capital, from the risk agent
    atr_stop_pct: float            # percent below entry, from the risk agent

    def signal(self) -> dict:
        return {"sector": self.sector, "confidence": self.confidence, "convergence_type": self.convergence_type}


@dataclass
class WeekInputs:
    week: str                                   # ISO week label, e.g. "2026-W44"
    scout_ok: bool                              # False: no successful scout result this week
    signals: list[dict]                         # each has at least "sector"
    picks: list[Pick]
    quotes: dict[str, Quote]                    # ticker -> Quote (the whole Universe)
    gbpusd: Optional[float]                     # US dollars per pound, or None if unavailable
    scout_error: Optional[str] = None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class Position:
    ticker: str
    company: str
    market: str
    currency: str
    primary_sector: Optional[str]
    quantity: float
    entry_week: str
    entry_price: float             # own units
    entry_fx: Optional[float]
    cost_basis_gbp: float          # what was paid, costs included
    stop_price: float              # own units
    clock_start_week: str          # entry, or the last week it was picked again
    sector_absent_weeks: int
    last_price: float              # own units
    last_fx: Optional[float]
    value_gbp: float
    stale: bool = False            # no usable price this week
    fx_stale: bool = False         # US Position valued at an old exchange rate
    signal: dict = field(default_factory=dict)


@dataclass
class PortfolioState:
    week: str
    portfolio_version: str
    cash: float
    positions: list[Position]
    benchmark_value: float
    universe_gbp: dict[str, float]  # last usable close in pounds per stock, for the Benchmark
    last_fx: Optional[float]

    def holdings_value(self) -> float:
        return sum(p.value_gbp for p in self.positions)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PortfolioState":
        names = {f.name for f in fields(Position)}
        return cls(**{**d, "positions": [Position(**{k: v for k, v in p.items() if k in names}) for p in d["positions"]]})


@dataclass
class Trade:
    week: str
    side: str                      # "buy" or "sell"
    ticker: str
    company: str
    market: str
    currency: str
    sector: Optional[str]
    reason: str                    # "pick", "stop", "max_hold", "sector_absent"
    quantity: float
    price: float                   # own units
    fx: Optional[float]
    value_gbp: float               # quantity x price in pounds, before costs
    cost_gbp: float
    pnl_gbp: Optional[float] = None  # sells: proceeds after costs minus cost basis
    signal: Optional[dict] = None    # buys: the signal behind the Pick


@dataclass
class WeekResult:
    state: PortfolioState
    trades: list[Trade]
    notes: list[dict]              # each: kind, message, and ticker where it applies
    valuation: dict                # cash, holdings, total, benchmark

    def to_dict(self) -> dict:
        return {"state": self.state.to_dict(), "trades": [asdict(t) for t in self.trades],
                "notes": self.notes, "valuation": self.valuation}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _week_monday(label: str) -> date:
    year, week = label.split("-W")
    return date.fromisocalendar(int(year), int(week), 1)


def weeks_between(earlier: str, later: str) -> int:
    """Whole ISO weeks from one week label to another."""
    return (_week_monday(later) - _week_monday(earlier)).days // 7


def to_gbp(price: float, market: str, gbpusd: Optional[float]) -> Optional[float]:
    """A price in its own units (pence or dollars) in pounds; None for a US price with no exchange rate."""
    if market == "US":
        return price / gbpusd if gbpusd else None
    return price / 100.0


def _usable(quote: Optional[Quote]) -> bool:
    return quote is not None and quote.close is not None and quote.close > 0 and not quote.stale


def _rank_key(p: Pick):
    return (-p.confidence, GRADE_RANK.get(p.setup_grade, 9), p.risk_score, p.ticker)


def _universe_gbp(quotes: dict[str, Quote], gbpusd: Optional[float]) -> dict[str, float]:
    out = {}
    for t, quote in quotes.items():
        if _usable(quote) and not quote.price_anomaly:
            gbp = to_gbp(quote.close, quote.market, gbpusd)
            if gbp:
                out[t] = gbp
    return out


def _benchmark_return(previous: dict[str, float], current: dict[str, float]) -> Optional[float]:
    common = [t for t in current if t in previous]
    if not common:
        return None
    return sum(current[t] / previous[t] - 1.0 for t in common) / len(common)


# ---------------------------------------------------------------------------
# The weekly step
# ---------------------------------------------------------------------------

def apply_week(previous: Optional[PortfolioState], inputs: WeekInputs) -> WeekResult:
    """
    This week's Paper Portfolio from last week's.  `previous=None` is the opening
    week: STARTING_CASH and no Positions.  The caller makes sure weeks are applied
    in order with no gaps (a week without a scout result is applied with scout_ok=False).
    """
    week, fx = inputs.week, inputs.gbpusd
    notes: list[dict] = []
    trades: list[Trade] = []

    if previous is None:
        cash, positions, benchmark, universe, last_fx = STARTING_CASH, [], STARTING_CASH, {}, None
    else:
        cash = previous.cash
        positions = [Position(**asdict(p)) for p in previous.positions]
        benchmark, universe, last_fx = previous.benchmark_value, dict(previous.universe_gbp), previous.last_fx
    if fx:
        last_fx = fx

    # Benchmark: equal-weight average return in pounds since the last week with prices
    current_universe = _universe_gbp(inputs.quotes, fx)
    if current_universe:
        if previous is not None:
            ret = _benchmark_return(universe, current_universe)
            if ret is not None:
                benchmark *= 1.0 + ret
        universe = {**universe, **current_universe}

    if not inputs.scout_ok:
        notes.append({"kind": "no_trade_week", "ticker": None,
                      "message": f"No successful scout result this week ({inputs.scout_error or 'unknown reason'}); no Trades."})
    if fx is None:
        notes.append({"kind": "missing_fx", "ticker": None,
                      "message": "No GBP/USD rate this week: no US Trades; US Positions keep their last rate."})

    # 1. revalue
    sellable: set[str] = set()
    for p in positions:
        quote = inputs.quotes.get(p.ticker)
        p.stale = not _usable(quote)
        if not p.stale:
            p.last_price = quote.close
        p.fx_stale = p.market == "US" and fx is None
        if p.market == "US" and fx:
            p.last_fx = fx
        p.value_gbp = p.quantity * to_gbp(p.last_price, p.market, p.last_fx)
        if p.stale:
            notes.append({"kind": "stale_price", "ticker": p.ticker,
                          "message": f"{p.ticker} has no usable price this week; kept at its last value and not sold."})
        elif not p.fx_stale:
            sellable.add(p.ticker)

    picked = {pk.ticker: pk for pk in inputs.picks}
    if inputs.scout_ok:
        # 2. sector-absent streaks and clock restarts
        signalled = {s["sector"] for s in inputs.signals}
        for p in positions:
            p.sector_absent_weeks = 0 if p.primary_sector in signalled else p.sector_absent_weeks + 1
            if p.ticker in picked:
                p.clock_start_week = week
                p.sector_absent_weeks = 0
                p.signal = picked[p.ticker].signal()

        # 3. sells
        kept = []
        for p in positions:
            reason = None
            if p.ticker in sellable:
                if p.last_price <= p.stop_price:
                    reason = "stop"
                elif weeks_between(p.clock_start_week, week) >= MAX_HOLD_WEEKS:
                    reason = "max_hold"
                elif p.sector_absent_weeks >= SECTOR_ABSENT_WEEKS:
                    reason = "sector_absent"
            if reason is None:
                kept.append(p)
                continue
            value = p.value_gbp
            cost = value * FX_FEE if p.market == "US" else 0.0
            cash += value - cost
            trades.append(Trade(week=week, side="sell", ticker=p.ticker, company=p.company, market=p.market,
                                currency=p.currency, sector=p.primary_sector, reason=reason, quantity=p.quantity,
                                price=p.last_price, fx=p.last_fx if p.market == "US" else None, value_gbp=value,
                                cost_gbp=cost, pnl_gbp=value - cost - p.cost_basis_gbp))
        positions = kept

        # 4. buys
        total = cash + sum(p.value_gbp for p in positions)
        spendable = cash - CASH_FLOOR * total
        held = {p.ticker for p in positions}
        out_of_cash = False
        for pk in sorted(inputs.picks, key=_rank_key):
            if pk.ticker in held:
                continue
            reason = None
            quote = inputs.quotes.get(pk.ticker)
            price_gbp = to_gbp(quote.close, quote.market, fx) if _usable(quote) else None
            target = total * min(pk.max_position_size_pct / 100.0 * pk.confidence, MAX_POSITION_WEIGHT)
            rate = (FX_FEE if quote.market == "US" else STAMP_DUTY) if quote else 0.0
            if out_of_cash:
                reason = "not enough cash"
            elif not _usable(quote):
                reason = "no usable price"
            elif price_gbp is None:
                reason = "no exchange rate"
            elif len(positions) >= MAX_POSITIONS:
                reason = "position limit reached"
            elif target < MIN_TRADE_GBP:
                reason = "under £25"
            elif target * (1 + rate) > spendable:
                reason, out_of_cash = "not enough cash", True
            if reason:
                notes.append({"kind": "skipped", "ticker": pk.ticker, "message": f"Pick {pk.ticker} not bought: {reason}.",
                              "reason": reason})
                continue
            cost = target * rate
            cash -= target + cost
            spendable -= target + cost
            us = quote.market == "US"
            positions.append(Position(
                ticker=pk.ticker, company=quote.company, market=quote.market, currency=quote.currency,
                primary_sector=quote.primary_sector, quantity=target / price_gbp, entry_week=week,
                entry_price=quote.close, entry_fx=fx if us else None, cost_basis_gbp=target + cost,
                stop_price=quote.close * (1 - pk.atr_stop_pct / 100.0), clock_start_week=week,
                sector_absent_weeks=0, last_price=quote.close, last_fx=fx if us else None, value_gbp=target,
                signal=pk.signal(),
            ))
            held.add(pk.ticker)
            trades.append(Trade(week=week, side="buy", ticker=pk.ticker, company=quote.company, market=quote.market,
                                currency=quote.currency, sector=quote.primary_sector, reason="pick",
                                quantity=target / price_gbp, price=quote.close, fx=fx if us else None,
                                value_gbp=target, cost_gbp=cost, signal=pk.signal()))

    state = PortfolioState(week=week, portfolio_version=PORTFOLIO_VERSION, cash=cash, positions=positions,
                           benchmark_value=benchmark, universe_gbp=universe, last_fx=last_fx)
    holdings = state.holdings_value()
    return WeekResult(state=state, trades=trades, notes=notes,
                      valuation={"cash": cash, "holdings": holdings, "total": cash + holdings, "benchmark": benchmark})
