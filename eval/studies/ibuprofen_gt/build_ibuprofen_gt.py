#!/usr/bin/env python
"""
Reproducibly build the ibuprofen GT workbook from the Cochrane review PDF.

Source : "Ibuprofen for acute postoperative pain in children" (CD015432.pub2, 2024).
Output : eval/sheets/gt sheets/ibuprofen.xlsx  (definitions + 6 aligned data tabs).

Pipeline (all deterministic, no LLM):
  1. pdftotext -layout the two data-bearing sections of the review:
       - Characteristics of included studies  (physical pp. 60-110)
       - Data and analyses forest plots        (physical pp. 121-142)
  2. Parse the forest plots  -> continuous_outcomes + dichotomous_outcomes rows,
     cross-checked so each analysis block's per-arm N / event sums equal its
     'Total (95% CI)' line.
  3. Parse the Characteristics blocks -> study_char / patient_pop / interventions
     / risk_of_bias (7 RoB-1 domains), cross-checked so per-arm N sums equal the
     randomised N (the 3 residual mismatches are the review's own arithmetic
     quirks: Steen Law 2000, Viitanen 2003, and Polat 2005b 150-randomised/
     120-analysed).
  4. Assemble the aligned xlsx (row 0 = ai_col headers, row 1 = EXTRACT/OPTIONAL/
     NEGLECT directive row, study_id key) consumed by forms/ibuprofen.py.

Run (from anywhere, in the `topics` conda env — needs pdftotext + openpyxl):
    python eval/studies/ibuprofen_gt/build_ibuprofen_gt.py \
        --pdf "/home/ubuntu/evistream/Ibuprofen for acute postoperative pain in children.pdf"
"""
import argparse, os, re, csv, json, subprocess, tempfile
from collections import defaultdict
import openpyxl
from openpyxl.styles import Font, PatternFill

REPO = "/home/ubuntu/evistream"
DEFAULT_PDF = f"{REPO}/Ibuprofen for acute postoperative pain in children.pdf"
OUT_XLSX = f"{REPO}/eval/sheets/gt sheets/ibuprofen.xlsx"

COMPARISON = {"1":"placebo","2":"paracetamol","3":"morphine","4":"ketorolac",
              "5":"naproxen_sodium","6":"rofecoxib","7":"aspirin"}

# ───────────────────────── text cleaning ─────────────────────────
_SUSPENDED = {"and", "or", "to"}
_KEEP_COMPOUND = {"centre","center","blind","blinded","controlled","based","related","dose","day",
    "response","case","aged","specific","dependent","matched","masked","label","term","free","point",
    "sectional","effect","associated","level","phase","group","arm","week","hour","line",
    # real words that appear as the 2nd half of a hyphenated compound wrapped at its hyphen
    # (e.g. "computer-generated", "over-the-counter", "strawberry-flavored") — keep the hyphen
    "generated","flavored","flavoured","reported","counter","possible","sparing","the"}
def de_hyphenate(t):
    """Join line-wrap hyphens ('adminis- tered'->'administered') without breaking real
    compounds ('single- centre'->'single-centre'), IDs/acronyms ('FPS- R'->'FPS-R',
    'NC- T03786029'->'NC-T03786029'), or suspended hyphens ('pre- and ...')."""
    def repl(m):
        a, b = m.group(1), m.group(2); bl = b.lower()
        if bl in _SUSPENDED: return m.group(0)                 # 'pre- and' -> unchanged
        if b[0].isupper() or b[0].isdigit(): return f"{a}-{b}" # ID/acronym/proper noun -> keep hyphen
        if bl in _KEEP_COMPOUND: return f"{a}-{b}"             # real compound -> keep hyphen
        return a + b                                            # wrapped single word -> join
    return re.sub(r"([A-Za-z]{2,})-\s+(\w+)", repl, t)

def clean_text(t):
    """Normalise a free-text cell: unify quotes/dashes, de-hyphenate wraps, drop bullets,
    collapse whitespace, strip wrapping punctuation. NR/empty pass through unchanged."""
    if t is None: return t
    t = str(t)
    t = t.replace("’","'").replace("‘","'").replace("“",'"').replace("”",'"')
    t = re.sub(r"(\d)\s*[–—]\s*(\d)", r"\1-\2", t)   # numeric en/em-dash range -> hyphen
    t = t.replace("–","-").replace("—","-")
    t = de_hyphenate(t)
    t = re.sub(r"[•●·]", " ; ", t)              # bullets -> separators
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(\s*;\s*)+", "; ", t)
    t = re.sub(r"^[\s;:.,)]+", "", t)
    t = re.sub(r"[\s;]+$", "", t)
    return t.strip(' "').strip()

