from typing import Dict, List

import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

# --- Set a modern, clean style for all plots ---
sns.set_style("whitegrid")

# ===================================================================
# PLOT 1: OVERALL RECORD-LEVEL METRICS (BAR CHART)
# (This function is unchanged)
# ===================================================================


def plot_overall_metrics(ax, precision, recall, f1):
    """Plots a clean bar chart for P, R, and F1 onto a specific ax."""
    metrics = {'Precision': precision, 'Recall': recall, 'F1 Score': f1}
    names = list(metrics.keys())
    values = list(metrics.values())

    # Create the bar plot
    colors = ['#4c72b0', '#55a868', '#c44e52']  # Blue, Green, Red
    bars = ax.bar(names, values, color=colors, width=0.6)

    # --- Style the plot ---
    ax.set_title('Overall Record-Level Metrics',
                 fontsize=16, fontweight='bold', pad=15)
    ax.set_ylabel('Score', fontsize=12)
    ax.set_ylim(0, 1.1)
    ax.grid(axis='x')  # Remove vertical grid lines

    # Add labels on top of bars
    for i, v in enumerate(values):
        ax.text(i, v + 0.03, f'{v:.3f}',
                ha='center', va='bottom',
                fontsize=11, fontweight='bold')

    sns.despine(ax=ax)

# ===================================================================
# PLOT 2: OVERALL ERROR CONTRIBUTION (DONUT CHART)
# (This function is unchanged)
# ===================================================================


def plot_error_contribution(ax, aggregated_field_counts):
    """Plots a stylish donut chart of error types onto a specific ax."""
    df = pd.DataFrame.from_dict(aggregated_field_counts, orient='index')

    # Sum all errors
    total_missing = df['missing'].sum()
    total_incorrect = df['incorrect'].sum()
    total_extra = df['extra'].sum()
    total_errors = total_missing + total_incorrect + total_extra

    if total_errors == 0:
        ax.text(0.5, 0.5, "No Errors Found!",
                ha='center', va='center', fontsize=16)
        return

    # Data for the pie chart
    error_data = [total_incorrect, total_missing, total_extra]
    labels = ['Incorrect\n(Not Matched)', 'Missing',
              'Extra\n(May be Hallucinated)']
    colors = ['#d62728', '#ff7f0e', '#9467bd']  # Red, Orange, Purple

    # Explode the smallest slice for visibility
    explode = [0, 0, 0]
    if min(error_data) / total_errors < 0.1:
        explode[np.argmin(error_data)] = 0.05

    # Create the pie chart
    patches, texts, autotexts = ax.pie(
        error_data,
        labels=labels,
        autopct=lambda p: f'{p:.1f}%\n({p*total_errors/100.0:.0f})',
        startangle=90,
        pctdistance=0.82,
        colors=colors,
        explode=explode,
        textprops={'fontsize': 11}
    )

    # Style the autopct labels
    for t in autotexts:
        t.set_color('white')
        t.set_fontweight('bold')

    # Draw a circle in the center to make it a donut chart
    centre_circle = plt.Circle((0, 0), 0.65, fc='white')
    ax.add_artist(centre_circle)

    ax.set_title('Overall Error Contribution',
                 fontsize=16, fontweight='bold', pad=15)
    # Equal aspect ratio ensures that pie is drawn as a circle.
    ax.axis('equal')

# ===================================================================
# PLOT 3 (FIXED): PERFORMANCE BREAKDOWN VS GT (BY COUNT)
# (This function is unchanged from the last working version)
# ===================================================================


