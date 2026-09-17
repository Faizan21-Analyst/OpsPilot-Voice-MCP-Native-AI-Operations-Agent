from pathlib import Path 
from llama_parse import LlamaParse
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

class DocumentParser:
    def __init__(self, api_key):
        self.llama_parser = LlamaParse(
            api_key=api_key,
            result_type='markdown'
        )

    async def parse(self, path: str):
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        logger.info("document_parsing_started", file=str(path))
        
        documents = await self.llama_parser.aload_data(str(path))

        content = "\n\n".join(
            document.text
            for document in documents
            if document.text
        )
        
        logger.info(
            "document_parsing_completed",
            file=str(path),
            characters=len(content),
            documents=len(documents),
        )

        return content