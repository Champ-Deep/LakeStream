from pydantic import BaseModel


class LakeB2BRecord(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    email_address: str | None = None
    company_name: str | None = None
    industry: str | None = None
    revenue_range: str | None = None
    employee_count: str | None = None
    direct_dial: str | None = None
    linkedin_url: str | None = None


LAKE_B2B_INDUSTRIES: list[str] = [
    "Technology",
    "Healthcare",
    "Financial Services",
    "Manufacturing",
    "Retail",
    "Education",
    "Media & Entertainment",
    "Real Estate",
    "Telecommunications",
    "Energy & Utilities",
    "Government",
    "Transportation & Logistics",
    "Hospitality",
    "Agriculture",
    "Construction",
    "Legal Services",
    "Non-Profit",
    "Aerospace & Defense",
    "Automotive",
    "Pharmaceuticals",
    "Insurance",
    "Consulting",
    "Food & Beverage",
    "Mining & Metals",
    "Chemicals",
    "Textiles & Apparel",
    "Packaging",
    "Environmental Services",
    "Professional Services",
    "Staffing & Recruiting",
    "Marketing & Advertising",
    "IT Services",
    "Software",
    "Hardware",
    "Biotechnology",
    "Medical Devices",
    "Banking",
    "Investment Management",
    "Private Equity & Venture Capital",
    "Accounting",
    "Architecture & Planning",
    "Civil Engineering",
    "Mechanical & Industrial Engineering",
    "Printing & Publishing",
    "Sports & Recreation",
    "Consumer Goods",
    "E-commerce",
    "Logistics & Supply Chain",
    "Security & Investigations",
    "Semiconductors",
    "Renewable Energy",
]


JOB_FUNCTION_MAP: dict[str, str] = {
    "ceo": "Executive",
    "coo": "Executive",
    "cto": "Technology",
    "cfo": "Finance",
    "cio": "Technology",
    "cmo": "Marketing",
    "cpo": "Product",
    "cro": "Sales",
    "chro": "Human Resources",
    "vp marketing": "Marketing",
    "vp sales": "Sales",
    "vp engineering": "Technology",
    "vp product": "Product",
    "vp finance": "Finance",
    "vp operations": "Operations",
    "vp hr": "Human Resources",
    "director of marketing": "Marketing",
    "director of sales": "Sales",
    "director of engineering": "Technology",
    "director of demand gen": "Marketing",
    "director of demand generation": "Marketing",
    "director of product": "Product",
    "director of hr": "Human Resources",
    "director of finance": "Finance",
    "director of operations": "Operations",
    "director of it": "Technology",
    "head of marketing": "Marketing",
    "head of sales": "Sales",
    "head of engineering": "Technology",
    "head of product": "Product",
    "head of hr": "Human Resources",
    "head of growth": "Marketing",
    "head of content": "Marketing",
    "head of design": "Design",
    "head of data": "Technology",
    "marketing manager": "Marketing",
    "sales manager": "Sales",
    "engineering manager": "Technology",
    "product manager": "Product",
    "project manager": "Operations",
    "hr manager": "Human Resources",
    "finance manager": "Finance",
    "it manager": "Technology",
    "account executive": "Sales",
    "software engineer": "Technology",
    "data scientist": "Technology",
    "recruiter": "Human Resources",
    "accountant": "Finance",
    "controller": "Finance",
    "general counsel": "Legal",
}


def map_job_title_to_function(title: str) -> str | None:
    """Map a job title to a Lake B2B standard function category."""
    title_lower = title.lower().strip()
    if title_lower in JOB_FUNCTION_MAP:
        return JOB_FUNCTION_MAP[title_lower]
    # Partial match: check if any key is contained in the title
    for key, function in JOB_FUNCTION_MAP.items():
        if key in title_lower:
            return function
    return None