def plot_performance_breakdown_gt(
    ax,
    aggregated_field_counts: Dict[str, Dict[str, float]],
    semantic_fields: List[str],
    exact_fields: List[str],
):
    """Plots stacked 100% bars for Exact vs. Semantic performance."""
    df = pd.DataFrame.from_dict(aggregated_field_counts, orient='index')

    def get_field_type(field_name):
        if field_name in semantic_fields:
            return 'Semantic'
        if field_name in exact_fields:
            return 'Exact'
        return 'Other'  # Should not happen if lists are correct

    df['field_type'] = df.index.map(get_field_type)
    grouped = df.groupby('field_type').sum()

    # We only care about GT-based errors here
    gt_data = grouped[['matched', 'missing', 'incorrect']].copy()
    gt_data['total_gt'] = grouped['gt_count']

    # Calculate percentages relative to Ground Truth total
    gt_perc = gt_data.drop('total_gt', axis=1).div(gt_data['total_gt'], axis=0)

    # --- Create the plot ---
    # Define the color map
    colors = {
        'matched': '#2ca02c',  # Green
        'incorrect': '#d62728',  # Red
        'missing': '#ff7f0e'   # Orange
    }

    df_stack = gt_perc[['matched', 'incorrect', 'missing']]

    df_stack.plot(
        kind='barh',
        stacked=True,
        color=[colors[col] for col in df_stack.columns],
        ax=ax,
        width=0.7
    )

    # --- Style the plot ---
    ax.set_title('Performance Breakdown vs. GT',
                 fontsize=16, fontweight='bold', pad=15)
    ax.set_xlabel('Percentage of Ground Truth Fields (%)', fontsize=12)
    ax.set_ylabel('Field Type', fontsize=12)

    # Format X-axis as percentages
    ax.set_xlim(0, 1)
    xticks = ax.get_xticks()
    ax.set_xticks(xticks)
    ax.set_xticklabels([f'{int(x*100)}%' for x in xticks])

    # Move legend to the top
    ax.legend(
        loc='upper center',
        bbox_to_anchor=(0.5, 1.12),
        ncol=3,
        frameon=False,
        fontsize=12
    )

    # Remove all spines
    sns.despine(ax=ax, left=True, bottom=True, top=True, right=True)

    # Add annotations (text labels) inside the bars
    for c in ax.containers:

        # --- THIS IS THE FIX ---
        # The original code had 'w' instead of 'w.get_width()'
        # 'w' is the Rectangle object, 'w.get_width()' is the float value
        labels = [f'{w.get_width():.1%}' if w.get_width()
                  > 0.03 else '' for w in c]
        # ------------------------

        ax.bar_label(
            c,
            label_type='center',
            labels=labels,
            color='white',
            fontweight='bold',
            fontsize=11
        )
# ===================================================================
# PLOT 4: THE REWORKED HALLUCINATION REPORT (*** NOW WITH FIELD TYPES ***)
# ===================================================================


def plot_hallucination_analysis(
    ax,
    aggregated_field_counts: Dict[str, Dict[str, float]],
    semantic_fields: List[str],
    exact_fields: List[str],
    top_n: int = 10
):
    """
    Shows the FULL breakdown for the top N 'hallucinating' fields.
    *** Y-axis labels are now color-coded by field type. ***
    """
    df = pd.DataFrame.from_dict(aggregated_field_counts, orient='index')

    # --- NEW: Add field type ---
    def get_field_type(field_name):
        if field_name in semantic_fields:
            return 'Semantic'
        if field_name in exact_fields:
            return 'Exact'
        return 'Other'
    df['field_type'] = df.index.map(get_field_type)
    # --- End new ---

    # Find the top N fields by 'extra'
    top_extra_fields_df = df.sort_values('extra', ascending=False).head(top_n)

    # Select just those fields and the columns we care about
    df_plot = top_extra_fields_df[[
        'extra', 'matched', 'incorrect', 'missing', 'field_type']]

    # Sort by 'extra' for the plot (so bars are ascending)
    df_plot = df_plot.sort_values('extra', ascending=True)

    # --- Create the plot ---
    colors = {
        'extra': '#9467bd',     # Purple
        'matched': '#2ca02c',  # Green
        'incorrect': '#d62728',  # Red
        'missing': '#ff7f0e'   # Orange
    }

    df_plot[colors.keys()].plot(
        kind='barh',
        stacked=True,
        color=[colors[col] for col in colors.keys()],
        ax=ax,
        width=0.75
    )

    # --- Style the plot ---
    ax.set_title('Extra Fields Analysis', fontsize=16,
                 fontweight='bold', pad=15)
    ax.set_xlabel('Total Count of Items (Found + Hallucinated)', fontsize=12)
    ax.set_ylabel('Field Name', fontsize=12)

    # --- NEW: Create legend handles ---
    bar_patches = [mpatches.Patch(color=c, label=l) for l, c in colors.items()]

    # Add a spacer
    spacer = mpatches.Patch(color='white', label='')

    # Add field type labels
    type_colors = {'Semantic': '#007acc', 'Exact': '#333333'}
    type_patches = [
        mpatches.Patch(color=type_colors['Semantic'],
                       label='Semantic Field (Label)'),
        mpatches.Patch(color=type_colors['Exact'], label='Exact Field (Label)')
    ]

    ax.legend(
        handles=bar_patches + [spacer] + type_patches,
        loc='upper center',
        bbox_to_anchor=(0.5, 1.22),  # Adjusted position
        ncol=3,
        frameon=False,
        fontsize=12
    )

    # --- NEW: Color the Y-tick labels ---
    field_type_map = df_plot['field_type'].map(type_colors)
    for label, color in zip(ax.get_yticklabels(), field_type_map):
        label.set_color(color)
        label.set_fontweight('bold')
    # --- End new ---

    sns.despine(ax=ax, left=True, bottom=True, top=True, right=True)

    # Add annotations
    for c in ax.containers:
        counts = [int(v.get_width()) for v in c]
        labels = [str(c) if c > 0 else '' for c in counts]

        ax.bar_label(
            c,
            label_type='center',
            labels=labels,
            color='white',
            fontweight='bold',
            fontsize=10
        )

