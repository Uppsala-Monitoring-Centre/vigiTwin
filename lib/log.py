import logging
import logging.config

LOGCFG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s - %(levelname)s - %(module)s - %(lineno)d - %(message)s"
        }
    },
    "handlers": {
        "queryLogger": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "level": "INFO",
            "formatter": "standard",
            "filename": "../log/log_queries.log",
            "when": "midnight",
            "interval": 1,
            "backupCount": 30,
        },
        "defaultLogger": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            # "level": "DEBUG",
            "level": "INFO",
            "formatter": "standard",
            "filename": "../log/log.log",
            "when": "midnight",
            "interval": 1,
            "backupCount": 30,
        },
    },
    "loggers": {
        "log_queries": {
            "handlers": ["queryLogger"],
            "level": "INFO",
            "propagate": False,
        },
        "log_default": {
            "handlers": ["defaultLogger"],
            "level": "DEBUG",
            # "level": "INFO",
            "propagate": False,
        },
    },
}

logging.config.dictConfig(LOGCFG)


# Function to add a new log entry
def add_log_entry(entry, logger="log_queries"):
    logger = logging.getLogger(logger)
    logger.info(entry)
