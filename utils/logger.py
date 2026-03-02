import sys
from pathlib import Path
from loguru import logger


def setup_logger(log_dir: str = "logs", strategy_name: str = "bot") -> None:
    """
    Configure loguru sinks:
      - Console: INFO and above, colorized
      - File:    TRACE and above, rotating daily, retained 30 days
    """
    Path(log_dir).mkdir(exist_ok=True)

    # Remove default sink
    logger.remove()

    # Console — INFO+
    logger.add(
        sys.stdout,
        level="INFO",
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    )

    # File — TRACE+ with daily rotation
    logger.add(
        f"{log_dir}/{strategy_name}_{{time:YYYY-MM-DD}}.log",
        level="TRACE",
        rotation="00:00",
        retention="30 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
        enqueue=True,
    )
