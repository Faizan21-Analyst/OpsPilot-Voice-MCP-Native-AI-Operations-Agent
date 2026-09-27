import os

# Silence MLflow's harmless "git executable not found" warnings.
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

import argparse
import asyncio
import json
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import mlflow

from deepeval import evaluate
from deepeval.metrics import (
    FaithfulnessMetric,
    AnswerRelevancyMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
)
from deepeval.test_case import LLMTestCase

try:
    # Newer deepeval versions expose both from deepeval.evaluate.
    from deepeval.evaluate import AsyncConfig, ErrorConfig
except ImportError:  # pragma: no cover - version compatibility shim
    from deepeval.evaluate import AsyncConfig

    try:
        from deepeval.evaluate.configs import ErrorConfig
    except ImportError:  # pragma: no cover
        ErrorConfig = None

from evals.judges.gemini_judge import GeminiJudge

from src.core.config import load_settings
from src.providers.llm.litellm_provider import LiteLLMProvider

from src.mcp_client.manager import MCPClientManager
from src.permissions.policy_engine import PolicyEngine
from src.agent.graph import build_agent_graph


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[1]

DATASET_PATH = BASE_DIR / "evals" / "datasets" / "golden.jsonl"


# ============================================================
# DATASET
# ============================================================

def load_dataset():

    cases = []

    with open(DATASET_PATH, "r", encoding="utf-8") as f:

        for line in f:

            line = line.strip()

            if not line:
                continue

            cases.append(json.loads(line))

    return cases


# ============================================================
# MESSAGE HELPERS
# ============================================================

def _message_role(message):

    if isinstance(message, dict):
        return message.get("role")

    return getattr(message, "role", None)


def _message_tool_name(message):

    if isinstance(message, dict):
        return message.get("name")

    return getattr(message, "name", None)


def _extract_tool_calls(message):

    if isinstance(message, dict):
        return message.get("tool_calls", []) or []

    return getattr(message, "tool_calls", []) or []


def _count_agent_turns(messages):

    count = 0

    for message in messages:

        if _message_role(message) == "assistant":
            count += 1

    return count


# ============================================================
# MCP RESULT NORMALIZATION
# ============================================================

def _unwrap_mcp_result(result):

    if result is None:
        return None

    if isinstance(
        result,
        (list, dict, str, int, float, bool)
    ):
        return result

    if getattr(result, "is_error", False):
        return None

    data = getattr(result, "data", None)

    if data is not None:
        return data

    structured = getattr(
        result,
        "structured_content",
        None,
    )

    if structured is not None:

        if (
            isinstance(structured, dict)
            and "result" in structured
        ):
            return structured["result"]

        return structured

    content = getattr(
        result,
        "content",
        None,
    )

    if not content:
        return None

    texts = []

    for item in content:

        text = getattr(item, "text", None)

        if text is not None:
            texts.append(text)

    if not texts:
        return None

    if len(texts) == 1:

        raw = texts[0]

        try:
            return json.loads(raw)

        except Exception:
            return raw

    return texts


# ============================================================
# RETRIEVAL NORMALIZATION
# ============================================================

def normalize_retrieval_results(result):

    result = _unwrap_mcp_result(result)

    if result is None:
        return []

    if isinstance(result, list):

        normalized = []

        for item in result:

            if (
                isinstance(item, dict)
                and "content" in item
            ):
                normalized.append(item)

        return normalized

    if isinstance(result, dict):

        for key in (
            "results",
            "documents",
            "data",
            "result",
        ):

            value = result.get(key)

            if isinstance(value, list):

                normalized = []

                for item in value:

                    if (
                        isinstance(item, dict)
                        and "content" in item
                    ):
                        normalized.append(item)

                if normalized:
                    return normalized

    return []


# ============================================================
# BUILD SYSTEM
# ============================================================

