"""Structured (JSON Lines) logging shared by every pipeline stage.

Each record carries run_id, stage, status, duration_ms and arbitrary context fields, so the
log file can be loaded as a table by the dashboard, shipped to Datadog/Loki/CloudWatch, or
grepped by an operator.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "run_id": RUN_ID,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "ctx", {}) or {})
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = getattr(record, "ctx", {}) or {}
        extra = " ".join(f"{k}={v}" for k, v in ctx.items() if k not in {"stage"})
        stage = ctx.get("stage", "-")
        return f"[{record.levelname:<8}] {stage:<22} {record.getMessage()} {extra}".rstrip()


def get_logger(name: str = "credit") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    config.ensure_dirs()
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(config.PIPELINE_LOG_FILE, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(ConsoleFormatter())
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, level: int, message: str, **ctx: Any) -> None:
    logger.log(level, message, extra={"ctx": ctx})


@contextmanager
def stage(logger: logging.Logger, name: str, **ctx: Any) -> Iterator[dict[str, Any]]:
    """Time a pipeline stage and emit started/success/failed events.

    The yielded dict can be enriched by the caller (e.g. rows processed); its content is
    attached to the final event.
    """
    info: dict[str, Any] = {}
    start = time.perf_counter()
    log_event(logger, logging.INFO, "stage started", stage=name, status="started", **ctx)
    try:
        yield info
    except Exception as exc:  # noqa: BLE001 - re-raised after logging
        duration = round((time.perf_counter() - start) * 1000, 2)
        log_event(logger, logging.ERROR, f"stage failed: {exc}", stage=name, status="failed",
                  duration_ms=duration, error_type=type(exc).__name__, **ctx, **info)
        raise
    duration = round((time.perf_counter() - start) * 1000, 2)
    info["duration_ms"] = duration
    status = info.pop("status", "success")
    log_event(logger, logging.INFO, "stage finished", stage=name, status=status, **ctx, **info)
