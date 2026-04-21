import pandas as pd

def normalise_stock_data(stock_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """
    Normalises each ticker DataFrame into a consistent, flat structure
    ready for the TrendAnalysisAgent.

    Operations:
    - Flattens multi-level columns if present
    - Ensures correct dtypes
    - Sorts by date ascending
    - Removes any duplicate dates

    Parameters
    ---------
    stock_data : dict[str, pd.DataFrame]

    Returns
    -------
    normalised : dict[str, pd.Dataframe]
    """
    normalised = {}

    for ticker, df in stock_data.items():
        df = df.copy()

        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[-1] for col in df.columns]

        # Ensure expected columns exist
        expected = {"Open", "High", "Low", "Close", "Volume"}
        if not expected.issubset(df.columns):
            print(f"  ✗ {ticker}: missing columns, skipping")
            continue

        # Enforce dtypes
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = df[col].astype(float)
        df["Volume"] = df["Volume"].astype(int)

        # Ensure index is datetime
        df.index = pd.to_datetime(df.index)
        df.index.name = "Date"

        # Sort ascending and drop duplicate dates
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]

        normalised[ticker] = df

    print(f"Normalised {len(normalised)}/{len(stock_data)} tickers")
    return normalised