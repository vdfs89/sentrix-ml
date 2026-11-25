"""Keep test runs out of logs/pipeline.jsonl, which is versioned evidence of the real pipeline."""

import tempfile
from pathlib import Path

from src import config

config.PIPELINE_LOG_FILE = Path(tempfile.gettempdir()) / "sentrix_tests_pipeline.jsonl"
