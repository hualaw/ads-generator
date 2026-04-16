import json
import logging


logger = logging.getLogger("ads_generator")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def log_event(level: str, event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    message = json.dumps(payload, ensure_ascii=True, default=str)
    if level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    else:
        logger.info(message)


def log_exception(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    message = json.dumps(payload, ensure_ascii=True, default=str)
    logger.exception(message)