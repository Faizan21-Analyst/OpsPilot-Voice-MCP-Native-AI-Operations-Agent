from phoenix.otel import register
from openinference.instrumentation.langchain import LangChainInstrumentor
from openinference.instrumentation.litellm import LiteLLMInstrumentor

from src.core.logging import get_logger

logger = get_logger(__name__)


def setup_tracing(endpoint: str = "http://localhost:6006/v1/traces", project_name: str = "opspilot"):
    try:
        tracer_provider = register(
            project_name=project_name,
            endpoint=endpoint,
        )
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        LiteLLMInstrumentor().instrument(tracer_provider=tracer_provider)
        logger.info("tracing_initialized", endpoint=endpoint, project=project_name)
    except Exception as e:
        logger.warning("tracing_setup_failed", error=str(e))