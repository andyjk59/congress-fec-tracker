"""Hand-curated crosswalk: Congress.gov policy area -> keyword list.

Used to keyword-match FEC contributor employer/occupation text and PAC
committee names against a bill's policy area, producing a directional
"industry signal" — NOT an authoritative industry classification like the
paid CRP/OpenSecrets codes. Review and extend this list like you would
sources.py in ClarifyAI: it's a small, reviewable table, not a black box.

Keys match Congress.gov's standard policy area taxonomy exactly.
"""

POLICY_AREA_KEYWORDS = {
    "Agriculture and Food": [
        "FARM", "AGRICULTUR", "GRAIN", "LIVESTOCK", "DAIRY", "CROP",
        "RANCH", "FOOD PRODUCT", "AGRIBUSINESS",
    ],
    "Animals": ["VETERINAR", "ANIMAL WELFARE", "LIVESTOCK", "PET "],
    "Armed Forces and National Security": [
        "DEFENSE", "AEROSPACE", "MILITARY", "ARMS", "WEAPONS", "DEFENSE CONTRACTOR",
    ],
    "Commerce": ["RETAIL", "CHAMBER OF COMMERCE", "MANUFACTURING", "WHOLESALE"],
    "Crime and Law Enforcement": ["POLICE", "LAW ENFORCEMENT", "SHERIFF", "CORRECTIONS"],
    "Economics and Public Finance": ["ECONOMI", "BUDGET", "FISCAL"],
    "Education": ["EDUCATION", "UNIVERSITY", "SCHOOL", "TEACHER", "COLLEGE"],
    "Energy": [
        "OIL", "GAS", "PETROLEUM", "ENERGY", "COAL", "UTILITY", "UTILITIES",
        "SOLAR", "WIND POWER", "NUCLEAR", "PIPELINE",
    ],
    "Environmental Protection": ["ENVIRONMENT", "CONSERVATION", "SIERRA CLUB", "CLIMATE"],
    "Finance and Financial Sector": [
        "BANK", "FINANCIAL", "INVESTMENT", "SECURITIES", "INSURANCE",
        "CREDIT UNION", "MORTGAGE", "HEDGE FUND", "PRIVATE EQUITY",
    ],
    "Foreign Trade and International Finance": ["TRADE ASSOC", "EXPORT", "IMPORT"],
    "Health": [
        "HOSPITAL", "PHYSICIAN", "PHARMA", "MEDICAL", "HEALTH CARE", "HEALTHCARE",
        "NURSE", "DENTIST", "BIOTECH", "DRUG",
    ],
    "Housing and Community Development": [
        "REALTOR", "REAL ESTATE", "HOME BUILDER", "HOUSING", "MORTGAGE",
    ],
    "Immigration": ["IMMIGRATION"],
    "Labor and Employment": ["UNION", "LABOR", "AFL-CIO", "TEAMSTERS", "EMPLOYEES"],
    "Law": ["LAW FIRM", "ATTORNEY", "LAWYER", "TRIAL LAWYERS"],
    "Public Lands and Natural Resources": ["MINING", "TIMBER", "FORESTRY", "MINERAL"],
    "Science, Technology, Communications": [
        "TECHNOLOGY", "SOFTWARE", "INTERNET", "TELECOM", "BROADCAST",
        "SEMICONDUCTOR", "COMPUTER",
    ],
    "Social Welfare": ["SOCIAL SERVICE", "NONPROFIT", "CHARIT"],
    "Taxation": ["ACCOUNTANT", "TAX", "CPA"],
    "Transportation and Public Works": [
        "AIRLINE", "TRUCKING", "RAILROAD", "SHIPPING", "AUTOMOTIVE", "TRANSIT",
        "AUTO DEALERS", "MARITIME",
    ],
    "Water Resources Development": ["WATER UTILITY", "IRRIGATION"],
}


def keywords_for_policy_area(policy_area: str) -> list[str]:
    return POLICY_AREA_KEYWORDS.get(policy_area, [])
