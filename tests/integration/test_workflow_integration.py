"""Integration tests for the NL2SQL confidence and clarification pipeline.

These tests exercise end-to-end code paths between components, mocking only
external services (Azure AI Search, Azure SQL). They verify that the five
phases (deterministic extraction, confidence scoring, confirmation notes,
hypothesis prompts, and schema area detection) work together correctly.
"""

from unittest.mock import AsyncMock

from entities.assistant.assistant import (
    SCHEMA_SUGGESTIONS,
    _detect_schema_area,
)
from entities.nl2sql_controller.pipeline import (
    _CONFIDENCE_THRESHOLD_HIGH,
    _CONFIDENCE_THRESHOLD_LOW,
    _format_confirmation_note,
    _format_hypothesis_prompt,
)

# ---------------------------------------------------------------------------
# Direct imports from the new pipeline/extractor modules
# ---------------------------------------------------------------------------
from entities.parameter_extractor.extractor import (
    _build_parameter_confidences,
    _fuzzy_match_allowed_value,
    _hydrate_database_allowed_values,
    _pre_extract_parameters,
)
from entities.shared.allowed_values_provider import AllowedValuesProvider, AllowedValuesResult
from models import (
    MissingParameter,
    ParameterDefinition,
    ParameterValidation,
    QueryTemplate,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_PATCH_TARGET = "entities.shared.allowed_values_provider.AzureSqlClient"


def _make_template(
    *,
    params: list[ParameterDefinition] | None = None,
) -> QueryTemplate:
    """Build a minimal QueryTemplate for integration tests."""
    default_params = [
        ParameterDefinition(
            name="category",
            required=True,
            confidence_weight=1.0,
            validation=ParameterValidation(
                type="string",
                allowed_values=["Supermarket", "Corporate", "Novelty Shop"],
            ),
        ),
        ParameterDefinition(
            name="limit",
            required=False,
            confidence_weight=1.0,
            default_value=10,
            validation=ParameterValidation(type="integer", min=1, max=100),
        ),
    ]
    return QueryTemplate(
        id="test_template",
        intent="test_intent",
        question="Show customers by category",
        sql_template=(
            "SELECT TOP %{{limit}}% * FROM Sales.Customers "
            "WHERE CustomerCategoryID = %{{category}}%"
        ),
        parameters=params if params is not None else default_params,
    )


# ═══════════════════════════════════════════════════════════════════════════
# a. High-confidence template match → execution
# ═══════════════════════════════════════════════════════════════════════════


class TestHighConfidenceExecution:
    """Verify that deterministic exact-match extraction yields high confidence."""

    def test_all_params_extracted_high_confidence(self) -> None:
        """When all params are exact-matched, confidences are >= 0.85."""
        template = _make_template()

        result = _pre_extract_parameters("top 5 supermarket customers", template)

        assert "category" in result.extracted
        assert result.extracted["category"] == "Supermarket"
        assert "limit" in result.extracted
        assert result.extracted["limit"] == 5

        confidences = _build_parameter_confidences(result.resolution_methods, template)

        assert all(c >= _CONFIDENCE_THRESHOLD_HIGH for c in confidences.values())

    def test_high_confidence_produces_empty_confirmation_note(self) -> None:
        """All-high-confidence params produce no confirmation note."""
        template = _make_template()
        result = _pre_extract_parameters("top 5 supermarket customers", template)
        confidences = _build_parameter_confidences(result.resolution_methods, template)

        note = _format_confirmation_note(confidences, result.extracted)

        assert not note


# ═══════════════════════════════════════════════════════════════════════════
# b. Medium-confidence → confirmation note
# ═══════════════════════════════════════════════════════════════════════════


class TestMediumConfidenceNote:
    """Verify that llm_validated resolution produces a confirmation note."""

    def test_llm_validated_produces_confirmation(self) -> None:
        """A param resolved via 'llm_validated' (0.75) triggers a note."""
        template = _make_template()
        resolution_methods = {"category": "llm_validated", "limit": "exact_match"}
        confidences = _build_parameter_confidences(resolution_methods, template)

        # Verify the category confidence is medium
        assert _CONFIDENCE_THRESHOLD_LOW <= confidences["category"] < _CONFIDENCE_THRESHOLD_HIGH

        note = _format_confirmation_note(
            confidences,
            {"category": "Supermarket", "limit": 5},
        )

        assert note
        assert "category=**Supermarket**" in note
        assert "I assumed" in note


# ═══════════════════════════════════════════════════════════════════════════
# c. Low-confidence → clarification triggered
# ═══════════════════════════════════════════════════════════════════════════


class TestLowConfidenceClarification:
    """Verify that low-confidence resolution triggers hypothesis prompt."""

    def test_llm_failed_triggers_clarification(self) -> None:
        """A param with 'llm_failed_validation' scores below the low threshold."""
        template = _make_template()
        resolution_methods = {"category": "llm_failed_validation", "limit": "exact_match"}
        confidences = _build_parameter_confidences(resolution_methods, template)

        assert confidences["category"] < _CONFIDENCE_THRESHOLD_LOW

    def test_hypothesis_prompt_generated(self) -> None:
        """Low-confidence params produce a hypothesis-first clarification."""
        missing = [
            MissingParameter(
                name="category",
                best_guess="Corporate",
                alternatives=["Supermarket", "Novelty Shop"],
            ),
        ]

        prompt = _format_hypothesis_prompt(missing)

        assert "It looks like you want **Corporate**" in prompt
        assert "Supermarket" in prompt
        assert "Novelty Shop" in prompt


# ═══════════════════════════════════════════════════════════════════════════
# d. Database-sourced param hydration → fuzzy match
# ═══════════════════════════════════════════════════════════════════════════


class TestDatabaseHydrationFuzzyMatch:
    """Verify hydration populates allowed_values and fuzzy-match works."""

    async def test_hydrate_then_fuzzy_match(self) -> None:
        """After hydration, fuzzy-match succeeds against DB-sourced values."""
        provider = AsyncMock(spec=AllowedValuesProvider)
        provider.get_allowed_values.return_value = AllowedValuesResult(
            values=["Corporate", "Gift Store", "Supermarket"],
            is_partial=False,
        )

        # Build a template with database-sourced param (no static allowed_values)
        template = QueryTemplate(
            id="t_hydrate",
            intent="test",
            question="test",
            sql_template="SELECT 1",
            parameters=[
                ParameterDefinition(
                    name="category",
                    required=True,
                    allowed_values_source="database",
                    table="Sales.CustomerCategories",
                    column="CustomerCategoryName",
                ),
            ],
        )

        # Call the standalone hydration function directly
        await _hydrate_database_allowed_values(template, provider)

        # Verify hydration worked
        param = template.parameters[0]
        assert param.validation is not None
        assert "Supermarket" in param.validation.allowed_values

        # Fuzzy-match against hydrated values
        match = _fuzzy_match_allowed_value("supermarkets", param.validation.allowed_values)
        assert match == "Supermarket"


# ═══════════════════════════════════════════════════════════════════════════
# e. Schema area detection → suggestions
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemaAreaDetection:
    """Verify schema area detection from SQL table references."""

    def test_sales_orders(self) -> None:
        assert _detect_schema_area(["Sales.Orders"]) == "sales"

    def test_warehouse_stock(self) -> None:
        assert _detect_schema_area(["Warehouse.StockItems"]) == "warehouse"

    def test_purchasing(self) -> None:
        assert _detect_schema_area(["Purchasing.PurchaseOrders"]) == "purchasing"

    def test_application(self) -> None:
        assert _detect_schema_area(["Application.People"]) == "application"

    def test_detected_area_has_suggestions(self) -> None:
        """Each detected area maps to schema suggestions."""
        for area in ("sales", "warehouse", "purchasing", "application"):
            assert area in SCHEMA_SUGGESTIONS
            assert len(SCHEMA_SUGGESTIONS[area]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# f. Full pipeline: extraction → confidence → confirmation note
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipeline:
    """End-to-end: deterministic extraction → confidence → confirmation note."""

    def test_template_with_default_policy_date(self) -> None:
        """A date param using default_policy produces medium confidence + note."""
        template = QueryTemplate(
            id="t_pipeline",
            intent="recent_orders",
            question="Show recent orders",
            sql_template=(
                "SELECT * FROM Sales.Orders "
                "WHERE OrderDate >= %{{from_date}}% "
                "ORDER BY OrderDate %{{order}}%"
            ),
            parameters=[
                ParameterDefinition(
                    name="from_date",
                    required=True,
                    confidence_weight=1.0,
                    default_policy="DATEADD(day, -30, GETDATE())",
                ),
                ParameterDefinition(
                    name="order",
                    required=False,
                    confidence_weight=1.0,
                    default_value="DESC",
                    validation=ParameterValidation(
                        type="string",
                        allowed_values=["ASC", "DESC"],
                    ),
                ),
            ],
        )

        # Step 1: Deterministic extraction
        result = _pre_extract_parameters("show me recent orders", template)

        assert "from_date" in result.extracted
        assert result.resolution_methods["from_date"] == "default_policy"
        assert "order" in result.extracted
        assert result.resolution_methods["order"] == "default_value"

        # Step 2: Confidence scoring
        confidences = _build_parameter_confidences(result.resolution_methods, template)

        # default_policy → 0.7, default_value → 0.7 (both medium)
        assert _CONFIDENCE_THRESHOLD_LOW <= confidences["from_date"] < _CONFIDENCE_THRESHOLD_HIGH
        assert _CONFIDENCE_THRESHOLD_LOW <= confidences["order"] < _CONFIDENCE_THRESHOLD_HIGH

        # Step 3: Confirmation note
        note = _format_confirmation_note(confidences, result.extracted)

        assert note
        assert "I assumed" in note
        assert "from_date" in note
        assert "order" in note

    def test_mixed_confidence_pipeline(self) -> None:
        """Exact + default produces one high + one medium → note for medium only."""
        template = _make_template()

        # "supermarket" → exact_match for category
        # "limit" has no match in query → falls back to default_value=10
        result = _pre_extract_parameters("show supermarket customers", template)

        assert result.resolution_methods["category"] == "exact_match"
        assert result.resolution_methods["limit"] == "default_value"

        confidences = _build_parameter_confidences(result.resolution_methods, template)

        assert confidences["category"] >= _CONFIDENCE_THRESHOLD_HIGH
        assert confidences["limit"] < _CONFIDENCE_THRESHOLD_HIGH

        note = _format_confirmation_note(confidences, result.extracted)

        assert note
        assert "limit" in note
        # category is high confidence, so it should NOT appear in the note
        assert "category" not in note
