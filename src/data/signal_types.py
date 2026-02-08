"""Pre-built signal type library for Lake B2B Intent Data Platform.

This module defines the available intent signal types that users can configure
to monitor companies, people, technology, and behavioral changes in real-time.

Signal types are seeded into the database via migration 014_create_signals.sql.
This module provides programmatic access to the definitions for documentation
and validation purposes.
"""

from typing import Any

# Signal type definitions (matches database seed data)
SIGNAL_TYPES: list[dict[str, Any]] = [
    {
        "id": "job_change",
        "name": "Job Change Detected",
        "category": "people",
        "description": "Fires when someone changes jobs or gets promoted",
        "config_schema": {
            "type": "object",
            "properties": {
                "job_title_contains": {
                    "type": "string",
                    "description": "Filter by job title keyword",
                },
                "seniority_level": {
                    "type": "string",
                    "enum": ["VP", "Director", "Manager", "IC"],
                    "description": "Seniority level",
                },
                "company_domain": {
                    "type": "string",
                    "description": "Specific company domain",
                },
                "change_type": {
                    "type": "string",
                    "enum": ["new_hire", "promotion", "departure"],
                    "description": "Type of change",
                },
            },
        },
        "example": "Alert when a VP of Sales joins a SaaS company",
    },
    {
        "id": "funding_round",
        "name": "Funding Round Announced",
        "category": "company",
        "description": "Fires when a company raises funding",
        "config_schema": {
            "type": "object",
            "properties": {
                "round_type": {
                    "type": "string",
                    "enum": ["Seed", "Series A", "Series B", "Series C+"],
                    "description": "Funding round stage",
                },
                "min_amount_usd": {
                    "type": "number",
                    "description": "Minimum funding amount in USD",
                },
                "investor_contains": {
                    "type": "string",
                    "description": "Filter by investor name",
                },
            },
        },
        "example": "Alert when a company raises $10M+ Series A",
    },
    {
        "id": "tech_stack_change",
        "name": "Tech Stack Change",
        "category": "technology",
        "description": "Fires when a company adopts or removes a technology",
        "config_schema": {
            "type": "object",
            "properties": {
                "technology": {
                    "type": "string",
                    "description": "Technology name (e.g., Salesforce, HubSpot)",
                },
                "change_type": {
                    "type": "string",
                    "enum": ["adopted", "removed"],
                    "description": "Type of change",
                },
                "category": {
                    "type": "string",
                    "enum": ["CRM", "Analytics", "Marketing", "Sales", "Platform"],
                    "description": "Technology category",
                },
            },
        },
        "example": "Alert when a company switches from HubSpot to Salesforce",
    },
    {
        "id": "pricing_change",
        "name": "Pricing Page Change",
        "category": "behavior",
        "description": "Fires when pricing information changes on a website",
        "config_schema": {
            "type": "object",
            "properties": {
                "price_increase_threshold": {
                    "type": "number",
                    "description": "Minimum price increase percentage",
                },
                "new_plan_added": {
                    "type": "boolean",
                    "description": "Alert on new pricing plan",
                },
                "plan_removed": {
                    "type": "boolean",
                    "description": "Alert on removed plan",
                },
            },
        },
        "example": "Alert when competitor raises pricing by 20%+",
    },
    {
        "id": "hiring_spike",
        "name": "Hiring Spike",
        "category": "company",
        "description": "Fires when job posting volume increases significantly",
        "config_schema": {
            "type": "object",
            "properties": {
                "department": {
                    "type": "string",
                    "enum": ["Engineering", "Sales", "Marketing", "All"],
                    "description": "Department filter",
                },
                "spike_threshold": {
                    "type": "number",
                    "description": "Multiplier (e.g., 5 = 5x normal volume)",
                },
                "timeframe_days": {
                    "type": "number",
                    "description": "Time window in days",
                },
            },
        },
        "example": "Alert when company posts 5x more sales jobs than usual",
    },
]


def get_signal_type(signal_type_id: str) -> dict[str, Any] | None:
    """Get signal type definition by ID."""
    return next((st for st in SIGNAL_TYPES if st["id"] == signal_type_id), None)


def get_signal_types_by_category(category: str) -> list[dict[str, Any]]:
    """Get all signal types in a category."""
    return [st for st in SIGNAL_TYPES if st["category"] == category]
