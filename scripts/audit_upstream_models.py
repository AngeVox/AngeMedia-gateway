#!/usr/bin/env python3
"""Print a read-only upstream model drift report as JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.upstream_audit import AUDIT_SPECS, audit_upstream_models  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare AngeMedia's static model catalog with fixed official upstream model-list endpoints.",
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=[spec.provider_id for spec in AUDIT_SPECS],
        help="Limit the report to one provider; repeat to select multiple providers.",
    )
    parser.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="Exit 2 when a configured local model is no longer present upstream.",
    )
    args = parser.parse_args()

    report = audit_upstream_models(provider_ids=args.provider)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if args.fail_on_missing:
        for provider in report["providers"].values():
            if provider.get("status") == "ok" and provider.get("missing_upstream"):
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
