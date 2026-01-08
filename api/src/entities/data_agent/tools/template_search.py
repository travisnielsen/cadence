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

# Default confidence threshold for vector similarity scores (0.0 to 1.0 range)
# Vector search uses cosine similarity, so scores are more absolute than RRF
DEFAULT_CONFIDENCE_THRESHOLD = float(os.getenv("QUERY_TEMPLATE_CONFIDENCE_THRESHOLD", "0.75"))

# Minimum score gap between 1st and 2nd result to consider match unambiguous
# If gap is smaller than this, the match is considered ambiguous
DEFAULT_AMBIGUITY_GAP_THRESHOLD = float(os.getenv("QUERY_TEMPLATE_AMBIGUITY_GAP", "0.05"))


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

    This function searches the query_templates index using pure vector search
    to find SQL templates with parameterized tokens that can be filled in
    based on the user's specific question.

    Vector search provides cosine similarity scores (0.0 to 1.0) which are
    more discriminative than hybrid RRF scores, enabling better detection
    of ambiguous matches where multiple templates have similar relevance.

    Unlike cached queries (which are exact SQL), templates contain tokens
    like %{{parameter_name}}% that need to be substituted with actual values.

    Args:
        user_question: The user's natural language question about the data

    Returns:
        A dictionary containing:
        - has_high_confidence_match: Whether a template above threshold was found AND is unambiguous
        - is_ambiguous: Whether multiple templates have similar high scores
        - best_match: The best matching QueryTemplate object (if confidence is high and unambiguous)
        - confidence_score: The search relevance score of the best match
        - confidence_threshold: The threshold used for this template
        - ambiguity_gap: The score difference between 1st and 2nd results
        - ambiguity_gap_threshold: The minimum gap required for unambiguous match
        - all_matches: All matching templates with their scores
        - message: Status message explaining the result
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
            # Use pure vector search for more discriminative scoring
            # Cosine similarity scores provide absolute ranking (0-1 range)
            # compared to RRF which compresses differences between results
            results = await client.vector_search(
                query=user_question,
                select=[
                    "id",
                    "intent",
                    "question",
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
                "is_ambiguous": False,
                "best_match": None,
                "confidence_score": 0.0,
                "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
                "ambiguity_gap": 0.0,
                "ambiguity_gap_threshold": DEFAULT_AMBIGUITY_GAP_THRESHOLD,
                "all_matches": [],
                "message": "No query templates found"
            }

        # Hydrate all results into QueryTemplate objects
        hydrated_templates = [_hydrate_query_template(r) for r in results]
        best_template = hydrated_templates[0]

        # Use global confidence threshold for all templates
        threshold = DEFAULT_CONFIDENCE_THRESHOLD
        has_high_confidence = best_template.score >= threshold

        # Calculate ambiguity: check if there are multiple high-scoring matches
        # that are too close together to confidently pick one
        ambiguity_gap = 0.0
        is_ambiguous = False
        
        if len(hydrated_templates) >= 2:
            second_best = hydrated_templates[1]
            ambiguity_gap = best_template.score - second_best.score
            
            # Match is ambiguous if:
            # 1. Both top results are above the confidence threshold
            # 2. The gap between them is smaller than the ambiguity threshold
            if has_high_confidence and second_best.score >= threshold:
                is_ambiguous = ambiguity_gap < DEFAULT_AMBIGUITY_GAP_THRESHOLD
        
        # A match is only considered valid if it's both high confidence AND unambiguous
        is_valid_match = has_high_confidence and not is_ambiguous

        # Build descriptive message
        if not has_high_confidence:
            message = f"No high confidence match (score {best_template.score:.3f} < threshold {threshold:.3f})"
        elif is_ambiguous:
            message = (
                f"Ambiguous match: top results '{best_template.intent}' ({best_template.score:.3f}) "
                f"and '{hydrated_templates[1].intent}' ({hydrated_templates[1].score:.3f}) "
                f"are too similar (gap {ambiguity_gap:.3f} < {DEFAULT_AMBIGUITY_GAP_THRESHOLD:.3f})"
            )
        else:
            message = f"High confidence unambiguous match: '{best_template.intent}'"

        logger.info(
            "Template search: %d results. Best: '%s' score=%.3f (threshold: %.3f, gap: %.3f, ambiguous: %s, valid: %s)",
            len(results),
            best_template.intent,
            best_template.score,
            threshold,
            ambiguity_gap,
            is_ambiguous,
            is_valid_match
        )

        finish_step()
        return {
            "has_high_confidence_match": is_valid_match,
            "is_ambiguous": is_ambiguous,
            "best_match": best_template.model_dump() if is_valid_match else None,
            "confidence_score": best_template.score,
            "confidence_threshold": threshold,
            "ambiguity_gap": ambiguity_gap,
            "ambiguity_gap_threshold": DEFAULT_AMBIGUITY_GAP_THRESHOLD,
            "all_matches": [t.model_dump() for t in hydrated_templates],
            "message": message
        }

    except Exception as e:
        logger.error("Error searching query templates: %s", e)
        finish_step()
        return {
            "has_high_confidence_match": False,
            "is_ambiguous": False,
            "best_match": None,
            "confidence_score": 0.0,
            "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
            "ambiguity_gap": 0.0,
            "ambiguity_gap_threshold": DEFAULT_AMBIGUITY_GAP_THRESHOLD,
            "all_matches": [],
            "error": str(e),
            "message": f"Error: {e}"
        }
