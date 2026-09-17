from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from src.core.logging import get_logger

logger = get_logger(__name__)


class DocumentChunker:

    def __init__(
        self,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
    ):
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "Header 1"),
                ("##", "Header 2"),
                ("###", "Header 3"),
            ],
            strip_headers=False,
        )

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def chunk(self, document: str, source: str):

        sections = self.header_splitter.split_text(document)

        chunks = self.text_splitter.split_documents(sections)

        for chunk in chunks:
            chunk.metadata["source"] = source

        logger.info(
            "document_chunked",
            source=source,
            chunks=len(chunks),
        )

        return chunks