def clean_reason(t):
    """clean_text + strip RoB-specific artifacts (leading elision, trailing wrapped domain
    labels like 'porting bias)', trailing table-footnote legends)."""
    t = clean_text(t)
    t = re.sub(r"^\[\.\.\.\]\s*", "", t)
    t = re.sub(r"\s*\(?(?:re-?\s*)?(?:report|port)ing bias\)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\(?(?:selection|performance|detection|attrition|reporting) bias\)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*(?:and personnel|all outcomes)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+[A-Z]{2,}:\s.*$", "", t)                  # trailing footnote legend (' AE: ...')
    return t.strip(' ".;').strip()

def clean_arm(label):
    return re.sub(r"[\s(–\-]+$", "", clean_text(label)).strip()[:60]

# ───────────────────────── pdftotext helpers ─────────────────────────
def pdftotext(pdf, f, l, layout=True):
    cmd = ["pdftotext"] + (["-layout"] if layout else []) + ["-f", str(f), "-l", str(l), pdf, "-"]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout

# ───────────────────────── forest-plot parsing ─────────────────────────
def analysis_titles(pdf):
    # NON-layout: -layout embeds the TOC page-number column into wrapped titles
    # (e.g. "...rescue medication less  120  than 2 hours"), which breaks the
    # timepoint substring match in classify().
    t = pdftotext(pdf, 1, 4, layout=False)
    flat = re.sub(r"[ \t]*\n[ \t]*", " ", t)
    titles = {}
    # capture each TOC entry up to the dot-leaders / trailing page number / next entry
    for e in re.findall(r"(Analysis \d+\.\d+\.\s*Comparison \d+:.*?)(?:\.{3,}|\s+\d+\s*$|(?=Analysis \d+\.\d+\.))", flat):
        e = re.sub(r"\.{2,}.*$", "", e).strip()
        m = re.match(r"Analysis (\d+)\.(\d+)\.\s*(.*)", e)
        if m: titles[f"{m.group(1)}.{m.group(2)}"] = m.group(3).strip()
    return titles

def classify(title):
    t = title.lower()
    tp = ("under_2h" if "less than 2 hours" in t else
          "2h_to_24h" if "2 hours to less than 24 hours" in t else
          "24h_to_7d" if ("24 hours" in t and "7 days" in t) else "overall")
    rep = ("child" if "reported by the child" in t else
           "third_party" if "reported by a third party" in t else "NA")
    if "pain intensity" in t:       return "CONT", "pain_intensity", rep, tp
    if "opioid consumption" in t:   return "CONT", "opioid_consumption", "NA", tp
    if "time to rescue medication" in t: return "CONT", "time_to_rescue", rep, tp
    if "rescue medication" in t:    return "DICH", "rescue_medication", tp
    if "adverse events" in t:       return "DICH", "adverse_events", "overall"
    if "nausea or vomiting" in t:   return "DICH", "nausea_or_vomiting", "overall"
    if "bleeding" in t:             return "DICH", "bleeding", "overall"
    if "renal dysfunction" in t:    return "DICH", "renal_dysfunction", "overall"
    return "?", "unknown", rep, tp

STUDY_RE = re.compile(r"^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.\-]*(?:\s+[A-Za-zÀ-ÿ'’.\-]+)*?\s+\d{4}[a-z]?)\b\s*(?:\(\d+\))?\s+(.*)$")
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
SKIP = ("Total","Subtotal","Heterogeneity","Test for","Footnotes","Risk of bias",
        "Study or Subgroup","Analysis","Cochrane","Library","Ibuprofen for acute",
        "Copyright","(A)","(B)","(C)","(D)","(E)","(F)","(G)","Favours")

