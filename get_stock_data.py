import pandas as pd
import yfinance as yf
from tqdm import tqdm
from pytickersymbols import PyTickerSymbols


def get_index_data(
        start:str,
        end:str,
        index:str = 'FTSE 100',
        verbose:bool = False
        ) -> pd.DataFrame:
    """
    get_index_data(index)

    Get the dataframe of stocks data for the last 2 years from the specified index

    Parameters
    ----------
    start: str
        Start date for the stock data in YYYY-MM-DD format
    end: str
        End date for the stock data in YYYY-MM-DD format
    index : str, default=FTSE 100
    verbose: bool, default=False
        Sets the verbosity for the tickerwise data download

    Returns
    -------
    stocks_df : dict
        Dictionary of dataframes containing the stocks data about the last 2 years for the FTSE 100 index
    """
    stock_data = PyTickerSymbols()
    uk_stocks = stock_data.get_stocks_by_index(index)
    tickers = [s['symbol'] for s in uk_stocks]

    valid_data = {}

    print(f"Downloading stock data from {index} index")
    for ticker in tqdm(tickers, disable=verbose):
        if verbose:
            print(f"Downloaded data for for {ticker}")
        try:
            df = yf.download(
                tickers=ticker,
                start=start,
                end=end,
                interval="1d",       # daily bars
                auto_adjust=True,    
                group_by="ticker",
                progress=verbose      # suppress printing output
            )
            valid_data[ticker] = df
        except:
            if verbose:
                print(f"Download failed for {ticker}")
            continue    # TODO: catch exception more gracefully
    return valid_data

def save_data(stocks_dict: dict, filename:str):
    """
    save_data(stocks_dict, filename)

    Save a dictionary of stokcs data by ticker to a singular csv file

    Parameters
    ----------
    stocks_dict : dict
    filename : str
    """
    stocks_csv = pd.concat(stocks_dict, axis=1)
    stocks_csv.to_csv(filename)
    print(f"Data saved to {filename}")