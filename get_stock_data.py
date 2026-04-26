import logging
import pandas as pd
import yfinance as yf
from tqdm import tqdm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FTSE 250 constituents — as of Q1 2026
# Source: London Stock Exchange / FTSE Russell quarterly review
# Update this list after each quarterly rebalance (Mar/Jun/Sep/Dec).
# ---------------------------------------------------------------------------

FTSE250_TICKERS = [
    "AGR.L",  "AJG.L",  "AML.L",  "AO.L",   "APH.L",  "ASL.L",  "ATG.L",
    "ATST.L", "AUTO.L", "AV.L",   "AVER.L", "AWE.L",  "AZN.L",  "BAB.L",
    "BAKK.L", "BATS.L", "BBY.L",  "BCG.L",  "BCPT.L", "BEZ.L",  "BGO.L",
    "BHMG.L", "BIRG.L", "BKGH.L", "BKG.L",  "BLND.L", "BME.L",  "BNZL.L",
    "BOO.L",  "BOWL.L", "BRBY.L", "BRK.L",  "BSRT.L", "BTG.L",  "BYIT.L",
    "CAL.L",  "CAML.L", "CCH.L",  "CCR.L",  "CLDN.L", "CLLN.L", "CLX.L",
    "CMC.L",  "CMCL.L", "CMH.L",  "CNE.L",  "CNIC.L", "COA.L",  "COG.L",
    "COPL.L", "CRDA.L", "CRN.L",  "CRST.L", "CSN.L",  "CTY.L",  "CVS.L",
    "CWK.L",  "CYBG.L", "DCG.L",  "DLAR.L", "DLG.L",  "DNO.L",  "DOM.L",
    "DPH.L",  "DRX.L",  "DSG.L",  "DSCV.L", "DTY.L",  "DVP.L",  "EAT.L",
    "EGF.L",  "ELTA.L", "EMIS.L", "EMG.L",  "EMR.L",  "ENOG.L", "EOG.L",
    "ERA.L",  "ESL.L",  "ESNT.L", "ESYS.L", "EVET.L", "EWI.L",  "EZJ.L",
    "FAN.L",  "FDEV.L", "FDM.L",  "FENR.L", "FGP.L",  "FJET.L", "FLO.L",
    "FLT.L",  "FLTV.L", "FPM.L",  "FRES.L", "FSV.L",  "FXPO.L", "GAW.L",
    "GCP.L",  "GENL.L", "GHE.L",  "GKN.L",  "GLEN.L", "GNK.L",  "GOOD.L",
    "GRG.L",  "GRI.L",  "GRS.L",  "GSK.L",  "GTD.L",  "GVC.L",  "HAS.L",
    "HBRN.L", "HGT.L",  "HIML.L", "HIK.L",  "HNE.L",  "HOME.L", "HOC.L",
    "HOTC.L", "HSBA.L", "HSL.L",  "HTG.L",  "HVN.L",  "HWG.L",  "HWDN.L",
    "IAG.L",  "ICP.L",  "IGG.L",  "IGTV.L", "IHG.L",  "IMB.L",  "IMP.L",
    "INF.L",  "ING.L",  "INPP.L", "INS.L",  "IOF.L",  "IOFB.L", "IQE.L",
    "IRON.L", "ISAT.L", "ITM.L",  "ITV.L",  "IWG.L",  "JD.L",   "JMAT.L",
    "JMG.L",  "JUST.L", "KAZ.L",  "KGF.L",  "KGHM.L", "KIE.L",  "KMK.L",
    "LAD.L",  "LAND.L", "LBG.L",  "LGO.L",  "LGEN.L", "LIO.L",  "LLOY.L",
    "LMP.L",  "LND.L",  "LSEG.L", "LUCE.L", "LWI.L",  "MAB.L",  "MACF.L",
    "MAN.L",  "MARS.L", "MCS.L",  "MCY.L",  "MCON.L", "MCRO.L", "MDC.L",
    "MERL.L", "MKS.L",  "MNDI.L", "MNG.L",  "MNZS.L", "MOTR.L", "MPI.L",
    "MRO.L",  "MSLH.L", "MTO.L",  "MTW.L",  "MUT.L",  "MXCT.L", "NCB.L",
    "NCC.L",  "NCYT.L", "NFC.L",  "NMC.L",  "NRR.L",  "NXT.L",  "OML.L",
    "OSB.L",  "OXB.L",  "PBBV.L", "PBH.L",  "PCGE.L", "PETS.L", "PHI.L",
    "PHNX.L", "PIC.L",  "PNN.L",  "POLR.L", "POST.L", "PPB.L",  "PRU.L",
    "PSON.L", "QQ.L",   "RCP.L",  "RDL.L",  "RDW.L",  "REC.L",  "RECI.L",
    "RENT.L", "RGD.L",  "RHM.L",  "RICA.L", "RKT.L",  "RMG.L",  "RNK.L",
    "ROO.L",  "ROR.L",  "RPC.L",  "RPS.L",  "RQM.L",  "RTO.L",  "RWS.L",
    "SAFE.L", "SCT.L",  "SDR.L",  "SDY.L",  "SEE.L",  "SEPL.L", "SHB.L",
    "SHI.L",  "SHRS.L", "SIV.L",  "SKG.L",  "SLI.L",  "SMT.L",  "SNG.L",
    "SNWS.L", "SOLB.L", "SPE.L",  "SPL.L",  "SQZ.L",  "SRC.L",  "SRP.L",
    "SSE.L",  "SSYS.L", "STJ.L",  "STO.L",  "SVS.L",  "SXS.L",  "TATE.L",
    "TLW.L",  "TMG.L",  "TNC.L",  "TPK.L",  "TRN.L",  "TUI.L",  "TUNE.L",
    "TVS.L",  "UBM.L",  "UDG.L",  "ULE.L",  "UPR.L",  "UTG.L",  "VCT.L",
    "VED.L",  "VIP.L",  "VMH.L",  "VMUK.L", "VOD.L",  "VTY.L",  "VVO.L",
    "WAG.L",  "WEIR.L", "WG.L",   "WHR.L",  "WIX.L",  "WKP.L",  "WPS.L",
    "WTB.L",  "XAR.L",  "XPP.L",  "YCA.L",
]

