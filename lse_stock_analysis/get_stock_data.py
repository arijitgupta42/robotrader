import logging
import pandas as pd
import yfinance as yf

try:
    from tqdm import tqdm
except ImportError:                     # progress bar is optional
    def tqdm(iterable, **_kwargs):
        return iterable

from .universe import universe_tickers

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Universe — tickers come from sector_map.json (see universe.py)
# ---------------------------------------------------------------------------

_INDEX_TICKERS = {
    'FTSE 350': universe_tickers(),
    'FTSE 250': universe_tickers('FTSE 250'),
    'FTSE 100': universe_tickers('FTSE 100'),
}


# ---------------------------------------------------------------------------
# Price download
# ---------------------------------------------------------------------------

def get_index_data(
        start:   str,
        end:     str,
        index:   str  = 'FTSE 250',
        verbose: bool = False,
        ) -> dict[str, pd.DataFrame]:
    """
    Download daily OHLCV data for every constituent of the given index.

    Tickers are sourced from the Sector Map (sector_map.json) — no scraping,
    no external dependencies beyond yfinance.  Update the Sector Map after
    each quarterly FTSE Russell rebalance (March, June, September, December).

    Parameters
    ----------
    start : str   — YYYY-MM-DD.  6 months back is recommended for swing analysis.
    end   : str   — YYYY-MM-DD
    index : str   — 'FTSE 350', 'FTSE 250' or 'FTSE 100'
    verbose : bool

    Returns
    -------
    dict[str, pd.DataFrame]
        ticker → OHLCV DataFrame.  Failed / empty tickers are omitted.
    """
    tickers = _INDEX_TICKERS.get(index)
    if tickers is None:
        raise ValueError(f"Unknown index '{index}'. Available: {list(_INDEX_TICKERS)}")

    logger.info(f"Downloading {index} ({len(tickers)} tickers) | {start} → {end}")

    valid_data: dict[str, pd.DataFrame] = {}
    failed:     list[str]               = []

    for ticker in tqdm(tickers, disable=verbose):
        if verbose:
            logger.info(f"  {ticker}")
        try:
            df = yf.download(
                tickers     = ticker,
                start       = start,
                end         = end,
                interval    = "1d",
                auto_adjust = True,
                group_by    = "ticker",
                progress    = False,
            )

            if df is None or df.empty:
                failed.append(ticker)
                continue

            # yfinance occasionally returns a MultiIndex for a single ticker
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(-1)

            required = {"Open", "High", "Low", "Close", "Volume"}
            if not required.issubset(df.columns):
                logger.debug(f"  {ticker}: unexpected columns {list(df.columns)}")
                failed.append(ticker)
                continue

            valid_data[ticker] = df

        except Exception as exc:
            logger.debug(f"  {ticker}: {exc}")
            failed.append(ticker)

    logger.info(
        f"Downloaded {len(valid_data)}/{len(tickers)} tickers "
        f"({len(failed)} failed)"
    )
    if failed:
        logger.debug(f"Failed: {failed}")

    return valid_data


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def save_data(stocks_dict: dict, filename: str) -> None:
    """Cache a ticker → DataFrame dict to CSV so you skip re-downloading."""
    pd.concat(stocks_dict, axis=1).to_csv(filename)
    logger.info(f"Saved to {filename}")


def load_data(filename: str) -> dict[str, pd.DataFrame]:
    """Re-hydrate a dict of DataFrames from a CSV saved by save_data()."""
    df      = pd.read_csv(filename, header=[0, 1], index_col=0, parse_dates=True)
    tickers = df.columns.get_level_values(0).unique().tolist()
    logger.info(f"Loaded {len(tickers)} tickers from {filename}")
    return {t: df[t].copy() for t in tickers}
