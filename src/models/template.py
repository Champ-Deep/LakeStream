from pydantic import BaseModel


class SelectorSet(BaseModel):
    blog_landing: list[str] = []
    article_list: list[str] = []
    article_link: list[str] = []
    article_title: list[str] = []
    article_date: list[str] = []
    article_author: list[str] = []
    article_content: list[str] = []
    team_members: list[str] = []
    contact_info: list[str] = []
    navigation: list[str] = []


class PaginationStrategy(BaseModel):
    type: str = "none"  # numbered, load_more, infinite_scroll, next_link, none
    next_selector: str | None = None
    page_param_name: str | None = None
    max_pages: int = 50


class TemplateConfig(BaseModel):
    id: str
    name: str
    description: str = ""
    platform_signals: list[str] = []
    selectors: SelectorSet = SelectorSet()
    pagination: PaginationStrategy = PaginationStrategy()
    blog_path_patterns: list[str] = []
    article_path_patterns: list[str] = []
    team_path_patterns: list[str] = []
    resource_path_patterns: list[str] = []
    rate_limit_ms: int = 1000
    max_concurrent_pages: int = 3