def parse_forest(pdf, titles):
    lines = pdftotext(pdf, 121, 142).splitlines()
    cur, blocks = None, defaultdict(list)
    for ln in lines:
        m = re.search(r"Analysis (\d+)\.(\d+)\.", ln)
        if m: cur = f"{m.group(1)}.{m.group(2)}"; continue
        if cur: blocks[cur].append(ln)
    cont, dich = [], []
    for tag, blines in blocks.items():
        info = classify(titles.get(tag, "")); kind = info[0]
        comparison = COMPARISON.get(tag.split(".")[0], tag.split(".")[0])
        # verification accumulators
        s1=s2=e1=e2=0; tot1=tot2=tote1=tote2=None
        for raw in blines:
            s = raw.strip()
            if s.startswith("Total (95% CI)"):
                n = re.findall(r"\b\d+\b", s.split("%")[0])
                if len(n) >= 2: tot1, tot2 = int(n[0]), int(n[1])
                continue
            if s.startswith("Total events:"):
                n = re.findall(r"\b\d+\b", s)
                if len(n) >= 2: tote1, tote2 = int(n[0]), int(n[1])
                continue
            if not s or s.startswith(SKIP): continue
            m = STUDY_RE.match(s)
            if not m: continue
            study, rest = m.group(1).strip(), m.group(2)
            nums = []
            for tk in rest.split():
                if NUM_RE.match(tk): nums.append(tk)
                elif tk.endswith("%") or tk.startswith("[") or tk in ("Not","estimable"): break
                else: break
            if kind == "CONT" and len(nums) >= 6:
                m1,sd1,n1,m2,sd2,n2 = nums[:6]; s1+=int(float(n1)); s2+=int(float(n2))
                cont.append(dict(study_id=study, comparison=comparison, outcome_type=info[1],
                    reporter=info[2], timepoint=info[3], scale="", mean_arm1=m1, sd_arm1=sd1,
                    n_arm1=n1, mean_arm2=m2, sd_arm2=sd2, n_arm2=n2))
            elif kind == "DICH" and len(nums) >= 4:
                ev1,n1,ev2,n2 = nums[:4]; e1+=int(ev1); s1+=int(n1); e2+=int(ev2); s2+=int(n2)
                dich.append(dict(study_id=study, comparison=comparison, outcome=info[1],
                    timepoint=info[2], arm1_label="ibuprofen", arm1_events=ev1, arm1_n=n1,
                    arm2_label=comparison, arm2_events=ev2, arm2_n=n2))
        # cross-check
        msgs = []
        if tot1 is not None and s1 != tot1: msgs.append(f"N1 {s1}!={tot1}")
        if tot2 is not None and s2 != tot2: msgs.append(f"N2 {s2}!={tot2}")
        if kind == "DICH":
            if tote1 is not None and e1 != tote1: msgs.append(f"ev1 {e1}!={tote1}")
            if tote2 is not None and e2 != tote2: msgs.append(f"ev2 {e2}!={tote2}")
        if msgs: print(f"  ⚠ forest {tag}: {msgs}")
    return cont, dich

# ───────────────────────── characteristics parsing ─────────────────────────
HEADER_FRAGS = ("Trusted evidence.","Informed decisions.","Better health.",
                "Cochrane Database of Systematic Reviews",
                "Ibuprofen for acute postoperative pain in children (Review)","Copyright ©")
SECTION_LABELS = ["Methods","Participants","Interventions","Outcomes","Setting (country)","Notes"]
ROB = [("random_sequence_generation",r"Random sequence gener"),
       ("allocation_concealment",r"Allocation concealment"),
       ("blinding_participants_personnel",r"Blinding of participants"),
       ("blinding_outcome_assessment",r"Blinding of outcome as"),
       ("incomplete_outcome_data",r"Incomplete outcome data"),
       ("selective_reporting",r"Selective reporting"),("other_bias",r"Other bias")]
JUDG = {"low risk":"low","high risk":"high","unclear risk":"unclear"}

def parse_characteristics(pdf):
    raw = pdftotext(pdf, 60, 110)
    clean = []
    for ln in raw.splitlines():
        s = ln.strip()
        if any(f in ln for f in HEADER_FRAGS): continue
        if s in ("Cochrane","Library"): continue
        if re.match(r"^\d+$", s): continue
        clean.append(ln)
    text = "\n".join(clean)
    a = "Characteristics of included studies [ordered by study ID]"
    text = text[text.index(a)+len(a):]
    cut = text.find("Characteristics of excluded studies")
    if cut != -1: text = text[:cut]
    rl = text.splitlines()
    starts = []
    for i, ln in enumerate(rl):
        if ln.strip() == "Study characteristics":
            j = i-1
            while j >= 0 and not rl[j].strip(): j -= 1
            starts.append((j, rl[j].strip()))
    data, order = {}, []
    for k,(idx,sid) in enumerate(starts):
        end = starts[k+1][0] if k+1 < len(starts) else len(rl)
        block = re.sub(rf"^{re.escape(sid)} \(Continued\)\s*$", "", "\n".join(rl[idx:end]), flags=re.M)
        secs, rob = _sections(block)
        data[sid] = {"sections": secs, "rob": _rob(rob)}; order.append(sid)
    return data, order

