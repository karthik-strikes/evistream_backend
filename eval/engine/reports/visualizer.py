"""
Generate publication-quality plots:
- Per-form bar chart: kappa + F1 per field
- Cross-form heatmap: fields × forms, color = kappa
- Structural match summary
"""

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns

_DEFAULT_FIGURES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "outputs", "figures")


def _figures_dir() -> str:
    return os.environ.get("EVAL_FIGURES_DIR", _DEFAULT_FIGURES_DIR)
sns.set_theme(style="whitegrid", palette="muted", font_scale=1.0)


def _save(fig, name: str):
    figures_dir = _figures_dir()
    os.makedirs(figures_dir, exist_ok=True)
    path = os.path.join(figures_dir, f"{name}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → Figure saved: {path}")


def plot_form_bar(stats_df: pd.DataFrame, form_name: str):
    """Bar chart of kappa + F1 per field for one form."""
    df = stats_df[stats_df["kappa"].notna() | stats_df["macro_f1"].notna()].copy()
    if df.empty:
        return

    fields = df["field"].tolist()
    kappas = df["kappa"].fillna(0).tolist()
    f1s    = df["macro_f1"].fillna(0).tolist()

    x = np.arange(len(fields))
    width = 0.35

    fig, ax = plt.subplots(figsize=(max(8, len(fields) * 0.8), 5))
    bars1 = ax.bar(x - width/2, kappas, width, label="Cohen's κ", color="#2196F3", alpha=0.85)
    bars2 = ax.bar(x + width/2, f1s,    width, label="Macro F1",  color="#FF9800", alpha=0.85)

    ax.set_xlabel("Field")
    ax.set_ylabel("Score")
    ax.set_title(f"{form_name.replace('_',' ').title()} — Agreement per Field")
    ax.set_xticks(x)
    ax.set_xticklabels(fields, rotation=40, ha="right", fontsize=8)
    ax.set_ylim(0, 1.1)
    ax.axhline(0.8, color="green",  linestyle="--", linewidth=0.8, alpha=0.7, label="Near-perfect (0.8)")
    ax.axhline(0.6, color="orange", linestyle="--", linewidth=0.8, alpha=0.7, label="Substantial (0.6)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    _save(fig, f"{form_name}_bar")


def plot_cross_form_heatmap(all_stats: dict[str, pd.DataFrame]):
    """
    Heatmap: rows = fields (labeled by form.field), cols = form, value = kappa.
    """
    records = []
    for form, df in all_stats.items():
        for _, row in df.iterrows():
            if row.get("kappa") is not None:
                records.append({"form": form, "field": row["field"], "kappa": row["kappa"]})
    if not records:
        return

    pivot = pd.DataFrame(records).pivot(index="field", columns="form", values="kappa")

    fig, ax = plt.subplots(figsize=(max(6, len(pivot.columns) * 1.5), max(6, len(pivot) * 0.4)))
    sns.heatmap(pivot, annot=True, fmt=".2f", cmap="RdYlGn",
                vmin=0, vmax=1, linewidths=0.5, ax=ax,
                cbar_kws={"label": "Cohen's κ"})
    ax.set_title("Agreement Heatmap (Cohen's κ) — All Forms")
    ax.set_xlabel("Form")
    ax.set_ylabel("Field")
    fig.tight_layout()
    _save(fig, "cross_form_heatmap")


def plot_structural_match_summary(structural: dict[str, dict]):
    """
    Bar chart: % studies matched per form, with test-arm match rate for index_test.
    structural = {form_name: {"pct_studies_matched": 80.0, "pct_arms_matched": ...}}
    """
    forms  = list(structural.keys())
    study_pcts = [structural[f].get("pct_studies_matched", 0) for f in forms]
    arm_pcts   = [structural[f].get("pct_arms_matched", None) for f in forms]

    fig, ax = plt.subplots(figsize=(max(6, len(forms) * 1.2), 4))
    x = np.arange(len(forms))
    ax.bar(x, study_pcts, color="#4CAF50", alpha=0.85, label="Studies matched (%)")
    for i, v in enumerate(arm_pcts):
        if v is not None:
            ax.bar(x[i] + 0.35, v, 0.35, color="#9C27B0", alpha=0.85, label="Test arms matched (%)")

    ax.set_xticks(x)
    ax.set_xticklabels([f.replace("_", " ").title() for f in forms], rotation=20, ha="right")
    ax.set_ylim(0, 110)
    ax.set_ylabel("% Matched")
    ax.set_title("Structural Match Rates per Form")
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), fontsize=8)
    fig.tight_layout()
    _save(fig, "structural_match_summary")
