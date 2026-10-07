"""
Intent: Render the figures as SVG with no plotting dependency, so the bytes are a pure
        function of the numbers and can be hashed alongside them
Context: matplotlib output varies across versions and platforms; a hand-written SVG does
        not. The charts are deliberately plain: bars, lines, a threshold rule, labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

W, H = 720, 360
PAD_L, PAD_R, PAD_T, PAD_B = 70, 20, 40, 70
FONT = "font-family='Helvetica, Arial, sans-serif' font-size='12'"


def _fmt(x: float) -> str:
    return f"{x:.4g}"


def _frame(title: str, x_label: str, y_label: str) -> list[str]:
    return [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' viewBox='0 0 {W} {H}'>",
        f"<rect width='{W}' height='{H}' fill='white'/>",
        f"<text x='{W / 2}' y='22' text-anchor='middle' {FONT} font-size='14'>{title}</text>",
        f"<text x='{W / 2}' y='{H - 8}' text-anchor='middle' {FONT}>{x_label}</text>",
        f"<text x='14' y='{H / 2}' text-anchor='middle' {FONT} "
        f"transform='rotate(-90 14 {H / 2})'>{y_label}</text>",
    ]


def _axes(y_min: float, y_max: float, ticks: int = 5) -> list[str]:
    out = [
        f"<line x1='{PAD_L}' y1='{PAD_T}' x2='{PAD_L}' y2='{H - PAD_B}' stroke='black'/>",
        f"<line x1='{PAD_L}' y1='{H - PAD_B}' x2='{W - PAD_R}' y2='{H - PAD_B}' stroke='black'/>",
    ]
    for i in range(ticks + 1):
        v = y_min + (y_max - y_min) * i / ticks
        y = _y(v, y_min, y_max)
        out.append(f"<line x1='{PAD_L - 4}' y1='{y}' x2='{PAD_L}' y2='{y}' stroke='black'/>")
        out.append(f"<text x='{PAD_L - 8}' y='{y + 4}' text-anchor='end' {FONT}>{_fmt(v)}</text>")
    return out


def _y(v: float, y_min: float, y_max: float) -> float:
    span = (y_max - y_min) or 1.0
    return round(H - PAD_B - (v - y_min) / span * (H - PAD_T - PAD_B), 2)


def bar_chart(
    path: Path, title: str, labels: Sequence[str], values: Sequence[float | None],
    y_label: str, y_max: float | None = None, errors: Sequence[float] | None = None,
) -> None:
    present = [v for v in values if v is not None]
    top = y_max if y_max is not None else (max(present) * 1.15 if present else 1.0)
    top = top or 1.0
    out = _frame(title, "", y_label) + _axes(0.0, top)
    n = max(len(labels), 1)
    slot = (W - PAD_L - PAD_R) / n
    for i, (label, value) in enumerate(zip(labels, values)):
        x0 = PAD_L + i * slot + slot * 0.15
        width = slot * 0.7
        if value is None:
            out.append(f"<text x='{x0 + width / 2}' y='{H - PAD_B - 6}' text-anchor='middle' "
                       f"{FONT} fill='gray'>n/a</text>")
        else:
            y = _y(value, 0.0, top)
            out.append(f"<rect x='{x0:.2f}' y='{y}' width='{width:.2f}' "
                       f"height='{H - PAD_B - y:.2f}' fill='#4a6fa5'/>")
            out.append(f"<text x='{x0 + width / 2:.2f}' y='{y - 4}' text-anchor='middle' "
                       f"{FONT}>{_fmt(value)}</text>")
            if errors is not None and errors[i]:
                lo = _y(max(0.0, value - errors[i]), 0.0, top)
                hi = _y(value + errors[i], 0.0, top)
                cx = x0 + width / 2
                out.append(
                    f"<line x1='{cx:.2f}' y1='{lo}' x2='{cx:.2f}' y2='{hi}' stroke='black'/>"
                )
        out.append(f"<text x='{x0 + width / 2:.2f}' y='{H - PAD_B + 14}' text-anchor='middle' "
                   f"{FONT} transform='rotate(25 {x0 + width / 2:.2f} {H - PAD_B + 14})'>"
                   f"{label}</text>")
    out.append("</svg>")
    path.write_text("\n".join(out) + "\n")


def line_chart(path: Path, title: str, x_label: str, y_label: str,
               series: Sequence[tuple[str, Sequence[tuple[float, float]], str]],
               y_min: float = 0.0, y_max: float = 1.0, h_line: float | None = None,
               bands: Sequence[tuple[float, float, str]] = ()) -> None:
    out = _frame(title, x_label, y_label)
    xs = [x for _, pts, _ in series for x, _ in pts]
    x_max = max(xs) if xs else 1.0

    def px(x: float) -> float:
        return round(PAD_L + x / (x_max or 1.0) * (W - PAD_L - PAD_R), 2)

    for x0, x1, color in bands:
        out.append(f"<rect x='{px(x0)}' y='{PAD_T}' width='{max(px(x1) - px(x0), 0.5):.2f}' "
                   f"height='{H - PAD_T - PAD_B}' fill='{color}' opacity='0.18'/>")
    out += _axes(y_min, y_max)
    for i in range(6):
        x = x_max * i / 5
        out.append(f"<text x='{px(x)}' y='{H - PAD_B + 16}' text-anchor='middle' {FONT}>"
                   f"{_fmt(x)}</text>")
    if h_line is not None:
        y = _y(h_line, y_min, y_max)
        out.append(f"<line x1='{PAD_L}' y1='{y}' x2='{W - PAD_R}' y2='{y}' stroke='#b03a2e' "
                   f"stroke-dasharray='6 4'/>")
    for j, (name, pts, color) in enumerate(series):
        if pts:
            d = " ".join(f"{px(x)},{_y(y, y_min, y_max)}" for x, y in pts)
            out.append(f"<polyline fill='none' stroke='{color}' stroke-width='1.5' points='{d}'/>")
        out.append(f"<rect x='{W - PAD_R - 150}' y='{PAD_T + 6 + 16 * j}' width='12' height='12' "
                   f"fill='{color}'/>")
        out.append(f"<text x='{W - PAD_R - 132}' y='{PAD_T + 17 + 16 * j}' {FONT}>{name}</text>")
    out.append("</svg>")
    path.write_text("\n".join(out) + "\n")
