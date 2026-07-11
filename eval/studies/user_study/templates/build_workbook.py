"""Build the participant-fillable CD004714 extraction workbook.

Written for CLINICIANS, not engineers: no "enum / float / int / string", every
abbreviation spelled out, every drop-down code explained in plain English, a
hover-note on each column header, and a glossary tab.

The column *headers* (row 1 of each data tab) are kept EXACTLY as the scoring
harness expects (score_participant.py) — only the human-facing text is plain.
Export each data tab to a CSV of the same name to feed the scorer.
"""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter

PAPERS = [
    "Artese 2015", "Bukleta 2018", "Chen 2012", "D'Aiuto 2018", "Das 2019",
    "El-Makaky 2020", "Engebretson 2013", "Gay 2014", "Jones 2007",
    "Kapellas 2017", "Katagiri 2009", "Kaur 2015", "Kiran 2005",
    "Koromantzos 2011", "Lee 2020", "Mauri-Obradors 2018", "Moeintaghavi 2012",
    "Qureshi 2021", "Raman 2014", "Rapone 2021", "Singh 2008", "Sun 2011",
    "Telgi 2013", "Tsobgny-Tsague 2018", "Vergnes 2018", "Wang S 2017",
    "Wang Y 2017", "Yun 2007", "Zhang 2013",
]

# plain-English meaning for every drop-down code (clinicians never see raw codes)
CODE_MEANINGS = {
    "T1DM": "type 1 diabetes", "T2DM": "type 2 diabetes",
    "mixed": "a mix of both", "NR": "not reported in the paper",
    "good": "good control", "fair": "fair control", "poor": "poor control",
    "SI_alone": "deep cleaning below the gumline on its own",
    "SI_plus_systemic_local_antimicrobial": "deep cleaning PLUS an antibiotic (swallowed or placed in the gum pocket)",
    "SI_plus_mouthrinse": "deep cleaning PLUS an antimicrobial mouthrinse",
    "usual_care": "control group — no active gum treatment / usual care",
    "supragingival_only": "cleaning above the gumline only",
    "HbA1c": "glycated haemoglobin — the blood-sugar marker (%)",
    "CAL": "clinical attachment level", "PPD": "probing pocket depth",
    "BOP": "bleeding on probing", "PI": "plaque index", "GI": "gingival index",
    "3-4_months": "3 to 4 months after start", "6_months": "6 months after start",
    "12_months": "12 months after start",
    "SI_vs_usual_care": "deep cleaning vs no active treatment / usual care",
    "SI_plus_antimicrobial": "deep cleaning + antibiotic comparison",
    "SI_plus_mouthrinse": "deep cleaning + mouthrinse comparison",
    "low": "low risk of bias", "unclear": "unclear", "high": "high risk of bias",
}

# how-to-enter, in words a clinician reads without thinking
FMT_PLAIN = {
    "text": "Text — type freely (or NR).",
    "whole": "A whole number (no decimals).",
    "decimal": "A number, decimals OK.",
    "pick": "Click the cell and pick from the drop-down list.",
}

ROB_DOMAINS = [
    ("random_sequence_generation", "Was the way patients were randomised proper?"),
    ("allocation_concealment", "Was the upcoming group assignment hidden until enrolment?"),
    ("blinding_participants", "Were patients / staff kept unaware of the group?"),
    ("blinding_clinical_operator", "Was the clinician giving the treatment kept unaware?"),
    ("blinding_outcome_assessor", "Was the person measuring outcomes kept unaware?"),
    ("incomplete_outcome_data", "Were drop-outs / missing data handled well?"),
    ("selective_reporting", "Were all the planned outcomes actually reported?"),
    ("other_bias", "Any other possible source of bias?"),
]

