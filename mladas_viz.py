"""
mladas_viz — consistent, accessible charts for the MLADAS demo notebooks (matplotlib + networkx).

Design rules (from the course's data-viz guide):
  * categorical hues in a fixed, CVD-validated order — color follows the entity, never its rank
  * thin marks, 2px lines, hairline recessive grid, one y-axis per chart (never dual-axis)
  * a legend whenever there are >= 2 series; direct labels only where the story is
  * text is ink-colored (never the series color)

Entity colors (the same everywhere in the course):
  roles   orchestrator=blue · specialist=aqua · remote partner (A2A)=orange · deterministic code=yellow ·
          multi-agent node=violet · memory=magenta · single-agent baseline=green
  models  Nova 2 Lite=blue · Nova Micro=violet · Nova Lite=aqua · Nova Pro=green   (model charts never mix with role charts)
  tokens  input=blue · output=orange
  M02     context categories (slide 6): system=violet · tool definitions=yellow · tool results=orange ·
          memory/history=magenta · retrieved=aqua · user=green
          cache: uncached input=blue · cache write=yellow · cache read=aqua · output=orange
          strategies (slide 15): format=yellow · write=magenta · select=aqua · compress=violet · isolate=green
          conversation managers: none (Null)=grey · sliding window=blue · summarizing=violet · context_manager auto=aqua
  M03     ROLE_COLORS["attacker"]=red · DECISION_COLORS (status steps: allowed=good · denied=critical ·
          rejected at the door=serious · would deny/LOG_ONLY=warning · error=grey) · ATTACK_COLORS (succeeded /
          partial / stopped) · CONTROL_COLORS: none=grey · identity=blue · policy=violet · guardrail=aqua ·
          network=yellow · audit=magenta.  grid(rows, cols, cell_text, cell_keys) draws a categorical matrix.
  ordered categories (days, sessions, stages) use ordinal(n): one blue hue, light -> dark
Projector rule: no text below 9 pt.

Gotcha: matplotlib treats a pair of '$' in any text as math mode — escape literal dollars as '\\$' in titles/labels.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch  # noqa: F401  (handy for notebook-specific drawings)

# ----------------------------------------------------------------------------------------------
# Palette (validated reference palette, light surface)
# ----------------------------------------------------------------------------------------------
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = PALETTE
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
NEUTRAL = "#c3c2b7"      # for "not used / not executed" marks

# Fixed identity colors for the course's recurring entities (color follows the entity everywhere).
ROLE_COLORS = {
    "orchestrator": BLUE, "specialist": AQUA, "remote": ORANGE, "function": YELLOW,
    "multiagent": VIOLET, "memory": MAGENTA, "monolith": GREEN,
}
MODEL_COLORS = {
    "us.amazon.nova-2-lite-v1:0": BLUE, "us.amazon.nova-micro-v1:0": VIOLET,
    "us.amazon.nova-lite-v1:0": AQUA, "us.amazon.nova-pro-v1:0": GREEN,
    "amazon.nova-micro-v1:0": VIOLET,          # in-Region Micro (deterministic caching demos) = the same model
}
MODEL_LABELS = {
    "us.amazon.nova-2-lite-v1:0": "Nova 2 Lite", "us.amazon.nova-micro-v1:0": "Nova Micro",
    "us.amazon.nova-lite-v1:0": "Nova Lite", "us.amazon.nova-pro-v1:0": "Nova Pro",
    "amazon.nova-micro-v1:0": "Nova Micro (in-Region)",
}


TOKEN_COLORS = {"input": BLUE, "output": ORANGE}

# M02 Context Engineering entity maps (each is its own chart family — never mix two maps in one chart)
CONTEXT_COLORS = {                      # the five context categories of slide 6 (tool split into definitions/results)
    "system": VIOLET, "tool definitions": YELLOW, "tool results": ORANGE,
    "memory": MAGENTA, "retrieved": AQUA, "user": GREEN,
}
CACHE_COLORS = {"uncached input": BLUE, "cache write": YELLOW, "cache read": AQUA, "output": ORANGE}
STRATEGY_COLORS = {"format": YELLOW, "write": MAGENTA, "select": AQUA, "compress": VIOLET, "isolate": GREEN}
MANAGER_COLORS = {"none": MUTED, "sliding window": BLUE, "summarizing": VIOLET, "auto": AQUA}

# M03 Security entity maps
ROLE_COLORS["attacker"] = RED           # the attacker (planted instructions, exfiltration target)
# Decisions are STATES, so they use the reserved status steps (+ neutral), never a categorical slot, and a cell
# always carries a glyph and words too (✓ / ✗ / ⚠): allowed vs denied are only ~4 ΔE apart for deutan viewers.
DECISION_COLORS = {
    "allowed": STATUS["good"],          # the call ran / the request got in
    "denied": STATUS["critical"],       # denied by policy (Cedar ENFORCE, a local gate, a guardrail block)
    "rejected": STATUS["serious"],      # rejected at the door (inbound auth: 401/403 before any tool runs)
    "would_deny": STATUS["warning"],    # LOG_ONLY: executed, but ENFORCE would deny it
    "error": NEUTRAL,                   # failed for another reason
}
DECISION_LABELS = {"allowed": "allowed", "denied": "denied (policy)", "rejected": "rejected at the door",
                   "would_deny": "would deny (LOG_ONLY)", "error": "error"}
# Red-team outcomes (§6 before/after): the attack's result, not the call's
ATTACK_COLORS = {"succeeded": STATUS["critical"], "partial": STATUS["warning"], "stopped": STATUS["good"]}
ATTACK_LABELS = {"succeeded": "attack succeeded", "partial": "partly stopped", "stopped": "attack stopped"}
# Which layer of defense in depth (slide 6 / §1.4): categorical, one fixed hue per control
CONTROL_COLORS = {"none": MUTED, "identity": BLUE, "policy": VIOLET, "guardrail": AQUA, "network": YELLOW,
                  "audit": MAGENTA}
_BLUE_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]   # ordinal steps 250 -> 700


def ordinal(n: int) -> list[str]:
    """n ordered shades of one hue (light -> dark) for ordered categories such as Day 1 / Day 3 / Day 7."""
    if n <= 1:
        return [BLUE]
    idx = [round(i * (len(_BLUE_RAMP) - 1) / (n - 1)) for i in range(n)]
    return [_BLUE_RAMP[i] for i in idx]


def apply_style() -> None:
    """Call once per notebook (setup cell)."""
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"], "font.size": 11,
        "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.fontsize": 10,
        "text.color": INK, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titleweight": "bold", "axes.titlesize": 13, "axes.titlelocation": "left", "axes.titlepad": 10,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
        "axes.axisbelow": True, "lines.linewidth": 2, "lines.solid_capstyle": "round",
        "legend.frameon": False, "legend.labelcolor": INK_2, "figure.dpi": 110,
    })


def _finish(ax, title: str | None = None, subtitle: str | None = None) -> None:
    if title:
        ax.set_title(title, pad=24 if subtitle else 10)
    if subtitle:
        ax.text(0, 1.012, subtitle, transform=ax.transAxes, fontsize=10, color=INK_2, va="bottom")


def thousands(ax, axis: str = "x") -> None:
    """Thousands separators on a value axis (1,000 not 1000)."""
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter("{x:,.0f}")


# ----------------------------------------------------------------------------------------------
# Bars
# ----------------------------------------------------------------------------------------------
def hbar(labels: Sequence[str], values: Sequence[float], *, colors: Sequence[str] | str = BLUE,
         fmt: str = "{:,.0f}", title: str | None = None, xlabel: str | None = None, ax=None,
         highlight: Iterable[str] = (), subtitle: str | None = None):
    """Horizontal bars with the value at the tip (one series -> no legend box)."""
    ax = ax or plt.subplots(figsize=(8, 0.45 * len(labels) + 1.2))[1]
    cols = [colors] * len(labels) if isinstance(colors, str) else list(colors)
    hl = set(highlight)
    if hl:
        cols = [c if lab in hl else NEUTRAL for c, lab in zip(cols, labels)]
    y = range(len(labels))
    ax.barh(list(y), values, height=0.55, color=cols)
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    vmax = max(values) if len(values) else 1
    for yi, v in zip(y, values):                 # offset in points, so labels never touch the bar end
        ax.annotate(fmt.format(v), (max(v, 0), yi), xytext=(4, 0), textcoords="offset points",   # negatives: right of 0
                    va="center", ha="left", fontsize=10, color=INK_2)
    vmin = min(0, min(values)) if len(values) else 0
    ax.set_xlim(vmin * 1.25 if vmin else 0, vmax * 1.15 if vmax > 0 else 1)
    if vmin < 0:                                  # negative bars (e.g. "% cheaper" < 0): mark zero, label left of the bar
        ax.axvline(0, color=AXIS, linewidth=1)
    if vmax >= 1000:
        thousands(ax)
    if xlabel:
        ax.set_xlabel(xlabel)
    _finish(ax, title, subtitle)
    return ax


def stacked_hbar(labels: Sequence[str], series: dict[str, Sequence[float]], *, colors: Sequence[str] | None = None,
                 title: str | None = None, xlabel: str | None = None, total_fmt: str = "{:,.0f}", ax=None,
                 subtitle: str | None = None):
    """Stacked horizontal bars (e.g. input vs output tokens) with a 2px surface gap between segments."""
    ax = ax or plt.subplots(figsize=(8.5, 0.5 * len(labels) + 1.4))[1]
    colors = list(colors or PALETTE)
    left = [0.0] * len(labels)
    for i, (name, vals) in enumerate(series.items()):
        ax.barh(range(len(labels)), vals, left=left, height=0.55, color=colors[i], label=name,
                edgecolor=SURFACE, linewidth=2)
        left = [a + b for a, b in zip(left, vals)]
    ax.set_yticks(range(len(labels)), labels)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    vmax = max(left) if left else 1
    for yi, tot in enumerate(left):
        ax.annotate(total_fmt.format(tot), (tot, yi), xytext=(4, 0), textcoords="offset points", va="center",
                    fontsize=10, color=INK_2)
    ax.set_xlim(0, vmax * 1.18 if vmax else 1)
    if vmax >= 1000:                             # like hbar(): '{x:,.0f}' prints every tick of a $0.004 axis as '0'
        thousands(ax)
    if xlabel:
        ax.set_xlabel(xlabel)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12 if not xlabel else -0.2), ncols=len(series))
    _finish(ax, title, subtitle)
    return ax


def stacked_bars(x_labels: Sequence[str], series: dict[str, Sequence[float]], *, colors: dict[str, str] | Sequence[str],
                 title: str | None = None, xlabel: str | None = None, ylabel: str | None = None,
                 subtitle: str | None = None, ax=None, total_fmt: str | None = "{:,.0f}", figsize=(9.5, 4.2),
                 gap_after: Iterable[int] = ()):
    """Vertical stacked columns (e.g. context composition per turn). colors: {series name: color} or a list.
    gap_after: indexes of bars followed by a wider gap and a thin divider, to separate groups such as two arms
    (e.g. gap_after=[3]: calls 1-4 of arm A | calls 1-4 of arm B)."""
    ax = ax or plt.subplots(figsize=figsize)[1]
    cols = [colors[n] for n in series] if isinstance(colors, dict) else list(colors)
    gaps = set(gap_after)
    x = [i + 0.6 * sum(1 for g in gaps if g < i) for i in range(len(x_labels))]
    for g in gaps:
        if 0 <= g < len(x) - 1:
            ax.axvline((x[g] + x[g + 1]) / 2, color=AXIS, linewidth=0.8)
    bottom = [0.0] * len(x_labels)
    for (name, vals), c in zip(series.items(), cols):
        ax.bar(x, vals, bottom=bottom, width=0.65, color=c, label=name, edgecolor=SURFACE, linewidth=1.5)
        bottom = [a + b for a, b in zip(bottom, vals)]
    ax.set_xticks(x, x_labels)
    ax.grid(axis="x", visible=False)
    vmax = max(bottom) if bottom else 1
    if total_fmt:
        for xi, tot in zip(x, bottom):
            ax.annotate(total_fmt.format(tot), (xi, tot), xytext=(0, 3), textcoords="offset points", ha="center",
                        fontsize=9.5, color=INK_2)
    ax.set_ylim(0, vmax * 1.12 if vmax else 1)
    if vmax >= 1000:
        thousands(ax, "y")
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    if len(series) > 1:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14 if not xlabel else -0.22), ncols=min(len(series), 6))
    _finish(ax, title, subtitle)
    return ax


def lines(x: Sequence[float], series: dict[str, Sequence[float]], *, colors: dict[str, str] | Sequence[str],
          title: str | None = None, xlabel: str | None = None, ylabel: str | None = None, subtitle: str | None = None,
          ax=None, markers: bool = True, end_labels: bool = True, figsize=(9.5, 4.2), yfmt: str = "{:,.0f}"):
    """One line per series over a shared x (e.g. input tokens per turn per conversation manager).
    Values may contain None (gaps). End labels name each line at its last point (plus a legend)."""
    ax = ax or plt.subplots(figsize=figsize)[1]
    cols = [colors[n] for n in series] if isinstance(colors, dict) else list(colors)
    for (name, vals), c in zip(series.items(), cols):
        pts = [(xi, v) for xi, v in zip(x, vals) if v is not None]
        if not pts:
            continue
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=c, label=name,
                marker="o" if markers else None, markersize=3.5)
        if end_labels:
            ax.annotate(yfmt.format(pts[-1][1]), pts[-1], xytext=(5, 0), textcoords="offset points", va="center",
                        fontsize=9.5, color=INK_2)
    ax.set_ylim(bottom=0)
    if all(float(xi).is_integer() for xi in x):
        from matplotlib.ticker import MaxNLocator
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ymax = ax.get_ylim()[1]
    if ymax >= 1000:
        thousands(ax, "y")
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    if len(series) > 1:
        ax.legend(loc="upper left")
    _finish(ax, title, subtitle)
    return ax


def heatmap(rows: Sequence[str], cols: Sequence[str], values: Sequence[Sequence[float | None]], *,
            title: str | None = None, xlabel: str | None = None, ylabel: str | None = None, subtitle: str | None = None,
            fmt: str = "{:.0%}", vmin: float = 0.0, vmax: float = 1.0, good_high: bool = True, ax=None,
            figsize=(8.5, 3.8)):
    """Annotated grid (e.g. needle accuracy by context size x depth). One sequential hue; None = not run (grey)."""
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    ax = ax or plt.subplots(figsize=figsize)[1]
    ramp = ["#fcfcfb", "#86b6ef", "#2a78d6", "#104281"] if good_high else ["#104281", "#2a78d6", "#86b6ef", "#fcfcfb"]
    cmap = LinearSegmentedColormap.from_list("mladas_seq", ramp)
    cmap.set_bad(NEUTRAL)
    arr = np.array([[np.nan if v is None else v for v in r] for r in values], dtype=float)
    ax.imshow(np.ma.masked_invalid(arr), cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(cols)), cols)
    ax.set_yticks(range(len(rows)), rows)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    for i in range(len(rows)):
        for j in range(len(cols)):
            v = arr[i, j]
            txt = "n/a" if np.isnan(v) else fmt.format(v)
            dark = not np.isnan(v) and (v - vmin) / ((vmax - vmin) or 1) > 0.55
            ax.text(j, i, txt, ha="center", va="center", fontsize=10, color="white" if dark == good_high else INK)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    _finish(ax, title, subtitle)
    return ax


def _tint(color: str, amount: float) -> tuple[float, float, float]:
    """`amount` of `color` over the chart surface (0 = surface, 1 = the full color)."""
    from matplotlib.colors import to_rgb

    c, s = to_rgb(color), to_rgb(SURFACE)
    return tuple(amount * a + (1 - amount) * b for a, b in zip(c, s))


def grid(row_labels: Sequence[str], col_labels: Sequence[str], cell_text: Sequence[Sequence[str]],
         cell_keys: Sequence[Sequence[str | None]], *, colors: dict[str, str] | None = None, title: str | None = None,
         subtitle: str | None = None, legend: bool | dict[str, str] = True, fill: float = 0.42,
         row_header: str | None = None, ax=None, figsize=None, wrap: int = 16):
    """A categorical matrix: one colored cell per (row, column) with a short text in it, e.g. who gets in at the
    Gateway (§2.4), the policy decision matrix (§4.4) or the red-team before/after matrix (§6.5).

    cell_text[i][j] is what the cell says ("✓ 200", "✗ 403", "5/5 hijacked") — always put a glyph or words in it:
    the color is never the only signal. cell_keys[i][j] is a key of `colors` (default DECISION_COLORS) or None
    (a blank "not run" cell). legend=True labels the keys used with DECISION_LABELS / ATTACK_LABELS; pass
    {key: label} to name them yourself, or False. `fill` is the tint strength (text stays ink-colored for contrast).
    Width is capped at 10.5 in and text is >= 9.5 pt (projector rules). Returns the Axes."""
    import textwrap

    from matplotlib.patches import Patch, Rectangle

    colors = colors or DECISION_COLORS
    n_rows, n_cols = len(row_labels), len(col_labels)
    if len(cell_text) != n_rows or len(cell_keys) != n_rows or any(
            len(t) != n_cols or len(k) != n_cols for t, k in zip(cell_text, cell_keys)):
        raise ValueError(f"cell_text and cell_keys must both be {n_rows} rows x {n_cols} columns")
    unknown = {k for row in cell_keys for k in row if k is not None and k not in colors}
    if unknown:
        raise ValueError(f"cell keys {sorted(unknown)} are not in colors ({sorted(colors)})")

    cols_wrapped = ["\n".join(textwrap.wrap(str(c), wrap, break_long_words=False)) or str(c) for c in col_labels]
    longest_cell = max([len(str(t)) for row in cell_text for t in row]
                       + [len(w) for c in col_labels for w in str(c).split()], default=4)
    label_w = min(3.4, 0.4 + 0.085 * max((len(str(r)) for r in row_labels), default=8))
    cell_w = min(1.9, max(0.95, 0.12 + 0.085 * max(longest_cell, 8)), (10.5 - label_w - 0.3) / max(n_cols, 1))
    header_lines = max(c.count("\n") + 1 for c in cols_wrapped) if cols_wrapped else 1
    if ax is None:
        width = min(10.5, label_w + cell_w * n_cols + 0.3)
        height = 0.55 * n_rows + 0.3 * header_lines + (1.35 if title else 0.6) + (0.55 if legend else 0)
        ax = plt.subplots(figsize=figsize or (width, height))[1]

    for i in range(n_rows):
        for j in range(n_cols):
            key = cell_keys[i][j]
            face = _tint(colors[key], fill) if key is not None else SURFACE
            ax.add_patch(Rectangle((j, i), 1, 1, facecolor=face, edgecolor=SURFACE, linewidth=2.5))
            if key is not None:                        # a full-strength bar on the left keeps the hue legible
                ax.add_patch(Rectangle((j + 0.03, i + 0.12), 0.045, 0.76, facecolor=colors[key], edgecolor="none"))
            ax.text(j + 0.54, i + 0.5, str(cell_text[i][j]), ha="center", va="center", fontsize=10,
                    color=INK if key is not None else MUTED, fontweight="bold" if key is not None else "normal")
    ax.set_xlim(0, n_cols)
    ax.set_ylim(n_rows, 0)
    ax.set_xticks([j + 0.5 for j in range(n_cols)], cols_wrapped, fontsize=10)
    ax.set_yticks([i + 0.5 for i in range(n_rows)], [str(r) for r in row_labels], fontsize=10)
    ax.xaxis.tick_top()
    ax.tick_params(length=0, pad=6)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    if row_header:
        ax.text(-0.02, 1.0, row_header, transform=ax.transAxes, ha="right", va="bottom", fontsize=10, color=INK_2,
                fontweight="bold")

    if legend:
        used = list(dict.fromkeys(k for row in cell_keys for k in row if k is not None))
        order = [k for k in colors if k in used]
        names = legend if isinstance(legend, dict) else {**DECISION_LABELS, **ATTACK_LABELS}
        handles = [Patch(facecolor=_tint(colors[k], fill), edgecolor=colors[k], linewidth=1.5, label=names.get(k, k))
                   for k in order]
        if handles:
            ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.04), ncols=min(len(handles), 4),
                      fontsize=10, handlelength=1.4, borderaxespad=0.2)
    header_pt = 10 + 13 * header_lines                 # the column labels sit above the cells
    if subtitle:
        ax.annotate(subtitle, xy=(0, 1), xycoords="axes fraction", xytext=(0, header_pt + 4),
                    textcoords="offset points", fontsize=10, color=INK_2, va="bottom", ha="left")
    if title:
        ax.set_title(title, pad=header_pt + (22 if subtitle else 8))
    return ax


# ----------------------------------------------------------------------------------------------
# Repeated independent runs (strip / dot plot)
# ----------------------------------------------------------------------------------------------
def strips(labels: Sequence[str], values: Sequence[Sequence[float | None]], *, colors: Sequence[str] | str = BLUE,
           point_colors: Sequence[Sequence[str]] | None = None, legend: dict[str, str] | None = None,
           title: str | None = None, subtitle: str | None = None, xlabel: str | None = None,
           fmt: str = "{:.2f}", median_label: str = "median {}", zero: bool = True, ax=None, figsize=None):
    """Repeated independent runs as unconnected dots: one row per group, plus a median tick and its value.

    The form for values that are separate runs (latency, score, tokens per repeat): a line would imply an order
    between runs and a bar would hide the spread. colors: one color per row (an entity map's values) or one color.
    point_colors: one color per dot (e.g. each call's cache outcome); overrides colors. legend: {label: color}, drawn
    below the axes; pass it whenever dot colors encode something the row labels don't. None values are skipped;
    neighbouring values are offset vertically (deterministic) so no dot hides another. zero=True starts x at 0."""
    import statistics

    n_rows = len(labels)
    ax = ax or plt.subplots(figsize=figsize or (9, 0.72 * n_rows + 1.9))[1]
    row_colors = [colors] * n_rows if isinstance(colors, str) else list(colors)
    vmax = 0.0
    for i, vals in enumerate(values):
        pts = [(v, point_colors[i][k] if point_colors else row_colors[i]) for k, v in enumerate(vals) if v is not None]
        if not pts:
            continue
        for rank, k in enumerate(sorted(range(len(pts)), key=lambda k: pts[k][0])):   # rank -> row offset -1/0/+1
            ax.scatter(pts[k][0], i + (rank % 3 - 1) * 0.14, s=60, color=pts[k][1], edgecolors=SURFACE,
                       linewidths=0.8, zorder=3)
        med = statistics.median(v for v, _ in pts)
        ax.plot([med, med], [i - 0.3, i + 0.3], color=INK, linewidth=2, solid_capstyle="butt", zorder=4)
        ax.annotate(median_label.format(fmt.format(med)), (med, i - 0.3), xytext=(0, 2), textcoords="offset points",
                    ha="center", va="bottom", fontsize=9.5, color=INK_2)
        vmax = max(vmax, max(v for v, _ in pts))
    ax.set_yticks(range(n_rows), labels)
    ax.set_ylim(n_rows - 0.45, -0.8)                   # first label on top; headroom for the median labels
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    if zero:
        ax.set_xlim(0, vmax * 1.08 if vmax else 1)
    if xlabel:
        ax.set_xlabel(xlabel)
    if legend:
        for name, c in legend.items():
            ax.scatter([], [], s=60, color=c, label=name)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2 if xlabel else -0.1), ncols=min(len(legend), 4))
    _finish(ax, title, subtitle)
    return ax


# ----------------------------------------------------------------------------------------------
# Timeline (Gantt) — parallel execution
# ----------------------------------------------------------------------------------------------
def gantt(spans: Sequence[dict[str, Any]], *, title: str = "Execution timeline", group_colors: dict[str, str] | None = None,
          ax=None, annotate_wall: bool = True, legend_loc: str = "below"):
    """spans: [{"name": str, "start": seconds, "end": seconds, "group": str}] — one bar per step."""
    spans = sorted(spans, key=lambda s: (s["start"], s["name"]))
    ax = ax or plt.subplots(figsize=(9, 0.42 * len(spans) + 1.5))[1]
    groups = list(dict.fromkeys(s.get("group", "step") for s in spans))
    gcol = group_colors or {g: PALETTE[i % len(PALETTE)] for i, g in enumerate(groups)}
    wall_end = max(s["end"] for s in spans) if spans else 1
    for i, s in enumerate(spans):
        dur = s["end"] - s["start"]
        ax.barh(i, dur, left=s["start"], height=0.5, color=gcol[s.get("group", "step")], label=s.get("group", "step"))
        if dur < wall_end * 0.01:          # near-instant (deterministic) steps stay visible as a diamond
            ax.scatter(s["start"], i, marker="D", s=40, color=gcol[s.get("group", "step")], zorder=3)
        ax.text(s["end"] + wall_end * 0.01, i, f"{dur:.1f}s", va="center", fontsize=9.5, color=INK_2)
    ax.set_xlim(0, wall_end * 1.12)
    ax.set_yticks(range(len(spans)), [s["name"] for s in spans])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("seconds since start")
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    if len(uniq) > 1:
        if legend_loc == "below":
            ax.legend(uniq.values(), uniq.keys(), loc="upper center", bbox_to_anchor=(0.5, -0.18), ncols=len(uniq))
        else:
            ax.legend(uniq.values(), uniq.keys(), loc=legend_loc)
    if annotate_wall and spans:
        wall = max(s["end"] for s in spans) - min(s["start"] for s in spans)
        busy = sum(s["end"] - s["start"] for s in spans)
        ax.axvline(max(s["end"] for s in spans), color=AXIS, linewidth=1)
        _finish(ax, title, f"wall clock {wall:.1f}s  ·  sum of step times {busy:.1f}s")
    else:
        _finish(ax, title)
    return ax


# ----------------------------------------------------------------------------------------------
# Topologies (networkx)
# ----------------------------------------------------------------------------------------------
def _nx():
    import networkx as nx
    return nx


def draw_topology(nodes: dict[str, str], edges: Sequence[tuple[str, str]], *, pos: dict[str, tuple[float, float]] | None = None,
                  walked: Iterable[tuple[str, str]] = (), visited: Iterable[str] | None = None,
                  edge_labels: dict[tuple[str, str], str] | None = None, title: str | None = None,
                  ax=None, node_size: int = 2300, legend: bool = True, dashed: Iterable[tuple[str, str]] = (),
                  legend_loc: str = "lower left", edge_label_fontsize: float = 10, role_names: dict[str, str] | None = None,
                  node_sizes: dict[str, int] | None = None, font_size: float = 9.5):
    """Generic directed topology. nodes: {node_id: role} with role in ROLE_COLORS.
    walked edges / visited nodes are drawn in ink; everything else stays recessive (not executed)."""
    nx = _nx()
    G = nx.DiGraph()
    for n, role in nodes.items():
        G.add_node(n, role=role)
    G.add_edges_from(edges)
    pos = pos or nx.spring_layout(G, seed=7)
    ax = ax or plt.subplots(figsize=(9, 5))[1]
    walked, dashed = set(walked), set(dashed)
    visited = set(visited) if visited is not None else set(G.nodes)
    fill = [ROLE_COLORS.get(G.nodes[n]["role"], BLUE) if n in visited else "#eeede9" for n in G]
    ring = [INK if n in visited else AXIS for n in G]
    sizes = [(node_sizes or {}).get(n, node_size) for n in G]
    nx.draw_networkx_nodes(G, pos, ax=ax, node_size=sizes, node_color=fill, edgecolors=ring, linewidths=1.5)
    nx.draw_networkx_labels(G, pos, ax=ax, font_size=font_size, font_color=INK,
                            labels={n: n.replace("_", "\n") for n in G})
    straight = [e for e in G.edges if not G.has_edge(e[1], e[0])]
    curved = [e for e in G.edges if G.has_edge(e[1], e[0])]
    for group, style in ((straight, "arc3,rad=0.0"), (curved, "arc3,rad=0.22")):
        if not group:
            continue
        nx.draw_networkx_edges(G, pos, ax=ax, edgelist=group, nodelist=list(G), node_size=sizes, arrows=True,
                               arrowsize=16,
                               edge_color=[INK if e in walked else AXIS for e in group],
                               width=[2.2 if e in walked else 1.2 for e in group],
                               style=["dashed" if e in dashed else "solid" for e in group], connectionstyle=style)
    if edge_labels:
        nx.draw_networkx_edge_labels(G, pos, ax=ax, edge_labels=edge_labels, font_size=edge_label_fontsize, font_color=INK_2,
                                     rotate=False,
                                     bbox={"boxstyle": "round,pad=0.2", "fc": SURFACE, "ec": "none"})
    if legend:
        roles = list(dict.fromkeys(G.nodes[n]["role"] for n in G))
        for r in roles:
            ax.scatter([], [], s=90, color=ROLE_COLORS.get(r, BLUE), label=(role_names or {}).get(r, r))
        if len(roles) > 1:
            ax.legend(loc=legend_loc, fontsize=9.5)
    ax.axis("off")
    ax.margins(0.12)
    _finish(ax, title)
    return ax


def graph_to_topology(graph: Any, remote_types: tuple = ()) -> tuple[dict[str, str], list[tuple[str, str]], dict]:
    """Read nodes/edges/edge-condition names from a built Strands Graph."""
    from strands.multiagent.base import MultiAgentBase

    nodes, labels = {}, {}
    for nid, node in graph.nodes.items():
        ex = node.executor
        if remote_types and isinstance(ex, remote_types):
            role = "remote"
        elif type(ex).__name__ == "FunctionNode":
            role = "function"
        elif isinstance(ex, MultiAgentBase):
            role = "multiagent"
        else:
            role = "specialist"
        nodes[nid] = role
    edges = []
    for e in graph.edges:
        a, b = e.from_node.node_id, e.to_node.node_id
        edges.append((a, b))
        if e.condition is not None:
            labels[(a, b)] = getattr(e.condition, "__name__", "condition")
    return nodes, edges, labels


def layered_pos(nodes: Sequence[str], edges: Sequence[tuple[str, str]], entry: Sequence[str]) -> dict[str, tuple[float, float]]:
    """Left-to-right layers by shortest distance from the entry nodes (works without graphviz)."""
    nx = _nx()
    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    G.add_edges_from(edges)
    depth: dict[str, int] = {}
    for s in entry:
        for n, d in nx.single_source_shortest_path_length(G, s).items():
            depth[n] = min(depth.get(n, d), d)
    layers: dict[int, list[str]] = {}
    for n in nodes:
        layers.setdefault(depth.get(n, 0), []).append(n)
    pos = {}
    for d, ns in layers.items():
        for i, n in enumerate(ns):
            pos[n] = (d * 1.6, (len(ns) - 1) / 2 - i)
    return pos


def swarm_path(agents: Sequence[str], history: Sequence[str], *, title: str = "Swarm handoff path", ax=None):
    """Agents on a circle; numbered arrows show the actual handoff sequence."""
    import math

    ax = ax or plt.subplots(figsize=(6.5, 5.5))[1]
    n = len(agents)
    pos = {a: (math.cos(2 * math.pi * i / n + math.pi / 2), math.sin(2 * math.pi * i / n + math.pi / 2))
           for i, a in enumerate(agents)}
    visited = set(history)
    for a, (x, y) in pos.items():
        ax.scatter(x, y, s=2600, color=AQUA if a in visited else "#eeede9", edgecolors=INK if a in visited else AXIS,
                   linewidths=1.5, zorder=3)
        ax.text(x, y, a.replace("_", "\n"), ha="center", va="center", fontsize=9.5, color=INK, zorder=4)
    seen: dict[frozenset, int] = {}
    for step, (a, b) in enumerate(zip(history, history[1:]), start=1):
        (x1, y1), (x2, y2) = pos[a], pos[b]
        k = seen.get(frozenset((a, b)), 0)
        seen[frozenset((a, b))] = k + 1
        rad = 0.15 + 0.2 * k                       # bend repeated a<->b hops further apart
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1), zorder=2,
                    arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.8, shrinkA=26, shrinkB=26,
                                    connectionstyle=f"arc3,rad={rad}"))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        nx_, ny_ = -(y2 - y1), (x2 - x1)           # normal vector: push the label onto the bent arc
        norm = (nx_ ** 2 + ny_ ** 2) ** 0.5 or 1
        off = rad * 0.5 * ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        ax.text(mx - nx_ / norm * off, my - ny_ / norm * off, f" {step} ", fontsize=9.5, color=SURFACE, ha="center",
                va="center", bbox=dict(boxstyle="circle,pad=0.25", fc=INK, ec="none"), zorder=5)
    ax.set_xlim(-1.6, 1.6)
    ax.set_ylim(-1.5, 1.5)
    ax.set_aspect("equal")
    ax.axis("off")
    _finish(ax, title)
    return ax


# ----------------------------------------------------------------------------------------------
# Notebook display helpers
# ----------------------------------------------------------------------------------------------
def chat(who: str, text: str, *, role: str = "agent") -> None:
    """Render one chat turn as a compact card (customer vs agent) in the notebook."""
    import html

    from IPython.display import HTML, display

    accent = {"customer": MUTED, "agent": BLUE, "orchestrator": BLUE, "specialist": AQUA, "remote": ORANGE,
              "memory": MAGENTA, "system": VIOLET}.get(role, BLUE)
    body = html.escape(text or "").replace("\n", "<br>")
    display(HTML(                                 # tex2jax_ignore: two '$' amounts must not become LaTeX
        f'<div class="tex2jax_ignore mathjax_ignore" style="border-left:3px solid {accent};padding:6px 12px;margin:6px 0;background:rgba(127,127,127,.06);'
        f'border-radius:4px;font-family:system-ui,-apple-system,sans-serif;font-size:13px;line-height:1.45">'
        f'<div style="font-size:11px;font-weight:600;letter-spacing:.02em;opacity:.75;margin-bottom:2px">{html.escape(who)}</div>'
        f'{body}</div>'))