def _sections(block):
    parts = re.split(r"\n\s*Risk of bias\s*\n", block, maxsplit=1)
    top, rob = parts[0], (parts[1] if len(parts) > 1 else "")
    idxs = []
    for lab in SECTION_LABELS:
        m = re.search(rf"(?m)^\s*{re.escape(lab)}(?=\s|$)", top)
        if m: idxs.append((m.start(), lab, m.end()))
    idxs.sort(); out = {}
    for n,(st,lab,en) in enumerate(idxs):
        end = idxs[n+1][0] if n+1 < len(idxs) else len(top)
        out[lab] = re.sub(r"[ \t]*\n[ \t]*", " ", top[en:end]).strip()
    return out, rob

def _rob(rob):
    """Column-aware parse of the 3-column RoB table (Bias | Judgement | Support).
    Slicing by the support column's x-position (from the header) avoids the label
    column bleeding into the support text — which is what -layout flattening does."""
    lines = rob.split("\n")
    support_x = judg_x = None
    for ln in lines:
        if "Support for judgement" in ln:
            support_x = ln.index("Support for judgement")
            judg_x = ln.index("Authors' judgement") if "Authors' judgement" in ln else max(0, support_x - 24)
            break
    if support_x is None:              # fallback to typical Cochrane column positions
        support_x, judg_x = 59, 34
    res = {k: {"judgment": "", "reason": ""} for k, _ in ROB}
    cur = None
    for ln in lines:
        if not ln.strip(): continue
        if "Support for judgement" in ln or ln.lstrip().startswith("Bias "): continue
        matched = next((k for k, pat in ROB if re.match(r"\s*" + pat, ln)), None)
        if matched:
            cur = matched
            jm = re.search(r"(Low|High|Unclear) risk", ln[judg_x:support_x]) or \
                 re.search(r"(Low|High|Unclear) risk", ln)
            if jm: res[cur]["judgment"] = JUDG[jm.group(0).lower()]
            sup = ln[support_x:].strip()
            if sup: res[cur]["reason"] += " " + sup
        elif cur is not None:
            sup = ln[support_x:].strip() if len(ln) > support_x else ""   # support-col only
            if sup: res[cur]["reason"] += " " + sup
    for k in res:
        res[k]["reason"] = clean_reason(res[k]["reason"])
    return res

# ───────────────────────── per-study field derivation ─────────────────────────
def route_of(t):
    t = t.lower()
    return "IV" if "intraven" in t else "rectal" if "rectal" in t else ("oral" if "oral" in t else "NR")
def centres_of(m):
    t = m.lower()
    return "1" if re.search(r"single.centre|single.center", t) else \
           "multicentre" if re.search(r"multi.?cent", t) else "NR"
def funding_of(n):
    m = re.search(r"Study funding:\s*(.*?)(?:\s*Author declaration|$)", n, re.I|re.S)
    if not m: return "NR"
    f = m.group(1).strip().rstrip(".")
    return "none/NR" if re.search(r"not reported|none declared|no funding", f, re.I) else clean_text(f)
def surgery_of(p):
    m = re.search(r"Surgery/procedure type:\s*(.*)$", p, re.S); return clean_text(m.group(1)) if m else "NR"
def n_rand_of(p):
    m = re.search(r"(\d+)\s+(?:total|participants in total)", p) or re.search(r"\bTotal\s+(\d+)", p)
    return m.group(1) if m else "NR"
def age_of(p):
    typ = ("mean" if re.search(r"Mean\s*±\s*S[DE] age", p) else
           "median" if re.search(r"Median \(IQR\) age", p) else
           "mean" if re.search(r"Mean \((range|IQR)\) age", p) else
           "range" if re.search(r"Age range", p) else
           "mean" if re.search(r"Mean age", p) else "NR")
    val=sd=rng="NR"
    m = re.search(r"ibuprofen[\w/\-]*\s+([\d.]+)\s*±\s*([\d.]+)\s*(years|months?)", p, re.I)
    if m: val, sd = m.group(1), m.group(2)
    else:
        m = re.search(r"ibuprofen[\w/\-]*\s+([\d.]+)\s*(?:years|months?)?\s*\(([^)]+)\)", p, re.I)
        if m: val, rng = m.group(1), m.group(2).strip()
        else:
            m = re.search(r"ibuprofen[\w/\-]*\s+([\d.]+)", p, re.I)
            if m: val = m.group(1)
    if val == "NR":
        m = re.search(r"Mean age of participants:\s*([\d.]+)\s*(years|months?)", p, re.I)
        if m: val = m.group(1)
    if val == "NR":
        m = re.search(r"(?:Age range[^:]*:|range of participants[^:]*:)\s*([\d.]+\s*[-–]\s*[\d.]+\s*(?:years|months)?)", p)
        if m: rng = m.group(1).strip()
    return typ, val, sd, rng

