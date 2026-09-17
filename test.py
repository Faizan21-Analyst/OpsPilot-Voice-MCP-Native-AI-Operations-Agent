import os
import time
import asyncio
from dotenv import load_dotenv

# Import your new pipeline class
from src.mcp_servers.docs.rag.pipeline import RAGPipeline

# Load environment variables
load_dotenv()

async def main():
    llama_key = os.getenv('LLAMA_PARSER')
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    qdrant_url = os.getenv("QDRANT_HTTP")
    
    if not llama_key:
        raise ValueError("LLAMA_PARSER key not found! Check your .env file.")
    if not qdrant_url or not qdrant_api_key:
        raise ValueError("Qdrant credentials not found! Check QDRANT_HTTP and QDRANT_API_KEY.")

    # 1. Initialize the unified pipeline
    print("Initializing RAG Pipeline...")
    pipeline = RAGPipeline(
        llama_parse_api_key=llama_key,
        qdrant_url=qdrant_url,
        qdrant_api_key=qdrant_api_key
    )
    
    # 2. Index the Document with Latency Tracking
    pdf_path = "C:/Users/ligio/OneDrive/Desktop/OpsPilot-Voice-MCP-Native-AI-Operations-Agent/Prometheus_Grafana_Production_Monitoring_Guide.pdf"
    
    print("\nStarting Document Indexing... (This will parse, chunk, embed, and store)")
    
    # Start timer for indexing
    index_start_time = time.perf_counter()
    index_stats = await pipeline.index_document(file_path=pdf_path)
    index_latency = time.perf_counter() - index_start_time
    
    print(f"Indexing Complete! Stored {index_stats['chunks']} chunks from {index_stats['source']}")
    print(f"⏱️ Indexing Latency: {index_latency:.2f} seconds")

    # 3. Test the Search with Latency Tracking
    test_query = "how to install Prometheus python client?"
    print(f"\nSearching for: '{test_query}'")
    
    # Start timer for searching
    search_start_time = time.perf_counter()
    results = await pipeline.search(
        query=test_query,
        top_k=5,
    )
    search_latency = time.perf_counter() - search_start_time
    
    print("\n--- FINAL RERANKED RESULTS ---")
    for i, result in enumerate(results, 1):
        print(f"\nResult {i}:")
        # Print both the final reranker score and the original Qdrant DB score
        print(f"Rerank Score: {result.get('rerank_score', 'N/A'):.4f} | Original DB Score: {result.get('score', 'N/A'):.4f}")
        # Print a snippet of the content
        print(f"Content Preview: {result['content'][:250]}...")
        print(f"Metadata: {result['metadata']}")
        
    print(f"\n⏱️ Search Latency: {search_latency:.4f} seconds")
    print(f"⏱️ Total Execution Time: {(index_latency + search_latency):.2f} seconds")

if __name__ == "__main__":
    asyncio.run(main())