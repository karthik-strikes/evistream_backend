"""
Per-form modules own their own FIELDS + CFG. This package assembles them into
a single FORM_REGISTRY dict for the notebook and build_aligned_sheets.py.
"""

from . import (
    study_characteristics,
    patient_population,
    reference_standard,
    index_test,
    outcomes,
)

FORM_REGISTRY: dict[str, dict] = {
    "study_characteristics": study_characteristics.CFG,
    "patient_population":    patient_population.CFG,
    "reference_standard":    reference_standard.CFG,
    "index_test":            index_test.CFG,
    "outcomes":              outcomes.CFG,
}