# Each column: (header, format-key, allowed-list-or-None, plain meaning, example)
FORMS = {
    "study_char": {
        "one_row_per": "study — ONE row per paper (29 rows).",
        "cols": [
            ("Paper", "text", None, "The study's name: first author + year. Match the paper's file name.", "Artese 2015"),
            ("country", "text", None, "Country where the study was run (from author affiliations / setting).", "Brazil"),
            ("setting", "text", None, "Where patients were treated (hospital / primary care / university).", "hospital"),
            ("number_of_centres", "text", None, "How many centres or clinics recruited patients.", "1"),
            ("trial_design", "text", None, "How the trial was designed — copy the paper's wording (arms, parallel/crossover, blinding).", "2-arm RCT"),
            ("recruitment_period", "text", None, "When patients were recruited, if stated.", "February 2011 to December 2013"),
            ("funding_source", "text", None, "Who funded the study (copy wording), or 'none declared' / NR.", "FAPESP"),
            ("notes", "text", None, "Anything notable — sample-size calculation, data limitations, conflicts of interest.", "HbA1c shown only in a graph"),
        ],
    },
    "patient_pop": {
        "one_row_per": "study — ONE row per paper (29 rows).",
        "cols": [
            ("Paper", "text", None, "First author + year.", "Artese 2015"),
            ("age_arm_a", "decimal", None, "Average age (years) of the gum-treatment group (arm A).", "54.4"),
            ("age_arm_a_sd", "decimal", None, "Spread of age (standard deviation) in arm A.", "5.8"),
            ("age_arm_b", "decimal", None, "Average age (years) of the control group (arm B).", "52"),
            ("age_arm_b_sd", "decimal", None, "Spread of age (standard deviation) in arm B.", "3.3"),
            ("pct_female_arm_a", "decimal", None, "Percentage of women in arm A.", "56.3"),
            ("pct_female_arm_b", "decimal", None, "Percentage of women in arm B.", "52"),
            ("diabetes_type", "pick", ["T1DM", "T2DM", "mixed", "NR"], "Type of diabetes the patients had. Put extra detail (e.g. 'poorly controlled') in notes.", "T2DM"),
            ("baseline_hba1c_arm_a", "decimal", None, "Starting (baseline) HbA1c %, arm A.", "7.1"),
            ("baseline_hba1c_arm_b", "decimal", None, "Starting (baseline) HbA1c %, arm B.", "8.2"),
            ("metabolic_control_level", "pick", ["good", "fair", "poor", "NR"], "Overall blood-sugar control at the start, judged from the average baseline HbA1c.", "poor"),
            ("duration_since_diabetes_dx", "text", None, "How long since the diabetes diagnosis (copy wording).", "≥3 yrs"),
            ("tobacco_use", "text", None, "Smoking status (copy wording); may be NR or 'excluded by the criteria'.", "none (excluded)"),
            ("alcohol_consumption", "text", None, "Alcohol use (copy wording) or NR.", "NR"),
            ("inclusion_criteria", "text", None, "Who could take part — copy the inclusion criteria.", "≥35 yrs, T2DM ≥3 yrs, severe chronic periodontitis"),
            ("exclusion_criteria", "text", None, "Who was kept out — copy the exclusion criteria.", "pregnant, smokers, BMI>35"),
            ("n_randomised", "whole", None, "Total patients randomised (all groups combined).", "24"),
            ("n_evaluated", "text", None, "Number actually assessed at follow-up (may differ by timepoint).", "24 at 6 mths"),
        ],
    },
    "interventions": {
        "one_row_per": "treatment group (ARM) — add a row for EACH arm of the study.",
        "cols": [
            ("Paper", "text", None, "First author + year.", "Artese 2015"),
            ("arm_label", "text", None, "A short label for this group, unique within the study.", "Gp A (n=12)"),
            ("comparison_summary", "text", None, "One line: what this study compares.", "deep cleaning vs cleaning above the gumline"),
            ("intervention_description", "text", None, "What THIS group received — copy the paper (procedure, anaesthesia, instruments, how often).", "supragingival scaling with ultrasonic device, single 60 min appointment"),
            ("n_in_arm", "whole", None, "Number of patients put in THIS group.", "12"),
            ("duration_of_followup", "text", None, "How long patients were followed.", "6 mths"),
            ("cochrane_subgroup_category", "pick", ["SI_alone", "SI_plus_systemic_local_antimicrobial", "SI_plus_mouthrinse", "usual_care", "supragingival_only"], "What kind of treatment this group got — pick the closest category.", "SI_alone"),
        ],
    },
    "outcomes": {
        "one_row_per": "result — ONE row per measurement x timepoint x comparison. Add as many rows per paper as needed.",
        "cols": [
            ("Paper", "text", None, "First author + year.", "Bukleta 2018"),
            ("outcome_type", "pick", ["HbA1c", "CAL", "PPD", "BOP", "PI", "GI"], "Which measurement this row reports.", "HbA1c"),
            ("timepoint", "pick", ["3-4_months", "6_months", "12_months"], "When it was measured (months after start).", "3-4_months"),
            ("subgroup", "pick", ["SI_vs_usual_care", "SI_plus_antimicrobial", "SI_plus_mouthrinse"], "Which comparison this row belongs to.", "SI_vs_usual_care"),
            ("mean_arm1", "decimal", None, "Average value in the gum-treatment (deep-cleaning) group.", "8.03"),
            ("sd_arm1", "decimal", None, "Spread (standard deviation) in the treatment group.", "1.67"),
            ("n_arm1", "whole", None, "Number of patients in the treatment group at this timepoint.", "50"),
            ("mean_arm2", "decimal", None, "Average value in the control group.", "7.69"),
            ("sd_arm2", "decimal", None, "Spread (standard deviation) in the control group.", "2.09"),
            ("n_arm2", "whole", None, "Number of patients in the control group.", "50"),
        ],
    },
}