# ===================================================================
# PLOT 5 (FIXED): THE "ACTION PLAN" (*** NOW WITH FIELD TYPES ***)
# ===================================================================


def plot_accuracy_breakdown_list(
    ax,
    aggregated_field_counts: Dict[str, Dict[str, float]],
    semantic_fields: List[str],
    exact_fields: List[str],
    top_n: int = 20
):
    """
    Plots a stacked bar chart for the worst-performing fields,
    SORTED BY TOTAL ERROR COUNT (incorrect + missing).
    *** Y-axis labels are now color-coded by field type. ***
    """
    df = pd.DataFrame.from_dict(aggregated_field_counts, orient='index')

    # --- 1. Calculate new metrics ---
    df_gt = df[df['gt_count'] > 0].copy()  # Only fields that *should* exist

    # --- NEW: Add field type ---
    def get_field_type(field_name):
        if field_name in semantic_fields:
            return 'Semantic'
        if field_name in exact_fields:
            return 'Exact'
        return 'Other'
    df_gt['field_type'] = df_gt.index.map(get_field_type)
    # --- End new ---

    # We sort by the *number* of errors
    df_gt['Total Errors'] = df_gt['incorrect'] + df_gt['missing']
    df_gt_sorted = df_gt.sort_values(
        'Total Errors', ascending=False).head(top_n)

    # Select only the count columns for stacking
    df_stack = df_gt_sorted[['matched', 'incorrect', 'missing']]

    # --- 2. Create the plot ---
    colors = {'matched': '#2ca02c',
              'incorrect': '#d62728', 'missing': '#ff7f0e'}

    df_stack.plot(
        kind='barh',
        stacked=True,
        figsize=(16, 12),  # Set a fixed large size
        color=[colors[col] for col in colors.keys()],
        ax=ax,
        width=0.8
    )

    # --- 3. Style the plot ---
    ax.set_title(f'Top {len(df_stack)} Fields by Error Count',
                 fontsize=18, fontweight='bold', pad=25)

    ax.set_xlabel('Total Count of Ground Truth Items',
                  fontsize=14, labelpad=10)
    ax.set_ylabel('Field Name', fontsize=14, labelpad=10)

    # Invert Y-axis to show worst at the top
    ax.invert_yaxis()

    # --- NEW: Create legend handles ---
    bar_patches = [mpatches.Patch(color=c, label=l) for l, c in colors.items()]

    # Add a spacer
    spacer = mpatches.Patch(color='white', label='')

    # Add field type labels
    type_colors = {'Semantic': '#007acc', 'Exact': '#333333'}
    type_patches = [
        mpatches.Patch(color=type_colors['Semantic'],
                       label='Semantic Field (Label)'),
        mpatches.Patch(color=type_colors['Exact'], label='Exact Field (Label)')
    ]

    ax.legend(
        handles=bar_patches + [spacer] + type_patches,
        loc='upper center',
        bbox_to_anchor=(0.5, 1.05),  # Adjusted position
        ncol=5,  # Now 5 items
        frameon=False,
        fontsize=14
    )
    # --- End new ---

    # --- NEW: Color the Y-tick labels ---
    # The order of labels is in df_stack.index (or df_gt_sorted.index)
    field_type_map = df_gt_sorted['field_type'].map(type_colors)
    for label, color in zip(ax.get_yticklabels(), field_type_map):
        label.set_color(color)
        label.set_fontweight('bold')
    # --- End new ---

    # Remove all spines
    sns.despine(ax=ax, left=True, bottom=True, top=True, right=True)

    # Add annotations (text labels) inside the bars
    for c in ax.containers:
        counts = [int(v.get_width()) for v in c]
        labels = [str(c) if c > 0 else '' for c in counts]

        ax.bar_label(
            c,
            label_type='center',
            labels=labels,
            color='white',
            fontweight='bold',
            fontsize=11
        )
