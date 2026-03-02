from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    @abstractmethod
    def evaluate(self, prices: pd.Series) -> str:
        """Return 'BUY', 'SELL', or 'HOLD'."""
        pass
