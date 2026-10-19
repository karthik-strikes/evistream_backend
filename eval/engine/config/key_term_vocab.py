"""
Controlled vocabularies for set_compare_terms strategy.
Each vocab is a dict mapping canonical term → set of aliases (all lowercase).
The extractor scans free text for any alias and returns the canonical term.
"""

ANATOMY_SITES: dict[str, set[str]] = {
    "buccal mucosa":      {"buccal mucosa", "buccal", "cheek"},
    "tongue":             {"tongue", "lateral tongue", "ventral tongue", "dorsal tongue"},
    "floor of mouth":     {"floor of mouth", "floor"},
    "palate":             {"palate", "hard palate", "soft palate"},
    "gingiva":            {"gingiva", "gum", "gums", "gingival"},
    "lip":                {"lip", "lips", "lower lip", "upper lip"},
    "alveolar ridge":     {"alveolar ridge", "alveolar", "ridge"},
    "retromolar trigone": {"retromolar trigone", "retromolar"},
    "oropharynx":         {"oropharynx", "oropharyngeal"},
    "commissure":         {"commissure", "labial commissure"},
}

DYSPLASIA_GRADES: dict[str, set[str]] = {
    "no dysplasia":       {"no dysplasia", "normal", "hyperkeratosis", "hyperplasia",
                           "benign", "non-dysplastic", "without dysplasia"},
    "mild dysplasia":     {"mild dysplasia", "mild", "low-grade dysplasia"},
    "moderate dysplasia": {"moderate dysplasia", "moderate"},
    "severe dysplasia":   {"severe dysplasia", "severe", "high-grade dysplasia"},
    "carcinoma in situ":  {"carcinoma in situ", "cis", "in situ"},
    "oscc":               {"oscc", "oral squamous cell carcinoma", "squamous cell carcinoma",
                           "scc", "invasive carcinoma", "oral cancer", "malignant"},
    "verrucous carcinoma": {"verrucous carcinoma", "verrucous"},
}

CANCER_STAGES: dict[str, set[str]] = {
    "stage i":   {"stage i", "stage 1"},
    "stage ii":  {"stage ii", "stage 2"},
    "stage iii": {"stage iii", "stage 3"},
    "stage iv":  {"stage iv", "stage 4"},
}

RISK_FACTORS: dict[str, set[str]] = {
    "smoking":          {"smoking", "smoker", "cigarette", "bidi", "cigar", "tobacco smoking"},
    "smokeless tobacco":{"smokeless tobacco", "chewing tobacco", "tobacco chewing",
                         "gutkha", "gutka", "pan tobacco", "khaini"},
    "betel quid":       {"betel quid", "betel", "pan", "areca", "areca nut", "betel nut"},
    "alcohol":          {"alcohol", "alcoholic", "drinking"},
    "hpv":              {"hpv", "human papillomavirus"},
    "no risk factors":  {"no risk factors", "no habits", "none reported"},
}

POPULATION_CATEGORIES: dict[str, set[str]] = {
    # NOTE: keep aliases specific — the GT column for "suspicious" contains the word
    # "malignant" (in "seemingly malignant") and the "healthy" column overlaps "innocuous".
    "suspicious lesions":  {"suspicious", "potentially malignant", "opmd", "premalignant",
                            "clinically evident suspicious"},
    "malignant lesions":   {"cancer", "carcinoma", "oscc"},   # 'malignant' removed — fires on GT suspicious column
    "innocuous lesions":   {"innocuous", "benign", "nonsuspicious"},  # 'healthy'/'normal' removed — belong to healthy controls
    "healthy controls":    {"healthy", "healthy patients", "healthy subjects", "control",
                            "without lesions"},
}

# Sequence-of-tests canonical values
SEQUENCE_CATEGORIES: dict[str, set[str]] = {
    "index test first": {"index test first", "index test before", "it first"},
    "reference standard first": {"reference standard first", "biopsy first", "rs first"},
    "parallel":         {"parallel", "simultaneous", "same time", "concurrent"},
    "NR":               {"nr", "not reported", "unclear"},
}
