"""Structured JSON logging with defensive redaction of secrets."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_SENSITIVE_KEY = re.compile(
    r"pass(word)?|secret|token|authorization|api_?key|cookie|session", re.IGNORECASE
)
_SENSITIVE_VALUE = re.compile(
    r"(token\s+)[^\s:]+:[^\s\"']+|(postgres(?:ql)?(?:\+\w+)?://[^:/]+:)"
    r"[^@]+@",
    re.IGNORECASE,
)


def redact(value: object) -> object:
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub(lambda m: (m.group(1) or m.group(2)) + "***", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _STD or key.startswith("_"):
                continue
            payload[key] = "***" if _SENSITIVE_KEY.search(key) else redact(value)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_procurelens", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._procurelens = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
