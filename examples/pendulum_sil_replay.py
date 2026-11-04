"""Replay a SIL JSONL recording with the existing Matplotlib animators."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import PillowWriter
import numpy as np

from benchmark_systems.animations import AutoAnimation, Pendulum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--output", type=Path, default=Path("pendulum-replay.gif"))
    args = parser.parse_args()
    with args.recording.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    # Stop/failure records may repeat the last physical timestamp.
    rows = [row for index, row in enumerate(rows)
            if index == 0 or row["t"] > rows[index - 1]["t"]]
    if len(rows) < 2:
        parser.error("recording must contain at least two distinct simulated times")
    times = np.array([row["t"] for row in rows])
    theta = np.array([row["x"][0] for row in rows])
    torque = np.array([row["u"][0] for row in rows])
    length = rows[0]["parameters"]["L"]
    fig, (scene, trace) = plt.subplots(1, 2, figsize=(9, 4.2))
    fig.suptitle("Péndulo SIL · reproducción del registro completo")
    scene.set_xlabel("x (m)")
    scene.set_ylabel("y (m)")
    update_pendulum = Pendulum(scene, L=length, theta=theta)
    trace.plot(times, theta, color="steelblue", label="Ángulo (rad)")
    # Each snapshot reports the input used over the interval ending at its t.
    trace.step(times, torque, where="pre", color="darkorange", label="Par (N·m)")
    cursor = trace.axvline(times[0], color="gray", linestyle="--")
    trace.set_xlabel("Tiempo simulado (s)")
    trace.legend(loc="upper right")
    trace.grid(alpha=0.2)
    label = scene.text(0.02, 0.98, "", transform=scene.transAxes, va="top")

    def update(frame):
        artists = update_pendulum(frame)
        cursor.set_xdata([times[frame], times[frame]])
        label.set_text(f"Par retenido: {torque[frame]:.2f} N·m")
        return (*artists, cursor, label)

    stride = max(1, int(round(1 / (20 * np.median(np.diff(times))))))
    animation, _ = AutoAnimation(fig, update, times=times, speed=stride, fps=20)
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    animation.save(str(args.output), writer=PillowWriter(fps=20))
    plt.close(fig)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
