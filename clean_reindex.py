"""
clean_reindex.py

Deletes the existing 'policy_docs_hybrid' Qdrant collection (dropping the
stray PDF and any half-overwritten points along with it) and reindexes
all 6 real policy docs from data/policy_docs/ from a clean slate.

Run this AFTER dropping in the fixed retriever.py (uuid4 point IDs).

Run with:
    python -m clean_reindex
"""

import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from qdrant_client import QdrantClient

from src.mcp_servers.docs.rag.pipeline import RAGPipeline

load_dotenv()

COLLECTION_NAME = "policy_docs_hybrid"
DOCS_DIR = Path("data/policy_docs")

EXPECTED_FILES = [
    "access_control_policy.md",
    "incident_response_policy.md",
    "password_policy.md",
    "production_monitoring_guide.md",
    "software_request_policy.md",
    "vpn_remote_access_policy.md",
]


async def main():
    qdrant_url = os.getenv("QDRANT_HTTP")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    parse_api_key = os.getenv("LLAMA_PARSER")

    if not qdrant_url:
        print("ERROR: QDRANT_HTTP not set in environment/.env")
        return

    # --- Wipe the collection so we start clean (drops the stray PDF too) ---
    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    collections = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME in collections:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing collection '{COLLECTION_NAME}'.")
    else:
        print(f"No existing collection '{COLLECTION_NAME}' to delete.")

    # --- Rebuild pipeline (this recreates the empty collection) ---
    pipeline = RAGPipeline(
        llama_parse_api_key=parse_api_key,
        qdrant_url=qdrant_url,
        qdrant_api_key=qdrant_api_key,
    )

    # --- Verify all 6 expected files exist on disk before indexing ---
    missing_on_disk = [f for f in EXPECTED_FILES if not (DOCS_DIR / f).exists()]
    if missing_on_disk:
        print(f"ERROR: these expected files are not in {DOCS_DIR}: {missing_on_disk}")
        print("Fix the paths/filenames above before continuing.")
        return

    # --- Index each file ---
    for filename in EXPECTED_FILES:
        file_path = str(DOCS_DIR / filename)
        print(f"Indexing {file_path} ...")
        result = await pipeline.index_document(file_path)
        print(f"  -> {result['chunks']} chunks")

    # --- Verify final state ---
    info = client.get_collection(COLLECTION_NAME)
    print()
    print(f"Done. Collection '{COLLECTION_NAME}' now has {info.points_count} total points.")
    print("Run `python -m check_qdrant_sources` again to confirm all 6 files show as FOUND.")


if __name__ == "__main__":
    asyncio.run(main())