# risk_of_bias built programmatically: Paper + (judgment, reason) per domain
rob_cols = [("Paper", "text", None, "First author + year.", "Artese 2015")]
for dom, question in ROB_DOMAINS:
    rob_cols.append((f"{dom}_judgment", "pick", ["low", "unclear", "high"],
                     f"Your rating for: {question}", "low"))
    rob_cols.append((f"{dom}_reason", "text", None,
                     "Copy the exact sentence(s) from the paper that back up your rating.",
                     '"randomisation was computer-generated"'))
FORMS["risk_of_bias"] = {
    "one_row_per": "study — ONE row per paper (29 rows).", "cols": rob_cols,
}

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
PAPER_FILL = PatternFill("solid", fgColor="F1F5F9")
THIN = Side(style="thin", color="D1D5DB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def header_note(col_name, fmt, allowed, meaning, example):
    """Plain-English hover note shown when a clinician clicks the column header."""
    lines = [meaning, "", FMT_PLAIN[fmt]]
    if allowed:
        lines.append("")
        lines.append("Choose one:")
        for code in allowed:
            lines.append(f"  • {code} = {CODE_MEANINGS.get(code, code)}")
    lines.append("")
    lines.append(f"Example: {example}")
    lines.append("Use NR if the paper does not report it.")
    c = Comment("\n".join(lines), "study team")
    c.width, c.height = 300, 200
    return c


def build_form_sheet(wb, name, spec):
    ws = wb.create_sheet(title=name)
    cols = spec["cols"]
    ws.append([c[0] for c in cols])

    for c in range(1, len(cols) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(vertical="center", horizontal="left")
        cell.border = BORDER
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "B2"

    for i, paper in enumerate(PAPERS, start=2):
        cell = ws.cell(row=i, column=1, value=paper)
        cell.fill, cell.font = PAPER_FILL, Font(bold=True)

    last_row = 400
    for idx, (col_name, fmt, allowed, meaning, example) in enumerate(cols, start=1):
        letter = get_column_letter(idx)
        ws.cell(row=1, column=idx).comment = header_note(col_name, fmt, allowed, meaning, example)
        if fmt == "text" and (col_name in (
                "notes", "inclusion_criteria", "exclusion_criteria",
                "intervention_description", "comparison_summary") or col_name.endswith("_reason")):
            ws.column_dimensions[letter].width = 40
        elif col_name == "Paper":
            ws.column_dimensions[letter].width = 20
        else:
            ws.column_dimensions[letter].width = max(13, len(col_name) + 2)
        if allowed:
            dv = DataValidation(type="list", formula1='"' + ",".join(allowed) + '"',
                                allow_blank=True, showDropDown=False)
            dv.error = "Please pick one of the listed answers (or NR)."
            dv.errorTitle = "Not one of the choices"
            dv.prompt = "Pick one: " + ", ".join(allowed) + "  (or NR)"
            dv.promptTitle = "Choose from the list"
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{last_row}")
    return ws


def build_instructions(wb):
    ws = wb.create_sheet(title="START HERE")
    ws.column_dimensions["A"].width = 112
    rows = [
        ("CD004714 data-extraction workbook — gum treatment & blood-sugar control", "title"),
        ("", ""),
        ("You will read 29 trials and type their data into the tabs of this workbook, as if it were going into a real review.", ""),
        ("You may use Claude or Claude Code however you like, including writing your own prompts. There is no right way and no coaching.", ""),
        ("", ""),
        ("WHAT THIS REVIEW COVERS — only extract studies and data that fit this scope", "head"),
        ("• People: adults/adolescents (16 years or older) who have BOTH diabetes (type 1 or type 2) AND periodontitis (gum disease).", ""),
        ("• Treatment: periodontal treatment — mainly deep cleaning below the gumline (subgingival instrumentation / scaling & root planing), on its own or together with an antibiotic or an antimicrobial mouthrinse.", ""),
        ("• Compared against: no active gum treatment or usual care (oral hygiene instruction and/or cleaning above the gumline).", ""),
        ("• Outcomes we record: HbA1c (blood-sugar control — the main one) plus the gum measures CAL, PPD, BOP, PI, GI — at 3-4 months, 6 months, or 12 months.", ""),
        ("• Studies: randomised controlled trials (RCTs) with at least 3 months of follow-up that report HbA1c. (The 29 papers listed are already eligible — you do not need to re-screen them.)", ""),
        ("", ""),
        ("HOW THE WORKBOOK IS LAID OUT", "head"),
        ("• There is one tab per form along the bottom. In every tab, the first column on the left already lists all 29 studies — use it as your checklist and fill in each study's row going across to the right.", ""),
        ("• Click any column heading (the dark cells in row 1) to see a note explaining what it means, what to type, the choices, and an example. The same explanations are all in the 'Field guide' tab if you'd rather read them in one place.", ""),
        ("• Some cells have a drop-down list: click the cell, a small arrow appears on the right, and you pick one of the answers.", ""),
        ("", ""),
        ("HOW MANY ROWS EACH TAB NEEDS", "head"),
        ("• study_char, patient_pop, risk_of_bias  →  one row per study (the 29 are already listed).", ""),
        ("• interventions  →  one row per treatment GROUP (arm). Copy the paper name down and add a row for each group.", ""),
        ("• outcomes  →  one row per measurement at each timepoint and comparison. Add as many rows per paper as you need.", ""),
        ("", ""),
        ("A FEW RULES", "head"),
        ("• If the paper does NOT report something, type  NR  (Not Reported). Please don't leave a known value blank.", ""),
        ("• Take the information from the paper itself — please don't add outside knowledge.", ""),
        ("• When a field asks you to 'copy wording', paste the paper's own words.", ""),
        ("• Move on from a value once you trust it.", ""),
        ("", ""),
        ("There is a 'Word list' tab explaining the abbreviations (HbA1c, CAL, PPD, arm, SD, …) and a 'Field guide' tab with every column.", ""),
        ("", ""),
        ("WHEN YOU ARE DONE", "head"),
        ("• Save the workbook. Then save each of the 5 data tabs as a CSV file of the same name (study_char.csv, patient_pop.csv, …).", ""),
        ("• Please keep the column headings exactly as they are — that is how we line your answers up with everyone else's.", ""),
    ]
    for i, (text, kind) in enumerate(rows, start=1):
        cell = ws.cell(row=i, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if kind == "title":
            cell.font = Font(bold=True, size=13, color="1F2937")
        elif kind == "head":
            cell.font = Font(bold=True, size=11, color="1F2937")
    ws.sheet_view.showGridLines = False


def build_glossary(wb):
    ws = wb.create_sheet(title="Word list")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 90
    terms = [
        ("Term", "What it means"),
        ("NR", "Not Reported — type this when the paper does not give the value (don't leave it blank)."),
        ("arm / group", "One treatment group in the trial. Arm A = the gum-treatment group; arm B = the control group."),
        ("SI", "Subgingival instrumentation — deep cleaning below the gumline (also called scaling & root planing, SRP)."),
        ("usual care / control", "The comparison group that did not get the active gum treatment."),
        ("baseline", "The measurement taken before treatment starts."),
        ("HbA1c", "Glycated haemoglobin — the blood marker for average blood-sugar control, in %."),
        ("CAL", "Clinical attachment level (gum measurement, mm)."),
        ("PPD", "Probing pocket depth (gum measurement, mm)."),
        ("BOP", "Bleeding on probing (% of sites that bleed)."),
        ("PI", "Plaque index."),
        ("GI", "Gingival index."),
        ("SD (standard deviation)", "A number showing how spread out the values are around the average."),
        ("timepoint", "When a measurement was taken, counted in months after the start."),
        ("risk of bias", "A judgement (low / unclear / high) of how trustworthy a part of the study's method is."),
        ("drop-down list", "Click the cell; a small arrow appears; pick one of the offered answers."),
    ]
    for i, (a, b) in enumerate(terms, start=1):
        ca, cb = ws.cell(row=i, column=1, value=a), ws.cell(row=i, column=2, value=b)
        cb.alignment = Alignment(wrap_text=True, vertical="top")
        if i == 1:
            ca.font = cb.font = HEADER_FONT
            ca.fill = cb.fill = HEADER_FILL
        else:
            ca.font = Font(bold=True)


def build_field_guide(wb):
    ws = wb.create_sheet(title="Field guide")
    ws.sheet_view.showGridLines = False
    for i, w in enumerate([30, 26, 50, 38], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    r = 1
    for form, spec in FORMS.items():
        ws.cell(row=r, column=1, value=f"{form}  ({spec['one_row_per']})").font = Font(bold=True, size=12, color="1F2937")
        r += 1
        for c, h in enumerate(["Column", "What to type", "What it means", "Example"], start=1):
            cell = ws.cell(row=r, column=c, value=h)
            cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        r += 1
        for col_name, fmt, allowed, meaning, example in spec["cols"]:
            what = FMT_PLAIN[fmt]
            if allowed:
                what = "Pick one: " + "; ".join(f"{code} = {CODE_MEANINGS.get(code, code)}" for code in allowed)
            for c, val in enumerate([col_name, what, meaning, example], start=1):
                cell = ws.cell(row=r, column=c, value=val)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            r += 1
        r += 1


def main():
    wb = Workbook()
    wb.remove(wb.active)
    build_instructions(wb)
    for name, spec in FORMS.items():
        build_form_sheet(wb, name, spec)
    build_glossary(wb)
    build_field_guide(wb)
    out = "CD004714_extraction_workbook.xlsx"
    wb.save(out)
    print("wrote", out, "— tabs:", wb.sheetnames)


if __name__ == "__main__":
    main()