async def build_system():

    settings = load_settings()

    llm = LiteLLMProvider(
        settings.groq,
        settings.gemini,
        settings.litellm,
    )

    manager = MCPClientManager({
        "docs": "http://127.0.0.1:8001/mcp",
        "sql": "http://127.0.0.1:8002/mcp",
        "ops": "http://127.0.0.1:8003/mcp",
    })

    policy = PolicyEngine(
        "policies/tools.yaml"
    )

    agent = build_agent_graph(
        llm,
        manager,
        policy,
    )

    available_tools = await manager.discover_tools()

    return (
        agent,
        manager,
        policy,
        available_tools,
    )


# ============================================================
# RETRIEVAL CASE
# ============================================================

async def run_retrieval_case(
    case,
    manager,
    llm,
):

    try:

        result = await manager.call_tool(
            "docs",
            "search_policy_docs",
            {
                "query": case["query"]
            },
        )

        retrieval_results = normalize_retrieval_results(
            result
        )

        contexts = [
            item["content"]
            for item in retrieval_results
            if (
                isinstance(item, dict)
                and item.get("content")
            )
        ]

        if not contexts:

            print(
                f"  [{case['id']}] "
                "WARNING: no retrieval results"
            )

            return None

        context_text = "\n\n".join(
            contexts
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an IT operations policy assistant. "
                    "Answer the user's question using only the "
                    "provided policy context. "
                    "Answer only what was asked. If the relevant "
                    "policy rule also covers other systems or "
                    "tools not mentioned in the question, do not "
                    "fold them into your main sentence - lead with "
                    "the direct answer to what was asked, and only "
                    "add the wider rule afterward if it changes the "
                    "answer. "
                    "If the correct answer is a command, query, or "
                    "code snippet (for example a PromQL expression), "
                    "return only that exact string with no "
                    "introductory sentence or wrapping - it should "
                    "be copyable as-is. "
                    "If the context does not contain enough "
                    "information, say that the policy does not "
                    "provide enough information."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Policy context:\n\n"
                    f"{context_text}\n\n"
                    f"Question:\n{case['query']}"
                ),
            },
        ]

        response = await llm.generate(
            messages=messages
        )

        actual_output = (
            response.content
            if response.content
            else ""
        )

        if not actual_output.strip():

            print(
                f"  [{case['id']}] "
                "WARNING: LLM produced empty answer"
            )

            return None

        return LLMTestCase(
            input=case["query"],
            actual_output=actual_output,
            expected_output=case[
                "reference_answer"
            ],
            retrieval_context=contexts,
        )

    except Exception as e:

        print(
            f"  [{case['id']}] "
            f"retrieval failed: {e}"
        )

        traceback.print_exc()

        return None


# ============================================================
# NO ANSWER
# ============================================================

async def run_no_answer_case(
    case,
    manager,
):

    try:

        result = await manager.call_tool(
            "docs",
            "search_policy_docs",
            {
                "query": case["query"]
            },
        )

        retrieval_results = normalize_retrieval_results(
            result
        )

        actual_count = len(
            retrieval_results
        )

        expected_count = 0

        passed = (
            actual_count
            == expected_count
        )

        details = (
            f"got {actual_count} results, "
            f"expected {expected_count}"
        )

        return (
            case["type"],
            case["id"],
            passed,
            details,
        )

    except Exception as e:

        return (
            case["type"],
            case["id"],
            False,
            f"exception: {e}",
        )


# ============================================================
# TOOL SELECTION
# ============================================================

