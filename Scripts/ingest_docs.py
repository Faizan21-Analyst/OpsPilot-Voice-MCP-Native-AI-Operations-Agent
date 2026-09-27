import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from src.mcp_servers.docs.rag.pipeline import RAGPipeline

load_dotenv()


async def main():
    rag = RAGPipeline(
        llama_parse_api_key=os.getenv("LLAMA_PARSER"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY"),
        qdrant_url=os.getenv("QDRANT_HTTP"),
    )

    docs_folder = Path("data/policy_docs")
    files = sorted(docs_folder.glob("*.md"))  # adjust extension if you use .pdf instead

    if not files:
        print(f"No files found in {docs_folder.resolve()}")
        return

    succeeded = []
    failed = []

    for file_path in files:
        try:
            result = await rag.index_document(str(file_path))
            print(f"OK   {file_path.name} -> {result['chunks']} chunks")
            succeeded.append(file_path.name)
        except Exception as e:
            print(f"FAIL {file_path.name}: {e}")
            failed.append(file_path.name)

    print()
    print(f"Done: {len(succeeded)} succeeded, {len(failed)} failed")
    if failed:
        print("Failed files:", failed)


if __name__ == "__main__":
    asyncio.run(main())