# ===================================================================
# === MAIN DASHBOARD FUNCTION ===
# (This function is unchanged)
# ===================================================================


def create_performance_dashboards(
    aggregated_field_counts: Dict[str, Dict[str, float]],
    avg_precision: float,
    avg_recall: float,
    avg_f1: float,
    semantic_fields: List[str],
    exact_fields: List[str],
    save_to_file: bool = False,
    output_dir: str = "./dashboards",
    schema_name: str = "default"
):
    """
    Generates two dashboard figures:
    1. A 2x2 "Executive Summary" grid.
    2. A large "Action Plan" plot showing the worst-performing fields.

    Args:
        aggregated_field_counts: Dictionary of field-level counts
        avg_precision: Average precision score
        avg_recall: Average recall score
        avg_f1: Average F1 score
        save_to_file: If True, save to PNG files; if False, display interactively
        output_dir: Directory to save dashboard images (default: ./dashboards)

    Returns:
        tuple or None: If save_to_file=True, returns (summary_path, action_plan_path); 
                       otherwise returns None (displays interactively)
    """

    # --- Figure 1: The 2x2 Executive Summary ---

    fig1, ax = plt.subplots(2, 2, figsize=(22, 18))
    fig1.suptitle('Extractor Performance Dashboard: Executive Summary',
                  fontsize=24, fontweight='bold', y=1.02)

    # Top-Left: Overall Record-Level Metrics
    plot_overall_metrics(ax[0, 0], avg_precision, avg_recall, avg_f1)

    # Top-Right: Overall Error Contribution (Donut)
    plot_error_contribution(ax[0, 1], aggregated_field_counts)

    # Bottom-Left: Performance Breakdown vs. GT (by Count)
    plot_performance_breakdown_gt(
        ax[1, 0],
        aggregated_field_counts,
        semantic_fields,
        exact_fields
    )

    # Bottom-Right: The REWORKED Hallucination Analysis
    plot_hallucination_analysis(
        ax[1, 1],
        aggregated_field_counts,
        semantic_fields,
        exact_fields,
        top_n=10
    )

    fig1.tight_layout(pad=4.0)

    # --- Figure 2: The Action Plan (Worst-List) ---

    fig2, ax2 = plt.subplots(1, 1, figsize=(16, 12))  # 1x1 grid, but large
    fig2.suptitle('Extractor Performance Dashboard: Action Plan',
                  fontsize=24, fontweight='bold', y=1.02)

    # Plot the "Super-charged" Worst List
    plot_accuracy_breakdown_list(
        ax2,
        aggregated_field_counts,
        semantic_fields,
        exact_fields,
        top_n=30
    )

    fig2.tight_layout(pad=3.0)

    if save_to_file:
        from pathlib import Path
        from datetime import datetime

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d")

        summary_path = output_path / f"{schema_name}_summary_{timestamp}.png"
        fig1.savefig(summary_path, dpi=150, bbox_inches='tight')
        print(f"✓ Saved executive summary dashboard: {summary_path}")

        action_plan_path = output_path / f"{schema_name}_actions_{timestamp}.png"
        fig2.savefig(action_plan_path, dpi=150, bbox_inches='tight')
        print(f"✓ Saved action plan dashboard: {action_plan_path}")

        plt.close(fig1)
        plt.close(fig2)

        return str(summary_path), str(action_plan_path)
    else:
        # Display interactively (works in notebooks and GUI backends)
        plt.show()
        return None
