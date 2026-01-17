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

# Confidence threshold for vector search (cosine similarity) scores
# Cosine similarity ranges from 0.0 to 1.0, where 1.0 is identical
# Good semantic matches typically score 0.80+, weak matches below 0.70
DEFAULT_CONFIDENCE_THRESHOLD = float(os.getenv("QUERY_TEMPLATE_CONFIDENCE_THRESHOLD", "0.80"))

# Minimum normalized score margin between top results to consider match unambiguous
# Scores are normalized: normalized = score / top_score
# If (1 - second_normalized) < this threshold, results are too similar (ambiguous)
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
    (cosine similarity) to find SQL templates with parameterized tokens that
    can be filled in based on the user's specific question.

    Vector search provides cosine similarity scores (0.0 to 1.0) which are
    more interpretable than hybrid RRF scores for confidence thresholding.

    Ambiguity detection uses normalized scores:
    - normalized = score / top_score
    - If top results have similar normalized scores (margin < threshold): ambiguous
    - If single dominant match (margin >= threshold): proceed to extraction

    Unlike cached queries (which are exact SQL), templates contain tokens
    like %{{parameter_name}}% that need to be substituted with actual values.

    Args:
        user_question: The user's natural language question about the data

    Returns:
        A dictionary containing:
        - has_high_confidence_match: Whether a template above threshold was found AND is unambiguous
        - is_ambiguous: Whether multiple templates have similar high scores
        - best_match: The best matching QueryTemplate object (if confidence is high and unambiguous)
        - confidence_score: The cosine similarity score of the best match (0.0 to 1.0)
        - confidence_threshold: The threshold used for this template
        - ambiguity_gap: The normalized score margin between 1st and 2nd results
        - ambiguity_gap_threshold: The minimum margin required for unambiguous match
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
            # Use vector search for cosine similarity scores (0-1 range)
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

        # Use global confidence threshold
        threshold = DEFAULT_CONFIDENCE_THRESHOLD
        top_score = best_template.score
        has_high_confidence = top_score >= threshold

        # Calculate ambiguity using normalized scores
        # normalized = score / top_score (so top result always has normalized = 1.0)
        # margin = 1.0 - second_normalized (how much lower the second result is)
        normalized_margin = 1.0  # Default: no second result means fully unambiguous
        is_ambiguous = False
        
        if len(hydrated_templates) >= 2 and top_score > 0:
            second_score = hydrated_templates[1].score
            second_normalized = second_score / top_score
            normalized_margin = 1.0 - second_normalized
            
            # Match is ambiguous if:
            # 1. Top result meets confidence threshold
            # 2. The normalized margin is smaller than the ambiguity gap threshold
            #    (meaning second result is too close to the first)
            if has_high_confidence:
                is_ambiguous = normalized_margin < DEFAULT_AMBIGUITY_GAP_THRESHOLD
        
        # A match is only considered valid if it's both high confidence AND unambiguous
        is_valid_match = has_high_confidence and not is_ambiguous

        # Build descriptive message
        if not has_high_confidence:
            message = f"No high confidence match (score {best_template.score:.3f} < threshold {threshold:.3f})"
        elif is_ambiguous:
            second_template = hydrated_templates[1]
            second_normalized = second_template.score / top_score if top_score > 0 else 0
            message = (
                f"Ambiguous match: '{best_template.intent}' (score={best_template.score:.3f}) "
                f"and '{second_template.intent}' (score={second_template.score:.3f}, "
                f"normalized={second_normalized:.3f}) are too similar "
                f"(margin {normalized_margin:.3f} < {DEFAULT_AMBIGUITY_GAP_THRESHOLD:.3f})"
            )
        else:
            message = f"High confidence unambiguous match: '{best_template.intent}'"

        logger.info(
            "Template search: %d results. Best: '%s' score=%.3f (threshold: %.3f, margin: %.3f, ambiguous: %s, valid: %s)",
            len(results),
            best_template.intent,
            best_template.score,
            threshold,
            normalized_margin,
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
            "ambiguity_gap": normalized_margin,
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
