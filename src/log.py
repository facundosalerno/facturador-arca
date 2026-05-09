import logging
from typing import Any, Callable, Optional

from rich.text import Text

LEVEL_STYLES = {
    "DEBUG": "dim",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
}



class TextualLogHandler(logging.Handler):
    def __init__(self, write_fn: Callable[[Text], Any]) -> None:
        super().__init__()
        self._write_fn = write_fn

    def emit(self, record: logging.LogRecord) -> None:
        style = LEVEL_STYLES.get(record.levelname, "white")
        self._write_fn(Text(self.format(record), style=style))



def create_logger(level: str, name: Optional[str] = "default", handler: Optional[logging.Handler] = None) -> logging.Logger:
    log = logging.getLogger(name)
    # Si la instancia ya existia, se limpian los handlers
    log.handlers.clear()
    log.filters.clear()
    log.setLevel(level.upper())

    formatter = logging.Formatter('%(levelname)s: %(asctime)s - %(message)s')
    if handler is None:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    log.addHandler(handler)
    return log