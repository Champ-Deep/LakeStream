"""Lake B2B job title to function mapping."""

from src.models.lake_b2b import JOB_FUNCTION_MAP, map_job_title_to_function

# Re-export for convenience
FUNCTIONS = JOB_FUNCTION_MAP
map_title = map_job_title_to_function