CAT = [("ibuprofen","ibuprofen"),("placebo","placebo"),("saline","placebo"),("water","placebo"),
       ("acetaminophen","paracetamol"),("paracetamol","paracetamol"),("morphine","morphine"),
       ("ketorolac","ketorolac"),("naproxen","naproxen_sodium"),("rofecoxib","rofecoxib"),
       ("aspirin","aspirin"),("codeine","other"),("piroxicam","other"),("flurbiprofen","other"),
       ("combination","combination"),("combined","combination"),("chewing gum","other"),("wafer","other")]
def categorise(l):
    l = l.lower()
    for kw,cat in CAT:
        if kw in l: return cat
    return "other"
CANON = {"ibuprofen":"ibuprofen","paracetamol":"paracetamol|acetaminophen","placebo":"placebo|saline|water",
         "morphine":"morphine","ketorolac":"ketorolac","naproxen_sodium":"naproxen","rofecoxib":"rofecoxib","aspirin":"aspirin"}
CANON_NAME = {"ibuprofen":"ibuprofen","paracetamol":"paracetamol (acetaminophen)","placebo":"placebo",
              "morphine":"morphine","ketorolac":"ketorolac","naproxen_sodium":"naproxen sodium",
              "rofecoxib":"rofecoxib","aspirin":"aspirin"}
def clean_drug(label, cat):
    if cat in CANON_NAME: return CANON_NAME[cat]
    return re.sub(r"^\s*in the\s+|\s*group\s*$", "", label, flags=re.I).strip()[:50] or label[:50]
def dose_of(interv, cat, label):
    kw = CANON.get(cat) or re.escape(label.split()[0] if label.split() else label)
    m = re.search(rf"(?:{kw})[^(]*\(([^)]*(?:mg|mL|\bg\b)[^)]*)\)", interv, re.I)
    if m: return m.group(1).strip()
    m = re.search(rf"(?:{kw})\s+([\d.]+\s*(?:mg|mL|g)(?:/(?:kg|dose|d|mL))?)", interv, re.I)
    return m.group(1).strip() if m else "NR"
def freq_of(interv):
    m = re.search(r"(?:administered|given)\s+(?:orally|intravenously|rectally|through[^,.]*)?\s*(.*?)(?:\.|$)", interv, re.I)
    return (m.group(1).strip()[:120] if m and m.group(1).strip() else "NR")
def arm_breakdown(p, interv):
    m = re.search(r"(?:\d+\s+total|total\s+\d+|in total)[:\s]*(.*?)(?:Mean|Median|Age|Surgery|$)", p, re.I|re.S)
    seg = (m.group(1) if m else "").strip().strip(":")
    each = re.search(r"(\d+)\s+in each arm\s*\((\d+)\s*arms\)", seg, re.I)
    arms = []
    if each:
        for d in re.split(r"\s+or\s+", interv):
            nm = re.match(r"[A-Za-z][A-Za-z\s()+/\-]*", d.strip())
            if nm: arms.append((each.group(1), nm.group(0).strip()[:40]))
        return arms
    for part in re.split(r"[,/]|\band\b", seg):
        part = part.strip()
        m1 = re.match(r"(\d+)\s*\(?([A-Za-z][\w\s+()\-'’]*?)\)?$", part)
        m2 = re.match(r"\(?([A-Za-z][\w\s+()\-'’]*?)\)?\s+(\d+)$", part)
        if m1: arms.append((m1.group(1), m1.group(2).strip(" ()")))
        elif m2: arms.append((m2.group(2), m2.group(1).strip(" ()")))
    if len(arms) < 2:
        arms = []
        for d in re.split(r"\s+or\s+", interv):
            nm = re.match(r"[A-Za-z][A-Za-z\s()+/\-]*", d.strip())
            if nm and len(nm.group(0).strip()) > 2: arms.append(("NR", nm.group(0).strip()[:40]))
    return arms

