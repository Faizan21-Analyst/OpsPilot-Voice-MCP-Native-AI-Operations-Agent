"""
check_qdrant_sources.py

Scrolls through every point in the Qdrant collection and groups them by
their source document, so you can see at a glance which of your policy
docs actually made it into the index (and how many chunks each got).

Run with:
    python -m check_qdrant_sources
"""

import os
from collections import defaultdict
from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

COLLECTION_NAME = "policy_docs_hybrid"  # must match retriever.py

# The 6 files you uploaded — edit this list if the actual filenames differ
EXPECTED_SOURCES = [
    "access_control_policy.md",
    "incident_response_policy.md",
    "password_policy.md",
    "production_monitoring_guide.md",
    "software_request_policy.md",
    "vpn_remote_access_policy.md",
]


def main():
    qdrant_url = os.getenv("QDRANT_HTTP")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")

    if not qdrant_url:
        print("ERROR: QDRANT_HTTP not set in environment/.env")
        return

    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)

    collections = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in collections:
        print(f"ERROR: collection '{COLLECTION_NAME}' does not exist. Found: {collections}")
        return

    info = client.get_collection(COLLECTION_NAME)
    print(f"Collection '{COLLECTION_NAME}': {info.points_count} total points\n")

    counts = defaultdict(int)
    sample_payload = None
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        for point in points:
            payload = point.payload or {}
            if sample_payload is None:
                sample_payload = payload  # keep one example for shape-debugging

            metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
            source = metadata.get("source", "<no source field>")
            counts[source] += 1

        if offset is None:
            break

    print("Chunks per indexed source:")
    print("-" * 60)
    for source, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {count:4d}  {source}")

    print()
    print("Coverage check against expected files:")
    print("-" * 60)

    # Indexed sources may be full paths (e.g. "docs/vpn_remote_access_policy.md")
    # rather than bare filenames, so match by suffix rather than exact string.
    indexed_sources = list(counts.keys())
    missing = []
    for expected in EXPECTED_SOURCES:
        found = any(str(s).endswith(expected) for s in indexed_sources)
        status = "FOUND" if found else "MISSING"
        print(f"  [{status:7s}] {expected}")
        if not found:
            missing.append(expected)

    print()
    if missing:
        print(f"⚠ {len(missing)} of {len(EXPECTED_SOURCES)} expected files are NOT indexed: {missing}")
    else:
        print("✓ All expected files are represented in the collection.")

    if sample_payload is not None:
        print()
        print("Sample payload shape (for reference):")
        print(f"  keys: {list(sample_payload.keys())}")
        if isinstance(sample_payload.get("metadata"), dict):
            print(f"  metadata keys: {list(sample_payload['metadata'].keys())}")


if __name__ == "__main__":
    main()