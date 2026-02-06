from fastapi import APIRouter, HTTPException

from src.models.template import TemplateConfig
from src.templates.registry import get_template, list_templates

router = APIRouter(prefix="/templates")


@router.get("", response_model=list[TemplateConfig])
async def get_templates() -> list[TemplateConfig]:
    return list_templates()


@router.get("/{template_id}", response_model=TemplateConfig)
async def get_template_by_id(template_id: str) -> TemplateConfig:
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template
