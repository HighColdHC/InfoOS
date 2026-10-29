"""Preview or apply the bounded repairs identified by the September audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.infrastructure.storage.audit_repair import AuditRepairRepository
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.storage.database import default_database_path, ensure_database_initialized


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path("."))
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        initialize_runtime_storage(args.base_dir)
    else:
        ensure_database_initialized(default_database_path(args.base_dir))
    repo = AuditRepairRepository(args.base_dir)
    plan = repo.plan(limit=max(1,min(args.limit,5000)))
    summary = {key:len(rows) for key,rows in plan.items()}
    summary.update(mode="apply" if args.apply else "preview")
    if args.apply:
        summary.update(repo.apply(plan))
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == "__main__":
    main()
