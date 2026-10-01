-- Add tech-stack layered-detection toggles to tracked_domains (recurring scrapes)
ALTER TABLE tracked_domains
    ADD COLUMN IF NOT EXISTS tech_stack_wappalyzer BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS tech_stack_llm_fallback BOOLEAN NOT NULL DEFAULT false;