async def run_tool_selection_case(
    case,
    agent,
    manager,
    available_tools,
):

    try:

        config = {
            "configurable": {
                "thread_id":
                    f"eval-tool-{case['id']}"
            }
        }

        initial_state = {
            "messages": [
                {
                    "role": "user",
                    "content": case["query"],
                }
            ],
            "session_id":
                f"eval-{case['id']}",
            "principal":
                case.get(
                    "principal",
                    {
                        "user_id": "E001",
                        "role": "employee",
                    },
                ),
            "available_tools":
                available_tools,
            "blocked": False,
            "tools_called_this_turn": [],
        }

        result = await agent.ainvoke(initial_state, config=config)

        if "__interrupt__" in result:
            from langgraph.types import Command
            result = await agent.ainvoke(
                Command(resume={"approved": True}),
                config=config,
            )

        messages = result.get("messages", [])

        called_tools = set()

        for message in messages:

            for call in _extract_tool_calls(
                message
            ):

                name = (
                    call.get("name")
                    if isinstance(
                        call,
                        dict,
                    )
                    else getattr(
                        call,
                        "name",
                        None,
                    )
                )

                if name:
                    called_tools.add(name)

            if (
                _message_role(message)
                == "tool"
            ):

                tool_name = (
                    _message_tool_name(
                        message
                    )
                )

                if tool_name:
                    called_tools.add(
                        tool_name
                    )

        expected_tools = set(
            case.get(
                "expected_tools",
                [],
            )
        )

        forbidden_tools = set(
            case.get(
                "forbidden_tools",
                [],
            )
        )

        missing_expected = (
            expected_tools
            - called_tools
        )

        forbidden_violated = (
            forbidden_tools
            & called_tools
        )

        passed = (
            not missing_expected
            and not forbidden_violated
        )

        details = (
            f"called={called_tools}, "
            f"missing_expected={missing_expected}, "
            f"forbidden_violated={forbidden_violated}"
        )

        return (
            case["type"],
            case["id"],
            passed,
            details,
        )

    except Exception as e:

        return (
            case["type"],
            case["id"],
            False,
            f"exception: {e}",
        )


# ============================================================
# PERMISSION
#
# PolicyEngine.evaluate() signature (from policy_engine.py):
#   evaluate(tool_name, employee_id, requested_by, requester_role)
#
# - tool_name       -> case["policy_tool"]
# - employee_id     -> case["target_employee_id"] (whose access is
#                       being acted on)
# - requested_by    -> case["principal"]["user_id"] (who is asking)
# - requester_role  -> case["principal"]["role"]
# ============================================================

def _build_policy_call_kwargs(case):

    principal = case.get(
        "principal",
        {"user_id": "E001", "role": "employee"},
    )

    return {
        "tool_name": case["policy_tool"],
        "employee_id": case["target_employee_id"],
        "requested_by": principal.get("user_id", "E001"),
        "requester_role": principal.get("role", "employee"),
    }


async def run_permission_case(
    case,
    policy,
):

    try:

        call_kwargs = _build_policy_call_kwargs(
            case,
        )

        decision = policy.evaluate(
            **call_kwargs
        )

        expected = case[
            "expected_decision"
        ]

        actual_allowed = (
            decision.allowed
        )

        actual_requires_approval = (
            decision.requires_approval
        )

        if isinstance(
            expected,
            dict,
        ):

            expected_allowed = (
                expected.get("allowed")
            )

            expected_approval = (
                expected.get(
                    "requires_approval"
                )
            )

            passed = (
                actual_allowed
                == expected_allowed
                and (
                    expected_approval
                    is None
                    or actual_requires_approval
                    == expected_approval
                )
            )

        else:

            expected_lower = str(
                expected
            ).lower()

            if expected_lower in (
                "allow",
                "allowed",
                "true",
            ):

                passed = actual_allowed

            elif expected_lower in (
                "deny",
                "denied",
                "false",
            ):

                passed = not actual_allowed

            elif expected_lower in (
                "approval",
                "requires_approval",
            ):

                passed = (
                    actual_requires_approval
                )

            else:
                passed = False

        details = (
            f"got allowed={actual_allowed}, "
            f"requires_approval="
            f"{actual_requires_approval}, "
            f"reason={decision.reason}, "
            f"called_with={call_kwargs}"
        )

        return (
            case["type"],
            case["id"],
            passed,
            details,
        )

    except Exception as e:

        return (
            case["type"],
            case["id"],
            False,
            f"exception: {e}",
        )


