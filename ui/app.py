"""Gradio UI — LOCAL TESTING ONLY.

share=False always: on a government network share=True opens a tunnel out to
gradio.live — that is a security incident, not a convenience.

Rendering happens HERE, in the client, from the chart *specification* the
agent returns. The API never ships a rendered image.

Layout: a chat panel (with per-session conversation memory) on the left and
a details panel (chart, result table, SQL, agent steps) on the right, so a
non-technical reader sees question -> answer, and the SQL stays one click
away for anyone who wants to check it.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")  # no display server; Plotly would pull JS from a CDN
import matplotlib.pyplot as plt
import gradio as gr
import pandas as pd

from agent import llm
from agent.graph import ask

# Colorblind-safe categorical palette; the slot ORDER is the safety
# mechanism (adjacent hues stay distinguishable under CVD), so series
# take slots in this fixed order, never cycled or re-sorted.
PALETTE = ["#2a78d6", "#008300", "#e87ba4", "#eda100",
           "#1baf7a", "#eb6834", "#4a3aa7", "#e34948"]
SURFACE = "#fcfcfb"   # chart surface
INK = "#0b0b0b"       # titles
INK_2 = "#52514e"     # axis titles / legend text
MUTED = "#898781"     # tick labels
GRID = "#e1e0d9"      # hairline gridlines
BASELINE = "#c3c2b7"  # x-axis baseline

EXAMPLES = [
    "Show the trend of cleared disbursements over fiscal years in USD",
    "Who are our top 5 partners by total cleared disbursements?",
    "What was our total debt service in FY2025?",
    "Break down cleared commitments in FY2025 by sector",
]

CSS = """
#header h1 {margin-bottom: 0.1em}
#header p {margin-top: 0.2em}
#examples-label {margin-bottom: -8px}
"""


def _pretty(label: str) -> str:
    """fiscal_year -> fiscal year; keeps axis titles readable for humans."""
    return str(label).replace("_", " ")


def _style_axes(ax):
    """Recessive chrome: y-grid hairlines only, baseline-only spines,
    muted tick labels — the data ink should be the loudest thing."""
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelcolor=INK_2, length=0)


def render_chart(chart: dict | None, columns: list, rows: list):
    """Turn the agent's chart *specification* into a matplotlib figure.

    `chart` is None whenever the agent (or the hard gate in charts.py)
    decided a chart would not help — in that case the panel stays empty.
    """
    if not chart or not rows:
        return None
    df = pd.DataFrame(rows, columns=columns)
    x, title = chart["x"], chart["title"]
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    fig.patch.set_facecolor(SURFACE)
    if chart["type"] == "bar":
        ax.bar(df[x].astype(str), df[chart["y"]], color=PALETTE[0], width=0.72, zorder=3)
        ax.set_ylabel(_pretty(chart["y"]), color=INK_2)
        _style_axes(ax)
    elif chart["type"] == "line":
        ax.plot(df[x].astype(str), df[chart["y"]],
                color=PALETTE[0], linewidth=2, marker="o", markersize=5, zorder=3)
        ax.set_ylabel(_pretty(chart["y"]), color=INK_2)
        _style_axes(ax)
    elif chart["type"] == "pie":
        _, _, autotexts = ax.pie(
            df[chart["y"]], labels=[_pretty(v) for v in df[x].astype(str)],
            autopct="%1.1f%%", colors=PALETTE[:len(df)], startangle=90,
            counterclock=False, wedgeprops={"edgecolor": SURFACE, "linewidth": 2},
            textprops={"color": INK_2})
        # percentage labels sit ON the coloured slices — plain gray vanishes
        # against the darker hues; bold white stays readable on all of them
        plt.setp(autotexts, color="white", fontweight="bold")
    elif chart["type"] == "grouped_bar":
        y_cols = [c.strip() for c in chart["y"].split(",")]
        n = len(df)
        width = 0.8 / len(y_cols)
        for i, y in enumerate(y_cols):
            ax.bar([j + i * width for j in range(n)], df[y], width=width * 0.94,
                   label=_pretty(y), color=PALETTE[i % len(PALETTE)], zorder=3)
        ax.set_xticks([j + 0.4 - width / 2 for j in range(n)])
        ax.set_xticklabels(df[x].astype(str))
        # >= 2 series: a legend is required — identity must not be color-alone.
        ax.legend(frameon=False, labelcolor=INK_2)
        _style_axes(ax)
    ax.set_title(title, loc="left", color=INK, fontweight="bold", pad=12)
    if chart["type"] != "pie":
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    return fig


def respond(question, history, thread_id):
    """One chat turn: append the user question, run the agent, append the
    answer, and refresh the details panel (chart / table / SQL / steps)."""
    history = list(history or [])
    q = (question or "").strip()
    if not q:
        # Nothing typed: leave every panel exactly as it is.
        return "", history, gr.update(), gr.update(), gr.update(), gr.update(), thread_id

    history.append({"role": "user", "content": q})
    try:
        result = ask(q, thread_id=thread_id)
    except Exception as exc:  # never crash the chat; surface the error inline
        history.append({"role": "assistant",
                        "content": f"⚠️ Sorry, something went wrong: {exc}"})
        return "", history, gr.update(), gr.update(), gr.update(), gr.update(), thread_id

    fig = render_chart(result["chart"], result["columns"], result["rows"])
    table = (pd.DataFrame(result["rows"], columns=result["columns"])
             if result["rows"] else None)
    sql = result["sql"] or "-- no query executed"
    trace = ("```\n" + "\n".join(result["trace"]) + "\n```"
             if result["trace"] else "_no steps recorded_")

    answer = result["answer"] or "_(no answer returned)_"
    extras = (["chart"] if fig is not None else []) + (["result table"] if table is not None else [])
    if extras:
        answer += f"\n\n<sub>📊 See the {' and '.join(extras)} in the details panel.</sub>"
    history.append({"role": "assistant", "content": answer})

    return "", history, sql, table, fig, trace, thread_id


def reset():
    """New conversation: fresh thread_id (fresh agent memory), empty panels."""
    return "", [], "-- no query executed", None, None, "", str(uuid.uuid4())


with gr.Blocks(title="EAD SQL Agent (local testing)") as demo:
    thread = gr.State(lambda: str(uuid.uuid4()))

    with gr.Column(elem_id="header"):
        gr.Markdown(
            "# EAD Assistant — Foreign Economic Assistance\n"
            "Ask in plain English about **commitments, disbursements and debt service**. "
            "The agent writes read-only SQL, answers in words, and adds a chart only "
            "when it genuinely helps.\n\n"
            f"<sub>Local testing UI · model: `{llm.model_name()}` · database access: read-only</sub>"
        )

    with gr.Row(equal_height=False):
        # --- Left: the conversation ---
        with gr.Column(scale=6):
            chatbot = gr.Chatbot(
                height=480, label="Conversation",
                placeholder=("**Ask anything about Foreign Economic Assistance.**\n\n"
                             "Follow-ups work too — e.g. ask about FY2025, then just "
                             "say *“and what about FY2023?”*"),
            )
            with gr.Row():
                question = gr.Textbox(
                    placeholder="e.g. What was our total debt service in FY2025?",
                    show_label=False, scale=8, autofocus=True, container=False,
                )
                ask_btn = gr.Button("Ask", variant="primary", scale=1, min_width=80)
            gr.Markdown("<sub>Try one of these:</sub>", elem_id="examples-label")
            example_btns = []
            for row_examples in (EXAMPLES[:2], EXAMPLES[2:]):
                with gr.Row():
                    for q in row_examples:
                        example_btns.append((gr.Button(q, size="sm"), q))
            new_btn = gr.Button("🗑️ New conversation", size="sm", variant="secondary")

        # --- Right: the evidence behind the answer ---
        with gr.Column(scale=5):
            chart = gr.Plot(label="Chart — shown only when it helps")
            with gr.Accordion("Result table", open=True):
                table = gr.Dataframe(interactive=False)
            with gr.Accordion("SQL the agent ran", open=False):
                sql_box = gr.Code(language="sql")
            with gr.Accordion("How the agent got there", open=False):
                trace_md = gr.Markdown()

    inputs = [question, chatbot, thread]
    outputs = [question, chatbot, sql_box, table, chart, trace_md, thread]
    ask_btn.click(respond, inputs, outputs)
    question.submit(respond, inputs, outputs)
    for btn, q in example_btns:
        btn.click(lambda q=q: q, None, question).then(respond, inputs, outputs)
    new_btn.click(reset, None, outputs)


if __name__ == "__main__":
    llm.verify_tool_calling()  # fail loudly before serving anything
    demo.launch(server_name="127.0.0.1", share=False,
                theme=gr.themes.Soft(primary_hue="emerald"), css=CSS)