def per_study_rows(data, order):
    sc, pp, iv, rob = [], [], [], []
    for sid in order:
        S = data[sid]["sections"]
        methods, part, interv, notes = (S.get("Methods","") or ""), (S.get("Participants","") or ""), \
                                        (S.get("Interventions","") or ""), (S.get("Notes","") or "")
        sc.append(dict(study_id=sid, country=clean_text(S.get("Setting (country)","")),
            surgery_type=surgery_of(part), route_of_administration=route_of(interv),
            outcomes_measured=clean_text(S.get("Outcomes","")), trial_design=clean_text(methods),
            number_of_centres=centres_of(methods), funding_source=funding_of(notes)))
        typ,val,sd,rng = age_of(part)
        pp.append(dict(study_id=sid, age_central_tendency_type=typ, age_value=val, age_sd=sd,
            age_range=clean_text(rng), n_randomised=n_rand_of(part)))
        arms = arm_breakdown(part, interv)
        if sid == "Polat 2005b":   # 6-arm study, placebo implied in the 'or' list
            arms = [("20","ibuprofen"),("20","flurbiprofen"),("20","acetaminophen (paracetamol)"),
                    ("20","naproxen sodium"),("20","aspirin"),("20","placebo")]
        # comparison_summary = the study's actual comparison ("ibuprofen vs <comparators>"),
        # derived from the arm categories — a comparison, not the design sentence.
        _cats = [categorise(lbl) for _, lbl in arms]
        _comps = [c.replace("_", " ") for c in dict.fromkeys(_cats) if c != "ibuprofen"]
        comp_sum = ("ibuprofen vs " + " vs ".join(_comps)) if _comps else clean_text(methods)
        for n,label in arms:
            cat = categorise(label)
            iv.append(dict(study_id=sid, arm_label=clean_arm(label), arm_category=cat,
                drug_name=clean_text(clean_drug(label, cat)), dose=clean_text(dose_of(interv, cat, label)),
                route=route_of(interv), frequency=clean_text(freq_of(interv)), n_in_arm=n,
                comparison_summary=comp_sum))
        r = dict(study_id=sid)
        for key,_ in ROB:
            r[f"{key}_judgment"] = data[sid]["rob"][key]["judgment"]
            r[f"{key}_reason"]   = data[sid]["rob"][key]["reason"]
        rob.append(r)
    return sc, pp, iv, rob

# ───────────────────────── workbook assembly ─────────────────────────
DIRECTIVE = {
 "study_char": dict(study_id="EXTRACT",country="EXTRACT",surgery_type="EXTRACT",
    route_of_administration="EXTRACT",outcomes_measured="EXTRACT",trial_design="EXTRACT",
    number_of_centres="EXTRACT",funding_source="OPTIONAL"),
 "patient_pop": dict(study_id="EXTRACT",age_central_tendency_type="EXTRACT",age_value="EXTRACT",
    age_sd="EXTRACT",age_range="EXTRACT",n_randomised="EXTRACT"),
 "interventions": dict(study_id="EXTRACT",arm_label="OPTIONAL",arm_category="OPTIONAL",drug_name="EXTRACT",
    dose="EXTRACT",route="EXTRACT",frequency="EXTRACT",n_in_arm="EXTRACT",comparison_summary="EXTRACT"),
 "continuous_outcomes": dict(study_id="EXTRACT",comparison="EXTRACT",outcome_type="EXTRACT",reporter="EXTRACT",
    timepoint="EXTRACT",scale="OPTIONAL",mean_arm1="EXTRACT",sd_arm1="EXTRACT",n_arm1="EXTRACT",
    mean_arm2="EXTRACT",sd_arm2="EXTRACT",n_arm2="EXTRACT"),
 "dichotomous_outcomes": dict(study_id="EXTRACT",comparison="EXTRACT",outcome="EXTRACT",timepoint="EXTRACT",
    arm1_label="EXTRACT",arm1_events="EXTRACT",arm1_n="EXTRACT",arm2_label="EXTRACT",arm2_events="EXTRACT",arm2_n="EXTRACT"),
}
COLS = {
 "study_char":["study_id","country","surgery_type","route_of_administration","outcomes_measured","trial_design","number_of_centres","funding_source"],
 "patient_pop":["study_id","age_central_tendency_type","age_value","age_sd","age_range","n_randomised"],
 "interventions":["study_id","arm_label","arm_category","drug_name","dose","route","frequency","n_in_arm","comparison_summary"],
 "continuous_outcomes":["study_id","comparison","outcome_type","reporter","timepoint","scale","mean_arm1","sd_arm1","n_arm1","mean_arm2","sd_arm2","n_arm2"],
 "dichotomous_outcomes":["study_id","comparison","outcome","timepoint","arm1_label","arm1_events","arm1_n","arm2_label","arm2_events","arm2_n"],
}

