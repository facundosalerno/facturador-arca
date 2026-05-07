import logging
from typing import Optional

#class LogContext(logging.Filter):
#    def filter(self, record: logging.LogRecord) -> bool:
#        #record.id = 1
#        return True

def create_logger(level: str, name: Optional[str] = "default") -> logging.Logger:
    log = logging.getLogger(name)
    # Si la instancia ya existia, se limpian los handlers
    log.handlers.clear()
    log.filters.clear()
    log.setLevel(level.upper())

    formatter = logging.Formatter('%(levelname)s: %(asctime)s - %(message)s')
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    #stream.addFilter(LogContext())
    log.addHandler(stream)
    return log