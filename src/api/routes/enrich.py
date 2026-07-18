"""Company/brand enrichment API (v2.1).

POST /api/enrich {domain | email | company_name, force_refresh?}
→ firmographic CompanyProfile (industry, NAICS/SIC, logo, description, socials),
cached per (user, domain) in company_profiles.
"""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator

from src.api.middleware.auth import get_current_user
from src.config.settings import get_settings
from src.db.pool import get_pool
from src.models.company import CompanyProfile
from src.services.enrichment import EnrichmentError, enrich_company

router = APIRouter(prefix="/enrich")
logger = structlog.get_logger()


class EnrichRequest(BaseModel):
    domain: str | None = None
    email: str | None = None
    company_name: str | None = None
    force_refresh: bool = False

    @model_validator(mode="after")
    def one_identifier(self) -> "EnrichRequest":
        if not (self.domain or self.email or self.company_name):
            raise ValueError("Provide one of: domain, email, company_name")
        return self


class EnrichResponse(BaseModel):
    success: bool = True
    profile: CompanyProfile


@router.post("", response_model=EnrichResponse)
async def enrich(
    input: EnrichRequest, user: dict = Depends(get_current_user)
) -> EnrichResponse:
    settings = get_settings()
    if not settings.enable_enrichment:
        raise HTTPException(status_code=404, detail="Enrichment is disabled")

    pool = await get_pool()
    try:
        profile = await enrich_company(
            pool,
            domain=input.domain,
            email=input.email,
            name=input.company_name,
            user_id=UUID(user["user_id"]) if user.get("user_id") else None,
            org_id=UUID(user["org_id"]) if user.get("org_id") else None,
            force_refresh=input.force_refresh,
        )
    except EnrichmentError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("enrich_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Enrichment failed: {e}")
    return EnrichResponse(profile=profile)
