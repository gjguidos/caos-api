"""Read-only ownership inventory over a trusted JSON array export.

Run from checkout: python -m src.domains.agentverse.security.ownership_inventory export.json --source coll_agents
For registry exports, each row must carry key and metadata (RegistryItem dump).
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from src.domains.agentverse.entities.resource_ownership import inventory_ownership
from src.domains.agentverse.security.ownership_review import review_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--review-manifest", action="store_true")
    args = parser.parse_args()
    content = args.export.read_bytes()
    rows = json.loads(content)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        parser.error("Export must be a JSON array of documents")
    records = [
        {**row["metadata"], "key": row.get("key")}
        if args.source.startswith("registry:") and isinstance(row.get("metadata"), dict)
        else row
        for row in rows
    ]
    inventory = inventory_ownership(records, source=args.source)
    print(
        json.dumps(
            {
                "source": args.source,
                "sha256": hashlib.sha256(content).hexdigest(),
                "counts": dict(Counter(row["status"] for row in inventory)),
                "records": inventory,
                **(
                    {"review_manifest": review_manifest(rows, source=args.source)}
                    if args.review_manifest
                    else {}
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
