"""Deterministic industry → NAICS/SIC crosswalk (v2.1).

Maps the LAKE_B2B_INDUSTRIES taxonomy to representative NAICS 2022 and SIC
codes at sector/industry-group level. Codes are looked up here — never
guessed by the LLM — so the same industry always yields the same codes.
"""

# industry → (naics_code, sic_code)
INDUSTRY_CODES: dict[str, tuple[str, str]] = {
    "Technology": ("5415", "7370"),
    "Healthcare": ("62", "8000"),
    "Financial Services": ("52", "6199"),
    "Manufacturing": ("31-33", "3999"),
    "Retail": ("44-45", "5999"),
    "Education": ("61", "8200"),
    "Media & Entertainment": ("51", "7812"),
    "Real Estate": ("531", "6500"),
    "Telecommunications": ("517", "4813"),
    "Energy & Utilities": ("22", "4911"),
    "Government": ("92", "9199"),
    "Transportation & Logistics": ("48-49", "4700"),
    "Hospitality": ("72", "7011"),
    "Agriculture": ("11", "0100"),
    "Construction": ("23", "1500"),
    "Legal Services": ("5411", "8111"),
    "Non-Profit": ("813", "8399"),
    "Aerospace & Defense": ("3364", "3721"),
    "Automotive": ("3361", "3711"),
    "Pharmaceuticals": ("3254", "2834"),
    "Insurance": ("524", "6311"),
    "Consulting": ("5416", "8742"),
    "Food & Beverage": ("311", "2000"),
    "Mining & Metals": ("21", "1000"),
    "Chemicals": ("325", "2800"),
    "Textiles & Apparel": ("313", "2200"),
    "Packaging": ("3222", "2650"),
    "Environmental Services": ("562", "4959"),
    "Professional Services": ("54", "8999"),
    "Staffing & Recruiting": ("5613", "7361"),
    "Marketing & Advertising": ("5418", "7311"),
    "IT Services": ("5415", "7379"),
    "Software": ("5112", "7372"),
    "Hardware": ("3341", "3571"),
    "Biotechnology": ("5417", "8731"),
    "Medical Devices": ("3391", "3841"),
    "Banking": ("5221", "6020"),
    "Investment Management": ("5239", "6282"),
    "Private Equity & Venture Capital": ("5239", "6726"),
    "Accounting": ("5412", "8721"),
    "Architecture & Planning": ("5413", "8712"),
    "Civil Engineering": ("5413", "8711"),
    "Mechanical & Industrial Engineering": ("5413", "8711"),
    "Printing & Publishing": ("323", "2750"),
    "Sports & Recreation": ("71", "7999"),
    "Consumer Goods": ("339", "5099"),
    "E-commerce": ("4541", "5961"),
    "Logistics & Supply Chain": ("4931", "4225"),
    "Security & Investigations": ("5616", "7381"),
    "Semiconductors": ("3344", "3674"),
    "Renewable Energy": ("221114", "4911"),
}


def industry_to_codes(industry: str | None) -> tuple[str | None, str | None]:
    """Return (naics_code, sic_code) for a taxonomy industry, or (None, None)."""
    if not industry:
        return None, None
    codes = INDUSTRY_CODES.get(industry.strip())
    if codes:
        return codes
    # Tolerate case drift from the LLM
    for name, pair in INDUSTRY_CODES.items():
        if name.lower() == industry.strip().lower():
            return pair
    return None, None
