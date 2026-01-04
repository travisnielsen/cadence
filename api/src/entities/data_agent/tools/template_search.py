"""
Query template search tool using Azure AI Search.

Searches the query_templates index to find SQL templates that match
the user's question intent, returning hydrated QueryTemplate objects.
"""

import json
import logging
import os
from typing import Any

from agent_framework import ai_function

# Support both DevUI and FastAPI import patterns
try:
    from shared.search_client import AzureSearchClient  # type: ignore[import-not-found]
    from models import QueryTemplate, ParameterDefinition  # type: ignore[import-not-found]
except ImportError:
    from src.entities.shared.search_client import AzureSearchClient
    from src.entities.models import QueryTemplate, ParameterDefinition

logger = logging.getLogger(__name__)

# Default confidence threshold for RRF scores (typically 0.01-0.05 range)
DEFAULT_CONFIDENCE_THRESHOLD = float(os.getenv("QUERY_TEMPLATE_CONFIDENCE_THRESHOLD", "0.02"))


def _parse_parameters(params_json: str | list | None) -> list[ParameterDefinition]:
    """
    Parse the stringified JSON parameters field into ParameterDefinition objects.

    Args:
        params_json: Either a JSON string, a list of dicts, or None

    Returns:
        List of ParameterDefinition objects
    """
    if params_json is None:
        return []

    # If it's already a list (parsed by search client), use it directly
    if isinstance(params_json, list):
        params_list = params_json
    else:
        try:
            params_list = json.loads(params_json)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to parse parameters JSON: %s", e)
            return []

    if not isinstance(params_list, list):
        logger.warning("Parameters field is not a list: %s", type(params_list))
        return []

    result = []
    for param_dict in params_list:
        try:
            result.append(ParameterDefinition.model_validate(param_dict))
        except Exception as e:
            logger.warning("Failed to parse parameter definition: %s", e)

    return result


def _hydrate_query_template(raw_result: dict[str, Any]) -> QueryTemplate:
    """
    Convert a raw search result into a hydrated QueryTemplate object.

    Args:
        raw_result: Dictionary from Azure AI Search

    Returns:
        QueryTemplate with parsed parameters
    """
    # Parse the parameters field (stored as stringified JSON in the index)
    parameters = _parse_parameters(raw_result.get("parameters"))

    return QueryTemplate(
        id=raw_result.get("id", ""),
        intent=raw_result.get("intent", ""),
        question=raw_result.get("question", ""),
        confidence_threshold=raw_result.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD),
        sql_template=raw_result.get("sql_template", ""),
        reasoning=raw_result.get("reasoning", ""),
        parameters=parameters,
        allowed_tables=raw_result.get("allowed_tables", []),
        allowed_columns=raw_result.get("allowed_columns", []),
        score=raw_result.get("score", 0.0),
    )


@ai_function
async def search_query_templates(user_question: str) -> dict[str, Any]:
    """
    Search for query templates that match the user's question intent.

    This function searches the query_templates index using hybrid search
    to find SQL templates with parameterized tokens that can be filled in
    based on the user's specific question.

    Unlike cached queries (which are exact SQL), templates contain tokens
    like %{{parameter_name}}% that need to be substituted with actual values.

    Args:
        user_question: The user's natural language question about the data

    Returns:
        A dictionary containing:
        - has_high_confidence_match: Whether a template above threshold was found
        - best_match: The best matching QueryTemplate object (if confidence is high)
        - confidence_score: The search relevance score of the best match
        - confidence_threshold: The threshold used for this template
        - all_matches: All matching templates with their scores
        - message: Status message
    """
    logger.info("Searching query templates for: %s", user_question[:100])

    # Emit step start event for UI progress
    step_name = "Understanding intent"
    emit_step_end_fn = None
    try:
        from src.api.step_events import emit_step_start, emit_step_end
        emit_step_start(step_name)
        emit_step_end_fn = emit_step_end
    except ImportError:
        pass  # Step events not available

    def finish_step():
        if emit_step_end_fn:
            emit_step_end_fn(step_name)

    try:
        async with AzureSearchClient(
            index_name="query_templates",
            vector_field="content_vector"
        ) as client:
            results = await client.hybrid_search(
                query=user_question,
                select=[
                    "id",
                    "intent",
                    "question",
                    "confidence_threshold",
                    "sql_template",
                    "reasoning",
                    "parameters",
                    "allowed_tables",
                    "allowed_columns",
                ],
                top=3,
            )

        if not results:
            finish_step()
            return {
                "has_high_confidence_match": False,
                "best_match": None,
                "confidence_score": 0.0,
                "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
                "all_matches": [],
                "message": "No query templates found"
            }

        # Hydrate all results into QueryTemplate objects
        hydrated_templates = [_hydrate_query_template(r) for r in results]
        best_template = hydrated_templates[0]

        # Use the template's own confidence threshold if available
        threshold = best_template.confidence_threshold or DEFAULT_CONFIDENCE_THRESHOLD
        has_high_confidence = best_template.score >= threshold

        logger.info(
            "Found %d query templates. Best: '%s' score=%.3f (threshold: %.2f, match: %s)",
            len(results),
            best_template.intent,
            best_template.score,
            threshold,
            has_high_confidence
        )

        finish_step()
        return {
            "has_high_confidence_match": has_high_confidence,
            "best_match": best_template.model_dump() if has_high_confidence else None,
            "confidence_score": best_template.score,
            "confidence_threshold": threshold,
            "all_matches": [t.model_dump() for t in hydrated_templates],
            "message": "High confidence template found" if has_high_confidence else "No high confidence match"
        }

    except Exception as e:
        logger.error("Error searching query templates: %s", e)
        finish_step()
        return {
            "has_high_confidence_match": False,
            "best_match": None,
            "confidence_score": 0.0,
            "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
            "all_matches": [],
            "error": str(e),
            "message": f"Error: {e}"
        }
