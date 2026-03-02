import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class SymbolConfig:
    name: str
    position_size: int


@dataclass
class StrategyConfig:
    name: str
    parameters: dict
    symbols: list[SymbolConfig]


@dataclass
class GlobalSettings:
    profit_target_pct: float
    stop_loss_pct: float
    max_daily_loss_usd: float = 500.0
    max_hold_minutes: int = 60

    def get(self, key: str, default=None):
        return getattr(self, key, default)


@dataclass
class AppSettings:
    global_settings: GlobalSettings
    strategies: dict[str, StrategyConfig]


def load_settings(path: str = "settings.json") -> AppSettings:
    raw = json.loads(Path(path).read_text())

    g = raw["global_settings"]
    global_cfg = GlobalSettings(
        profit_target_pct=g["profit_target_pct"],
        stop_loss_pct=g["stop_loss_pct"],
        max_daily_loss_usd=g.get("max_daily_loss_usd", 500.0),
        max_hold_minutes=g.get("max_hold_minutes", 60),
    )

    strategies = {}
    for s in raw["strategies"]:
        symbols = [SymbolConfig(**sym) for sym in s["symbols"]]
        strategies[s["name"]] = StrategyConfig(
            name=s["name"],
            parameters=s["parameters"],
            symbols=symbols,
        )

    return AppSettings(global_settings=global_cfg, strategies=strategies)


def get_strategy(name: str, path: str = "settings.json") -> StrategyConfig:
    settings = load_settings(path)
    if name not in settings.strategies:
        raise KeyError(
            f"Strategy '{name}' not found in {path}. "
            f"Available: {list(settings.strategies.keys())}"
        )
    return settings.strategies[name]
