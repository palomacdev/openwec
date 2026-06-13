"""
OpenWEC SDK — Plotting
Basic matplotlib charts, inspired by FastF1's plotting helpers.

These are intentionally simple — alpha quality. Returns a matplotlib
Figure so users can further customize (fig.savefig, etc).
"""

from __future__ import annotations
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import pandas as pd


def plot_lap_evolution(laps: pd.DataFrame, car: str | None = None, ax=None):
    """
    Plots lap time evolution over the race.

    Args:
        laps: DataFrame from session.laps(car=...)
        car:  car number, used for the plot title
        ax:   optional matplotlib axes to plot on
    """
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 5))
    else:
        fig = ax.figure

    df = laps.copy()
    df = df[df["lap_time_s"].notna()]
    df = df[df["lap_time_s"] < 600]  # exclude formation/SC laps with huge times

    # Highlight pit laps
    pit_laps = df[df["crossing_finish_in_pit"] == True]
    clean    = df[df["crossing_finish_in_pit"] == False]

    ax.plot(clean["lap_number"], clean["lap_time_s"],
            marker="o", markersize=3, linewidth=1, label="Lap time")

    if not pit_laps.empty:
        ax.scatter(pit_laps["lap_number"], pit_laps["lap_time_s"],
                    color="red", marker="x", s=50, label="Pit lap", zorder=5)

    ax.set_xlabel("Lap")
    ax.set_ylabel("Lap time (s)")
    title = f"Lap Evolution — Car #{car}" if car else "Lap Evolution"
    ax.set_title(title)
    ax.legend()
    ax.grid(alpha=0.3)

    fig.tight_layout()
    return fig


def plot_stint_chart(stints: pd.DataFrame, ax=None):
    """
    Plots a horizontal stint chart — one row per car, colored bars per stint.
    Similar to FastF1's strategy chart.

    Args:
        stints: DataFrame from session.stints() (all cars)
        ax:     optional matplotlib axes to plot on
    """
    if ax is None:
        fig, ax = plt.subplots(figsize=(12, max(4, len(stints["car_number"].unique()) * 0.3)))
    else:
        fig = ax.figure

    cars = sorted(stints["car_number"].unique(), key=lambda x: int(x) if x.isdigit() else 999)

    # Color map by stint number (cycles)
    cmap = plt.get_cmap("tab10")

    for i, car in enumerate(cars):
        car_stints = stints[stints["car_number"] == car].sort_values("stint_number")
        for _, stint in car_stints.iterrows():
            duration = stint["end_lap"] - stint["start_lap"] + 1
            color = cmap(int(stint["stint_number"]) % 10)
            ax.barh(i, duration, left=stint["start_lap"], color=color,
                    edgecolor="white", height=0.6)

    ax.set_yticks(range(len(cars)))
    ax.set_yticklabels([f"#{c}" for c in cars])
    ax.set_xlabel("Lap")
    ax.set_title("Stint Chart")
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.3)

    fig.tight_layout()
    return fig


def plot_gap_to_leader(gaps: pd.DataFrame, ax=None):
    """
    Plots gap to leader over race distance.

    Args:
        gaps: DataFrame from session.gaps() — needs lap_number, car_number, cumulative_s
        ax:   optional matplotlib axes to plot on
    """
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 6))
    else:
        fig = ax.figure

    df = gaps.copy()
    df = df[df["cumulative_s"].notna()]

    # Leader = minimum cumulative_s per lap
    leader_per_lap = df.groupby("lap_number")["cumulative_s"].min()

    for car, group in df.groupby("car_number"):
        group = group.set_index("lap_number")
        gap = group["cumulative_s"] - leader_per_lap.reindex(group.index)
        ax.plot(gap.index, gap.values, label=f"#{car}", linewidth=1)

    ax.set_xlabel("Lap")
    ax.set_ylabel("Gap to leader (s)")
    ax.set_title("Gap to Leader")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=0.3)

    fig.tight_layout()
    return fig