FTSE100_TICKERS = [
    "AAF.L",  "AAL.L",  "ABF.L",  "ADM.L",  "AHT.L",  "ANTO.L", "AZN.L",
    "BA.L",   "BARC.L", "BATS.L", "BKG.L",  "BLND.L", "BNZL.L", "BP.L",
    "BRBY.L", "BT.L",   "CCH.L",  "CNA.L",  "CPG.L",  "CRDA.L", "CRH.L",
    "DCC.L",  "DGE.L",  "DPH.L",  "EDV.L",  "ENT.L",  "EXPN.L", "EZJ.L",
    "FCIT.L", "FLTR.L", "FRAS.L", "FRES.L", "GFS.L",  "GLEN.L", "GSK.L",
    "HIK.L",  "HL.L",   "HLMA.L", "HLN.L",  "HSBA.L", "HSX.L",  "IAG.L",
    "ICG.L",  "IHG.L",  "III.L",  "IMB.L",  "IMI.L",  "INF.L",  "ITRK.L",
    "JD.L",   "KGF.L",  "LAND.L", "LGEN.L", "LLOY.L", "LMP.L",  "LSEG.L",
    "MKS.L",  "MNDI.L", "MNG.L",  "MRO.L",  "NG.L",   "NXT.L",  "OCDO.L",
    "PHNX.L", "PRU.L",  "PSH.L",  "PSON.L", "PSN.L",  "RB.L",   "RCP.L",
    "REL.L",  "RIO.L",  "RKT.L",  "RMV.L",  "RR.L",   "RTO.L",  "SBRY.L",
    "SDR.L",  "SGE.L",  "SHEL.L", "SKG.L",  "SKY.L",  "SMDS.L", "SMIN.L",
    "SMT.L",  "SN.L",   "SPX.L",  "SSE.L",  "STAN.L", "STJ.L",  "SVT.L",
    "TSCO.L", "TUI.L",  "ULVR.L", "UTG.L",  "UU.L",   "VOD.L",  "WPP.L",
    "WTB.L",
]

_INDEX_TICKERS = {
    'FTSE 250': FTSE250_TICKERS,
    'FTSE 100': FTSE100_TICKERS,
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

    Tickers are sourced from the hardcoded lists above — no scraping,
    no external dependencies beyond yfinance.  Update the lists after
    each quarterly FTSE Russell rebalance (March, June, September, December).

    Parameters
    ----------
    start : str   — YYYY-MM-DD.  6 months back is recommended for swing analysis.
    end   : str   — YYYY-MM-DD
    index : str   — 'FTSE 250' or 'FTSE 100'
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
