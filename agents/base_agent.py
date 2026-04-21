from abc import ABC, abstractmethod
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

class BaseAgent(ABC):
    """
    Every agent in the pipeline will inherit from this

    Attributes
    ----------
    name : str
        Human-readable name of the agent, used in logging.
    """

    name: str = 'BaseAgent'

    def run(self, ticker: str, data) -> dict:
        """
        Public entry point for processing a single ticker.

        Parameters
        ----------
        ticker : str
            The stock ticker symbol to process (e.g. 'III.L').
        data : any
            Input data for the agent. Type varies by agent,
            typically a pd.DataFrame or a dict from a prior agent.

        Returns
        -------
        dict
            Processing result. Structure is defined by each
            subclass. On failure, returns the fallback dict
            from _fallback().
        """
        logger.info(f"[{self.name}] Processing {ticker}")
        try:
            result = self._process(ticker, data)
            logger.info(f"[{self.name}] - {ticker} - Success")
            return result
        except Exception as e:
            logger.error(f"[{self.name}] - {ticker} - Failure: {e}")
            return self._fallback(ticker, e)

    @abstractmethod
    def _process(self, ticker: str, data) -> dict:
        """Core logic - implemented by each agent."""
        pass

    def _fallback(self, ticker: str, error: Exception) -> dict:
        """Default fallback if processing fails. Can be overridden."""
        return {"ticker": ticker, "error": str(error), "status": "failed"}