# ============================================================
# EFFICIENCY
# ============================================================

async def run_efficiency_case(
    case,
    agent,
    manager,
    available_tools,
):

    try:

        config = {
            "configurable": {
                "thread_id":
                    f"eval-eff-{case['id']}"
            }
        }

        initial_state = {
            "messages": [
                {
                    "role": "user",
                    "content": case["query"],
                }
            ],
            "session_id":
                f"eval-{case['id']}",
            "principal":
                case.get(
                    "principal",
                    {
                        "user_id": "E001",
                        "role": "employee",
                    },
                ),
            "available_tools":
                available_tools,
            "blocked": False,
            "tools_called_this_turn": [],
        }

        result = await agent.ainvoke(
            initial_state,
            config=config,
        )

        messages = result.get(
            "messages",
            [],
        )

        called_tools = []

        for message in messages:

            for call in _extract_tool_calls(
                message
            ):

                name = (
                    call.get("name")
                    if isinstance(
                        call,
                        dict,
                    )
                    else getattr(
                        call,
                        "name",
                        None,
                    )
                )

                if name:
                    called_tools.append(
                        name
                    )

            if (
                _message_role(message)
                == "tool"
            ):

                tool_name = (
                    _message_tool_name(
                        message
                    )
                )

                if tool_name:
                    called_tools.append(
                        tool_name
                    )

        tool_calls = len(
            called_tools
        )

        tries = _count_agent_turns(
            messages
        )

        max_tool_calls = case.get(
            "max_tool_calls"
        )

        max_tries = case.get(
            "max_tries"
        )

        passed = True

        if (
            max_tool_calls is not None
            and tool_calls > max_tool_calls
        ):
            passed = False

        if (
            max_tries is not None
            and tries > max_tries
        ):
            passed = False

        details = (
            f"tool_calls={tool_calls} "
            f"(max {max_tool_calls}), "
            f"tries={tries} "
            f"(max {max_tries}), "
            f"calls={called_tools}"
        )

        return (
            case["type"],
            case["id"],
            passed,
            details,
        )

    except Exception as e:

        return (
            case["type"],
            case["id"],
            False,
            f"exception: {e}",
        )


# ============================================================
# DETERMINISTIC RESULTS
# ============================================================

def print_deterministic_results(
    deterministic_results,
):

    print()
    print("=" * 70)
    print("DETERMINISTIC CHECKS")
    print("=" * 70)

    category_stats = defaultdict(
        lambda: {
            "passed": 0,
            "total": 0,
        }
    )

    for (
        category,
        case_id,
        passed,
        details,
    ) in deterministic_results:

        status = (
            "PASS"
            if passed
            else "FAIL"
        )

        print(
            f"[{status}] "
            f"({category}) "
            f"{case_id}: "
            f"{details}"
        )

        category_stats[
            category
        ]["total"] += 1

        if passed:

            category_stats[
                category
            ]["passed"] += 1

    total_passed = sum(
        x["passed"]
        for x in category_stats.values()
    )

    total_cases = sum(
        x["total"]
        for x in category_stats.values()
    )

    overall_rate = (
        total_passed / total_cases
        if total_cases
        else 0.0
    )

    print()
    print(
        f"{total_passed}/{total_cases} "
        "deterministic checks passed overall"
    )

    category_rates = {}

    for (
        category,
        stats,
    ) in category_stats.items():

        rate = (
            stats["passed"]
            / stats["total"]
            if stats["total"]
            else 0.0
        )

        category_rates[
            category
        ] = rate

        print(
            f"  {category}: "
            f"{rate * 100:.1f}% "
            f"({stats['passed']}/"
            f"{stats['total']})"
        )

    return (
        overall_rate,
        category_rates,
    )


# ============================================================
# DEEPEVAL
# ============================================================

