from pydantic import ValidationError

from src.models.scraped_data import (
    ArticleMetadata,
    BlogUrlMetadata,
    ContactMetadata,
    DataType,
    ResourceMetadata,
    TechStackMetadata,
)

_METADATA_MODELS = {
    DataType.BLOG_URL: BlogUrlMetadata,
    DataType.ARTICLE: ArticleMetadata,
    DataType.CONTACT: ContactMetadata,
    DataType.TECH_STACK: TechStackMetadata,
    DataType.RESOURCE: ResourceMetadata,
}


def validate_metadata(data_type: DataType, metadata: dict) -> tuple[bool, str]:
    """Validate metadata against the expected Pydantic model for the data type."""
    model = _METADATA_MODELS.get(data_type)
    if model is None:
        return True, "No validation model for this data type"

    try:
        model(**metadata)
        return True, "Valid"
    except ValidationError as e:
        return False, str(e)