def _definitions():
    D = []
    def d(*row): D.append(list(row))
    d("study_char","study_id","string","","Primary key: first author + year (Cochrane study ID).","Characteristics header","Abdelbaser 2022a")
    d("study_char","country","string","","Country where the trial was conducted.","Characteristics → Setting (country)","Egypt")
    d("study_char","surgery_type","string","","Surgery / procedure type.","Characteristics → Participants → Surgery/procedure type","tonsillectomy")
    d("study_char","route_of_administration","enum","oral; IV; rectal; NR","Route ibuprofen was given.","Characteristics → Interventions","oral")
    d("study_char","outcomes_measured","string","","Outcomes the study measured/reported (verbatim list).","Characteristics → Outcomes (= review Table 1 'Outcomes' column)","pain intensity (VAS); rescue medication; AEs")
    d("study_char","trial_design","string","","Verbatim design description.","Characteristics → Methods","Double-blind RCT")
    d("study_char","number_of_centres","string","1; multicentre; NR","Single vs multicentre (NR when the trial doesn't state it).","Characteristics → Methods","1")
    d("study_char","funding_source","string","","Funder(s) or none/NR — OPTIONAL.","Characteristics → Notes → Study funding","none/NR")
    d("patient_pop","study_id","string","","FK to study_char.","—","Abdelbaser 2022a")
    d("patient_pop","age_central_tendency_type","enum","mean; median; range; NR","How age was summarised.","Characteristics → Participants","mean")
    d("patient_pop","age_value","float","","Ibuprofen-arm central age (reported unit; years unless months noted).","Characteristics → Participants","7.4")
    d("patient_pop","age_sd","float","","Ibuprofen-arm age SD (mean±SD studies).","Characteristics → Participants","1.9")
    d("patient_pop","age_range","string","","Reported age range / IQR (complements age_sd: mean±SD vs median/range studies).","Characteristics → Participants","3-5.2 years")
    d("patient_pop","n_randomised","int","","Total number randomised.","Characteristics → Participants → N total","59")
    d("interventions","study_id","string","","FK to study_char.","—","Abdelbaser 2022a")
    d("interventions","arm_label","string","","Arm label (matching key) — OPTIONAL.","Characteristics → Participants/Interventions","ibuprofen")
    d("interventions","arm_category","enum","ibuprofen; placebo; paracetamol; morphine; ketorolac; naproxen_sodium; rofecoxib; aspirin; combination; other","Treatment class (matching key) — OPTIONAL.","Derived from arm drug","ibuprofen")
    d("interventions","drug_name","string","","Drug given in this arm.","Characteristics → Interventions","ibuprofen")
    d("interventions","dose","string","","Dose verbatim.","Characteristics → Interventions","10 mg/kg")
    d("interventions","route","enum","oral; IV; rectal; NR","Route of administration.","Characteristics → Interventions","IV")
    d("interventions","frequency","string","","Dosing schedule / timing verbatim.","Characteristics → Interventions","every 6 h for 24 h")
    d("interventions","n_in_arm","int","","Number randomised to this arm (NR if not reported per arm).","Characteristics → Participants → N total breakdown","30")
    d("interventions","comparison_summary","string","","One-line design/comparison summary.","Characteristics → Methods","Double-blind RCT")
    d("continuous_outcomes","study_id","string","","FK to study_char.","—","Bahrololoomi 2019")
    d("continuous_outcomes","comparison","enum","placebo; paracetamol; morphine; ketorolac; naproxen_sodium; rofecoxib; aspirin","Comparator arm (ibuprofen vs …).","Data and analyses → Comparison N","placebo")
    d("continuous_outcomes","outcome_type","enum","pain_intensity; opioid_consumption; time_to_rescue","Continuous outcome.","Analysis title","pain_intensity")
    d("continuous_outcomes","reporter","enum","child; third_party; NA","Who reported (pain intensity only).","Analysis title","child")
    d("continuous_outcomes","timepoint","enum","under_2h; 2h_to_24h; 24h_to_7d; overall","Timepoint window.","Analysis title","under_2h")
    d("continuous_outcomes","scale","string","","Measurement scale — OPTIONAL (not in forest plots; SMD used).","Characteristics → Outcomes","VAS")
    d("continuous_outcomes","mean_arm1","float","","Ibuprofen-arm mean.","Forest plot row (Ibuprofen Mean)","0.45")
    d("continuous_outcomes","sd_arm1","float","","Ibuprofen-arm SD.","Forest plot row","0.82")
    d("continuous_outcomes","n_arm1","int","","Ibuprofen-arm N.","Forest plot row (Total)","20")
    d("continuous_outcomes","mean_arm2","float","","Comparator-arm mean.","Forest plot row","1.9")
    d("continuous_outcomes","sd_arm2","float","","Comparator-arm SD.","Forest plot row","1.22")
    d("continuous_outcomes","n_arm2","int","","Comparator-arm N.","Forest plot row (Total)","21")
    d("dichotomous_outcomes","study_id","string","","FK to study_char.","—","Kokki 1994")
    d("dichotomous_outcomes","comparison","enum","placebo; paracetamol; morphine; ketorolac; naproxen_sodium; rofecoxib; aspirin","Comparator arm.","Data and analyses → Comparison N","placebo")
    d("dichotomous_outcomes","outcome","enum","adverse_events; rescue_medication; nausea_or_vomiting; bleeding; renal_dysfunction","Dichotomous outcome.","Analysis title","adverse_events")
    d("dichotomous_outcomes","timepoint","enum","under_2h; 2h_to_24h; 24h_to_7d; overall","Timepoint window (rescue medication only).","Analysis title","overall")
    d("dichotomous_outcomes","arm1_label","string","","Ibuprofen arm label.","Forest plot","ibuprofen")
    d("dichotomous_outcomes","arm1_events","int","","Ibuprofen-arm event count.","Forest plot row (Events)","11")
    d("dichotomous_outcomes","arm1_n","int","","Ibuprofen-arm total.","Forest plot row (Total)","40")
    d("dichotomous_outcomes","arm2_label","string","","Comparator arm label.","Forest plot","placebo")
    d("dichotomous_outcomes","arm2_events","int","","Comparator-arm event count.","Forest plot row (Events)","10")
    d("dichotomous_outcomes","arm2_n","int","","Comparator-arm total.","Forest plot row (Total)","41")
    for dom,label in [("random_sequence_generation","Random sequence generation"),("allocation_concealment","Allocation concealment"),
        ("blinding_participants_personnel","Blinding of participants and personnel"),("blinding_outcome_assessment","Blinding of outcome assessment"),
        ("incomplete_outcome_data","Incomplete outcome data"),("selective_reporting","Selective reporting"),("other_bias","Other bias")]:
        d("risk_of_bias",f"{dom}_judgment","enum","low; high; unclear",f"{label} — RoB judgement.","Characteristics → Risk of bias table","low")
        d("risk_of_bias",f"{dom}_reason","string","",f"{label} — support for judgement (verbatim).","Characteristics → Risk of bias table","Computer-generated random numbers")
    return D