def run_deepeval(
    retrieval_test_cases,
):

    if not retrieval_test_cases:

        print()
        print(
            "No retrieval test cases available. "
            "Skipping DeepEval."
        )

        return {}, "n/a"

    print()
    print("=" * 70)

    print(
        "DEEPEVAL RETRIEVAL / GENERATION "
        f"METRICS ({len(retrieval_test_cases)} cases)"
    )

    print("=" * 70)

    judge = None

    try:

        # ====================================================
        # GROQ JUDGE ONLY
        # ====================================================

        judge = GeminiJudge()

        print(
            f"Judge model: "
            f"{judge.get_model_name()}"
        )

        # ====================================================
        # SEQUENTIAL
        #
        # Important for your current free-tier setup.
        # ====================================================

        metrics = [

            FaithfulnessMetric(
                threshold=0.7,
                model=judge,
                async_mode=False,
            ),

            AnswerRelevancyMetric(
                threshold=0.7,
                model=judge,
                async_mode=False,
            ),

            ContextualPrecisionMetric(
                threshold=0.7,
                model=judge,
                async_mode=False,
            ),

            ContextualRecallMetric(
                threshold=0.7,
                model=judge,
                async_mode=False,
            ),
        ]

        print(
            "Running DeepEval sequentially..."
        )

        evaluate_kwargs = dict(
            async_config=AsyncConfig(
                run_async=False
            ),
        )

        if ErrorConfig is not None:

            # Don't let one bad/empty judge response (rate limit,
            # malformed JSON, etc.) abort every other test case's
            # scores along with it.
            evaluate_kwargs["error_config"] = ErrorConfig(
                ignore_errors=True,
                skip_on_missing_params=True,
            )

        eval_result = evaluate(
            retrieval_test_cases,
            metrics,
            **evaluate_kwargs,
        )

    except Exception as e:

        print()
        print(
            "DeepEval could not complete."
        )

        print(
            f"  {type(e).__name__}: {e}"
        )

        print()
        print(
            "No fake metric scores will be "
            "generated from a failed judge call."
        )

        traceback.print_exc()

        model_name = (
            judge.get_model_name()
            if judge is not None
            else "Groq judge unavailable"
        )

        return {}, model_name

    # ========================================================
    # EXTRACT SCORES
    # ========================================================

    retrieval_scores = {}

    try:

        test_results = getattr(
            eval_result,
            "test_results",
            None,
        )

        if test_results:

            metric_values = defaultdict(
                list
            )

            for test_result in test_results:

                for metric_data in getattr(
                    test_result,
                    "metrics_data",
                    [],
                ):

                    name = getattr(
                        metric_data,
                        "name",
                        None,
                    )

                    score = getattr(
                        metric_data,
                        "score",
                        None,
                    )

                    if (
                        name is not None
                        and score is not None
                    ):

                        metric_values[
                            name
                        ].append(
                            float(score)
                        )

            for (
                name,
                values,
            ) in metric_values.items():

                if values:

                    retrieval_scores[
                        name
                    ] = (
                        sum(values)
                        / len(values)
                    )

    except Exception as e:

        print(
            "Warning: could not extract "
            f"DeepEval scores: {e}"
        )

        traceback.print_exc()

    # ========================================================
    # PRINT
    # ========================================================

    if retrieval_scores:

        print()

        for (
            name,
            score,
        ) in retrieval_scores.items():

            print(
                f"  {name}: "
                f"{score:.4f}"
            )

    else:

        print()
        print(
            "DeepEval completed but no "
            "metric scores were extracted."
        )

    return (
        retrieval_scores,
        judge.get_model_name(),
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--experiment",
        default="OpsPilot-Evaluation",
    )

    args = parser.parse_args()

    # ========================================================
    # BUILD PRODUCTION SYSTEM
    # ========================================================

    print("Building system...")

    (
        agent,
        manager,
        policy,
        available_tools,
    ) = await build_system()

    settings = load_settings()

    llm = LiteLLMProvider(
        settings.groq,
        settings.gemini,
        settings.litellm,
    )

    # ========================================================
    # DATASET
    # ========================================================

    cases = load_dataset()

    print(
        f"Loaded {len(cases)} golden cases"
    )

    retrieval_test_cases = []

    deterministic_results = []

    # ========================================================
    # RUN CASES
    # ========================================================

    for case in cases:

        case_type = case["type"]

        if case_type == "retrieval":

            tc = await run_retrieval_case(
                case,
                manager,
                llm,
            )

            if tc is not None:

                retrieval_test_cases.append(
                    tc
                )

        elif case_type == "no_answer_expected":

            deterministic_results.append(
                await run_no_answer_case(
                    case,
                    manager,
                )
            )

        elif case_type == "tool_selection":

            deterministic_results.append(
                await run_tool_selection_case(
                    case,
                    agent,
                    manager,
                    available_tools,
                )
            )

        elif case_type == "permission":

            deterministic_results.append(
                await run_permission_case(
                    case,
                    policy,
                )
            )

        elif case_type == "efficiency":

            deterministic_results.append(
                await run_efficiency_case(
                    case,
                    agent,
                    manager,
                    available_tools,
                )
            )

    # ========================================================
    # DETERMINISTIC RESULTS
    # ========================================================

    (
        overall_det_rate,
        category_rates,
    ) = print_deterministic_results(
        deterministic_results
    )

    # ========================================================
    # DEEPEVAL
    # ========================================================

    (
        retrieval_scores,
        judge_model_name,
    ) = run_deepeval(
        retrieval_test_cases
    )

    # ========================================================
    # HEADLINE
    # ========================================================

    all_scores = [
        overall_det_rate
    ] + list(
        retrieval_scores.values()
    )

    headline = (
        sum(all_scores)
        / len(all_scores)
        if all_scores
        else 0.0
    )

    # ========================================================
    # MLFLOW
    # ========================================================

    experiment_name = args.experiment

    mlflow.set_experiment(
        experiment_name
    )

    run_name = (
        "eval_"
        + datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
    )

    with mlflow.start_run(
        run_name=run_name
    ):

        mlflow.log_param(
            "dataset_path",
            str(DATASET_PATH),
        )

        mlflow.log_param(
            "num_cases",
            len(cases),
        )

        mlflow.log_param(
            "retrieval_cases",
            len(retrieval_test_cases),
        )

        mlflow.log_param(
            "judge_provider",
            "Groq",
        )

        mlflow.log_param(
            "judge_model",
            judge_model_name,
        )

        mlflow.log_param(
            "timestamp",
            datetime.now().isoformat(),
        )

        mlflow.log_metric(
            "deterministic_pass_rate",
            overall_det_rate,
        )

        for (
            category,
            rate,
        ) in category_rates.items():

            mlflow.log_metric(
                f"{category}_pass_rate",
                rate,
            )

        for (
            metric_name,
            score,
        ) in retrieval_scores.items():

            safe_name = (
                metric_name
                .lower()
                .replace(" ", "_")
            )

            mlflow.log_metric(
                f"retrieval_{safe_name}",
                score,
            )

        mlflow.log_metric(
            "overall_score",
            headline,
        )

        # ====================================================
        # SUMMARY
        # ====================================================

        print()
        print("=" * 70)
        print("EVALUATION SUMMARY")
        print("=" * 70)

        print(
            f"Deterministic score: "
            f"{overall_det_rate:.4f}"
        )

        for (
            category,
            rate,
        ) in category_rates.items():

            print(
                f"  {category}: "
                f"{rate:.4f}"
            )

        for (
            name,
            score,
        ) in retrieval_scores.items():

            print(
                f"  {name}: "
                f"{score:.4f}"
            )

        print(
            f"Overall score: "
            f"{headline:.4f}"
        )

        print()
        print(
            f"MLflow run: {run_name}"
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())