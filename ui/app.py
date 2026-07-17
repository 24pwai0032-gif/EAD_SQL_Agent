"""Gradio UI — LOCAL TESTING ONLY.

share=False always: on a government network share=True opens a tunnel out to
gradio.live — that is a security incident, not a convenience.

Rendering happens HERE, in the client, from the chart *specification* the
agent returns. The API never ships a rendered image.
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


def render_chart(chart: dict | None, columns: list, rows: list):
    if not chart or not rows:
        return None
    df = pd.DataFrame(rows, columns=columns)
    x, title = chart["x"], chart["title"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if chart["type"] == "bar":
        ax.bar(df[x].astype(str), df[chart["y"]])
        ax.set_ylabel(chart["y"])
    elif chart["type"] == "line":
        ax.plot(df[x].astype(str), df[chart["y"]], marker="o")
        ax.set_ylabel(chart["y"])
    elif chart["type"] == "pie":
        ax.pie(df[chart["y"]], labels=df[x].astype(str), autopct="%1.1f%%")
    elif chart["type"] == "grouped_bar":
        y_cols = [c.strip() for c in chart["y"].split(",")]
        n = len(df)
        width = 0.8 / len(y_cols)
        for i, y in enumerate(y_cols):
            ax.bar([j + i * width for j in range(n)], df[y], width=width, label=y)
        ax.set_xticks([j + 0.4 - width / 2 for j in range(n)])
        ax.set_xticklabels(df[x].astype(str))
        ax.legend()
    ax.set_title(title)
    if chart["type"] != "pie":
        plt.xticks(rotation=45, ha="right")
    fig.tight_layout()
    return fig


def respond(question, thread_id):
    if not question.strip():
        return "", "", None, None, thread_id
    result = ask(question, thread_id=thread_id)
    sql = result["sql"] or "-- no query executed"
    table = (
        pd.DataFrame(result["rows"], columns=result["columns"])
        if result["rows"] else None
    )
    fig = render_chart(result["chart"], result["columns"], result["rows"])
    return result["answer"], sql, table, fig, thread_id


with gr.Blocks(title="EAD SQL Agent (local testing)") as demo:
    gr.Markdown("## EAD Foreign Economic Assistance — SQL Agent (local testing UI)")
    thread = gr.State(lambda: str(uuid.uuid4()))
    question = gr.Textbox(label="Question", placeholder="e.g. Total debt service in FY2025?")
    ask_btn = gr.Button("Ask", variant="primary")
    answer = gr.Markdown(label="Answer")
    sql_box = gr.Code(label="SQL", language="sql")
    table = gr.Dataframe(label="Result table", interactive=False)
    chart = gr.Plot(label="Chart (only when it helps)")
    ask_btn.click(respond, [question, thread], [answer, sql_box, table, chart, thread])
    question.submit(respond, [question, thread], [answer, sql_box, table, chart, thread])


if __name__ == "__main__":
    llm.verify_tool_calling()  # fail loudly before serving anything
    demo.launch(server_name="127.0.0.1", share=False)
