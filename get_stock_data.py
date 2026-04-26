import logging
import pandas as pd
import yfinance as yf
from tqdm import tqdm
from pytickersymbols import PyTickerSymbols

logger = logging.getLogger(__name__)


def get_index_data(
        start: str,
        end: str,
        index: str = 'FTSE 250',
        verbose: bool = False
        ) -> dict:
    """
    get_index_data(start, end, index, verbose)

    Download daily OHLCV data for every stock in the specified index.

    Defaults to FTSE 250 for a wider universe of swing candidates —
    smaller, more volatile names that can realistically deliver the
    10 %+ moves targeted by a swing setup within a 5–20 day hold.

    Parameters
    ----------
    start : str
        Start date in YYYY-MM-DD format. For swing analysis, 6 months
        is usually sufficient (enough history for reliable indicators
        without stale macro noise).
    end : str
        End date in YYYY-MM-DD format.
    index : str, default='FTSE 250'
        Index name recognised by PyTickerSymbols.
    verbose : bool, default=False
        If True, logs a line per ticker instead of a progress bar.

    Returns
    -------
    valid_data : dict[str, pd.DataFrame]
        Ticker → OHLCV DataFrame. Tickers that fail to download or
        return empty data are silently skipped.
    """
    stock_data = PyTickerSymbols()
    uk_stocks = stock_data.get_stocks_by_index(index)
    tickers = [s['symbol'] for s in uk_stocks]

    valid_data = {}

    logger.info(f"Downloading {index} ({len(tickers)} tickers) from {start} to {end}")
    for ticker in tqdm(tickers, disable=verbose):
        if verbose:
            logger.info(f"  Downloading {ticker}")
        try:
            df = yf.download(
                tickers=ticker,
                start=start,
                end=end,
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                progress=False
            )
            if df.empty:
                continue
            valid_data[ticker] = df
        except Exception as e:
            if verbose:
                logger.error(f"  Download failed for {ticker}: {e}")
            continue

    logger.info(f"Downloaded {len(valid_data)}/{len(tickers)} tickers successfully")
    return valid_data


def save_data(stocks_dict: dict, filename: str):
    """
    Save a dictionary of stocks data by ticker to a single CSV file.

    Parameters
    ----------
    stocks_dict : dict
    filename : str
    """
    stocks_csv = pd.concat(stocks_dict, axis=1)
    stocks_csv.to_csv(filename)
    logger.info(f"Data saved to {filename}")