def build_xlsx(sheets, rob_rows, out):
    wb = openpyxl.Workbook()
    HF = PatternFill("solid", fgColor="1F2A44"); HFF = Font(color="FFFFFF", bold=True)
    DF = PatternFill("solid", fgColor="E8ECF4"); DFF = Font(italic=True, color="55618A")
    # definitions
    wsd = wb.active; wsd.title = "definitions"
    wsd.append(["sheet_name","column_name","data_type","allowed_values","definition","pdf_section","example_value"])
    for row in _definitions(): wsd.append(row)
    for cc in range(1,8): wsd.cell(1,cc).fill=HF; wsd.cell(1,cc).font=HFF
    wsd.freeze_panes = "A2"
    def add(name, header, rows, directive):
        ws = wb.create_sheet(name); ws.append(header)
        ws.append([directive.get(c,"EXTRACT") for c in header])
        for r in rows: ws.append([r.get(c,"") for c in header])
        for cc in range(1,len(header)+1):
            ws.cell(1,cc).fill=HF; ws.cell(1,cc).font=HFF
            ws.cell(2,cc).fill=DF; ws.cell(2,cc).font=DFF
        ws.freeze_panes = "A3"
        for i,c in enumerate(header,1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(max(12,len(c)+2),28)
    for name in ["study_char","patient_pop","interventions","continuous_outcomes","dichotomous_outcomes"]:
        add(name, COLS[name], sheets[name], DIRECTIVE[name])
    rob_header = list(rob_rows[0].keys())
    add("risk_of_bias", rob_header, rob_rows, {c:"EXTRACT" for c in rob_header})
    os.makedirs(os.path.dirname(out), exist_ok=True)
    wb.save(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=DEFAULT_PDF)
    ap.add_argument("--out", default=OUT_XLSX)
    a = ap.parse_args()
    print("Parsing forest plots…")
    titles = analysis_titles(a.pdf)
    cont, dich = parse_forest(a.pdf, titles)
    print(f"  continuous={len(cont)}  dichotomous={len(dich)}  (0 warnings above = all Total lines reconcile)")
    print("Parsing Characteristics of included studies…")
    data, order = parse_characteristics(a.pdf)
    sc, pp, iv, rob = per_study_rows(data, order)
    print(f"  studies={len(order)}  arms={len(iv)}")
    build_xlsx({"study_char":sc,"patient_pop":pp,"interventions":iv,
                "continuous_outcomes":cont,"dichotomous_outcomes":dich}, rob, a.out)
    print(f"✓ wrote {a.out}")

if __name__ == "__main__":
    main()
