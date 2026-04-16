#!/usr/bin/env python3
"""CLI for isolated Pub/Sub readiness checks."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from pubsub_readiness import (
    ENV_CREDENTIALS_PATH,
    ENV_PROJECT_ID,
    ENV_SUBSCRIPTION_ID,
    check_pubsub_readiness,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Pub/Sub consume readiness")
    parser.add_argument("--project-id", default=os.environ.get(ENV_PROJECT_ID))
    parser.add_argument("--subscription-id", default=os.environ.get(ENV_SUBSCRIPTION_ID))
    parser.add_argument("--credentials-path", default=os.environ.get(ENV_CREDENTIALS_PATH))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = check_pubsub_readiness(
        project_id=args.project_id,
        subscription_id=args.subscription_id,
        credentials_path=args.credentials_path,
    )

    stream = sys.stdout if result.exit_code == 0 else sys.stderr
    print(result.message, file=stream)
    for diagnostic in result.diagnostics:
        print(f"- {diagnostic}", file=stream)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
