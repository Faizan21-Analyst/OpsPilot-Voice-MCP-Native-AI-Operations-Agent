import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, Request, HTTPException

from src.auth.dependencies import require_admin
from src.auth.models import TokenPayload
from src.core.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.post("/admin/ingest")
async def ingest_document(
    request: Request,
    file: UploadFile = File(...),
    principal: TokenPayload = Depends(require_admin),
):
    container = request.app.state.container

    if not file.filename.endswith((".md", ".pdf")):
        raise HTTPException(status_code=400, detail="Only .md or .pdf files are supported")

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = await container.rag_pipeline.index_document(tmp_path)
        logger.info("admin_ingest", filename=file.filename, admin=principal.user_id, chunks=result["chunks"])
        return {"success": True, "filename": file.filename, "chunks": result["chunks"]}
    except Exception as e:
        logger.error("admin_ingest_failed", filename=file.filename, admin=principal.user_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)