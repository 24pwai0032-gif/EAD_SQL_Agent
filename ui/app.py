"""AI Analytics Chat for the EAD Foreign Economic Assistance agent.

A branded, chat-widget-style analytics assistant (the "atombot" look): a dark
navy conversation with a gradient header, rounded bubbles, a green-dot assistant
avatar, a circular send button and a "Powered by atomcamp AI" footer. It is fully
responsive — a centred column on desktop, full width on mobile — and dark by
default with a light/dark toggle in the header.

Under that skin it is a real analytics assistant: each AI turn renders as one
bubble containing the prose answer, an embedded chart and (for small results)
an inline table, followed by a collapsed "Details" section with the full
table, the exact SQL and the agent trace.

LOCAL / ON-PREMISE ONLY, AIR-GAPPED. The page pulls NOTHING from the public
internet: no CDN, no external stylesheets, no web fonts (the theme font is forced
to a system stack — Gradio's default themes would otherwise fetch Google Fonts),
no Plotly. Charts are STATIC matplotlib PNGs (Agg backend) embedded in the message
with Gradio's native expand + download; interactive JS charts are not possible
here. Gradio telemetry is switched off, and every icon is an inline SVG.

share=False, always: on a government network share=True would open a public
tunnel out to gradio.live — a security incident, not a convenience.

The agent is consumed through ONE contract, agent.graph.ask(), returning
{"answer", "sql", "columns", "rows", "chart", "trace"}. `chart` is a spec (or
None) rendered here; the SQL is never re-executed.
"""
import html
import sys
import tempfile
import traceback
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")  # headless: no display server, and never a JS/CDN renderer
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import gradio as gr
import pandas as pd

from agent import llm
from agent.graph import ask

# --- Palette (chart-side constants) --------------------------------------
GREEN = "#15803d"      # primary accent — chart marks (brand green)
GREEN_2 = "#5c9e7a"    # secondary green — 2nd series of a grouped bar only
INK = "#1a1a1a"        # chart titles
INK_2 = "#374151"      # axis titles
GRAY = "#6b7280"       # tick labels
GRID = "#eceff2"       # hairline y-gridlines
BASELINE = "#d1d5db"   # x-axis baseline

# System font stack ONLY. Plain strings -> Gradio uses them verbatim and emits no
# @font-face / no fonts.googleapis.com request (see gradio themes/base.py).
SYSTEM_FONT = ["system-ui", "-apple-system", "BlinkMacSystemFont",
               '"Segoe UI"', "Roboto", '"Helvetica Neue"', "Arial", "sans-serif"]
SYSTEM_MONO = ["ui-monospace", "SFMono-Regular", "Menlo", '"Cascadia Mono"',
               "Consolas", '"Liberation Mono"', "monospace"]

# Example prompts (EAD domain) shown as chips under the composer.
EXAMPLES = [
    "Disbursements by fiscal year",
    "Top 5 partners by disbursements",
    "Debt service trend",
    "Commitments by sector in FY2025",
]

# Chart PNGs and the avatar live here and are served by Gradio (allowed_paths).
CHART_DIR = Path(tempfile.mkdtemp(prefix="ead_charts_"))

# Shown only when no model is configured at all (has_credentials() is False).
CONFIG_ERROR_MD = ("**The analysis service is not configured.**\n\nNo model is set "
                   "up. Please add a valid model configuration to `.env`, then "
                   "restart the application.")
# Shown when the agent raised at runtime (config IS present) — the real traceback
# is printed to the server console, never blamed on missing config.
RUNTIME_ERROR_MD = ("**Something went wrong while analysing your question.**\n\n"
                    "Please try asking again in a moment. If it keeps happening, the "
                    "model service may be busy or unreachable.")


def _make_bot_avatar():
    """A small green dot avatar for the assistant — generated locally (no external
    image), matching the reference widget."""
    fig, ax = plt.subplots(figsize=(0.64, 0.64), dpi=120)
    ax.add_patch(plt.Circle((0.5, 0.5), 0.44, color="#22c55e"))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.axis("off")
    path = CHART_DIR / "bot_avatar.png"
    fig.savefig(path, transparent=True, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    return str(path)


BOT_AVATAR = _make_bot_avatar()


def _stamp() -> str:
    """A subtle message timestamp, e.g. '4:02 PM'."""
    t = datetime.now().strftime("%I:%M %p").lstrip("0")
    return f"<div style='font-size:11px;color:#8b98a5;margin-top:6px'>{t}</div>"


def _message_text(content) -> str:
    """Extract the plain question text from a chat message's content.

    Gradio normalises message content on the client<->server round-trip, so by
    the time bot() sees it the user's message may be a plain string, a
    {'text':..., 'type':'text'} dict, or a list of those — not the string we
    stored. Return the first text fragment either way (never a dict, which the
    agent would reject as an invalid message).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text", "")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str) and item.strip():
                return item
            if isinstance(item, dict) and item.get("type", "text") == "text" and item.get("text"):
                return item["text"]
    return str(content or "")


# --- Formatting helpers --------------------------------------------------
def _axis_label(col: str) -> str:
    """Plain-English axis title: 'amount_usd_mn' -> 'Amount (USD mn)'."""
    raw = str(col)
    words = [w for w in raw.replace("_", " ").split() if w.lower() not in {"usd", "pkr", "mn"}]
    label = " ".join(w[:1].upper() + w[1:] for w in words) if words else raw
    low = raw.lower()
    if "usd" in low:
        label += " (USD mn)"
    elif "pkr" in low:
        label += " (PKR mn)"
    return label


def _group_ylabel(y_cols: list) -> str:
    lows = [c.lower() for c in y_cols]
    if all("usd" in c for c in lows):
        return "USD mn"
    if all("pkr" in c for c in lows):
        return "PKR mn"
    return _axis_label(y_cols[0])


# --- Result table --------------------------------------------------------
def format_table(columns, rows):
    df = pd.DataFrame(rows, columns=columns)
    for col in df.columns:
        if pd.api.types.is_float_dtype(df[col]):
            df[col] = df[col].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "")
        elif pd.api.types.is_integer_dtype(df[col]):
            df[col] = df[col].map(lambda v: f"{v:,}" if pd.notna(v) else "")
    return df


def table_markdown(columns, rows, max_rows=12):
    if not rows or not columns:
        return None
    df = format_table(columns, rows).head(max_rows)
    head = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "| " + " | ".join("---" for _ in df.columns) + " |"
    body = "\n".join("| " + " | ".join(str(v) for v in row) + " |"
                     for row in df.itertuples(index=False))
    md = "\n".join([head, sep, body])
    if len(rows) > max_rows:
        md += f"\n\n_Showing {max_rows} of {len(rows):,} rows — full table in Details._"
    return md


def details_markdown(sql, trace, columns, rows, table_shown_inline):
    parts = []
    if rows and not table_shown_inline:
        parts.append("**Result table**\n\n" + table_markdown(columns, rows, max_rows=200))
    parts.append("**SQL executed**\n\n```sql\n" + (sql or "-- no query executed") + "\n```")
    if trace:
        parts.append("**Agent steps**\n\n```text\n" + "\n".join(trace) + "\n```")
    return "\n\n".join(parts)


# --- Chart rendering (static PNG) ----------------------------------------
def _style_axes(ax, vmax: float):
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.yaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    ax.tick_params(colors=GRAY, length=0, labelsize=9)
    decimals = 1 if 0 < vmax < 10 else 0
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.{decimals}f}"))


def _value_labels(ax, bars, values):
    numeric = [v for v in values if v is not None]
    if len(bars) > 12 or not numeric:
        return
    decimals = 0 if max(abs(v) for v in numeric) >= 100 else 1
    labels = [f"{v:,.{decimals}f}" if v is not None else "" for v in values]
    ax.bar_label(bars, labels=labels, padding=3, color=INK_2, fontsize=8.5)


def _build_figure(chart, columns, rows):
    if not chart or not rows:
        return None
    df = pd.DataFrame(rows, columns=columns)
    ctype, x, title = chart["type"], chart["x"], chart["title"]
    fig, ax = plt.subplots(figsize=(6.8, 4.0))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    if ctype == "bar":
        values = df[chart["y"]]
        bars = ax.bar(df[x].astype(str), values, color=GREEN, width=0.68, zorder=3)
        ax.set_ylabel(_axis_label(chart["y"]), color=INK_2, fontsize=10)
        _style_axes(ax, max((abs(v) for v in values if v is not None), default=0))
        _value_labels(ax, bars, list(values))

    elif ctype == "line":
        values = df[chart["y"]]
        xs = list(range(len(df)))
        ax.fill_between(xs, list(values), color=GREEN, alpha=0.10, zorder=2)
        ax.plot(xs, values, color=GREEN, linewidth=2.2, marker="o", markersize=6,
                markerfacecolor=GREEN, markeredgecolor="white", zorder=3)
        ax.set_xticks(xs)
        ax.set_xticklabels(df[x].astype(str))
        ax.set_ylabel(_axis_label(chart["y"]), color=INK_2, fontsize=10)
        _style_axes(ax, max((abs(v) for v in values if v is not None), default=0))

    elif ctype == "pie":
        values = df[chart["y"]]
        n = len(df)
        cmap = plt.get_cmap("Greens")
        colors = [cmap(0.45 + 0.4 * (i / max(n - 1, 1))) for i in range(n)]
        _, _, autotexts = ax.pie(
            values, labels=[str(v) for v in df[x]], autopct="%1.1f%%",
            colors=colors, startangle=90, counterclock=False,
            wedgeprops={"edgecolor": "white", "linewidth": 2},
            textprops={"color": INK, "fontsize": 9})
        plt.setp(autotexts, color="white", fontweight="bold")
        ax.set_title(title, loc="center", color=INK, fontweight="bold", pad=14, fontsize=12)
        fig.tight_layout()
        return fig

    elif ctype == "grouped_bar":
        y_cols = [c.strip() for c in chart["y"].split(",")]
        n, k = len(df), len(y_cols)
        width = 0.8 / k
        vmax = 0.0
        containers = []
        for i, y in enumerate(y_cols):
            positions = [j + i * width for j in range(n)]
            bars = ax.bar(positions, df[y], width=width * 0.9, label=_axis_label(y),
                          color=GREEN if i == 0 else GREEN_2, zorder=3)
            containers.append((bars, list(df[y])))
            vmax = max(vmax, max((abs(v) for v in df[y] if v is not None), default=0))
        ax.set_xticks([j + width * (k - 1) / 2 for j in range(n)])
        ax.set_xticklabels(df[x].astype(str))
        ax.set_ylabel(_group_ylabel(y_cols), color=INK_2, fontsize=10)
        ax.legend(frameon=False, fontsize=9, labelcolor=INK_2)
        _style_axes(ax, vmax)
        if n * k <= 12:
            for bars, values in containers:
                _value_labels(ax, bars, values)

    ax.set_title(title, loc="left", color=INK, fontweight="bold", pad=12, fontsize=12)
    if len(df) > 6:
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    fig.tight_layout()
    return fig


def render_chart_png(chart, columns, rows):
    try:
        fig = _build_figure(chart, columns, rows)
    except Exception:
        return None
    if fig is None:
        return None
    path = CHART_DIR / f"chart_{uuid.uuid4().hex}.png"
    fig.savefig(path, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(path)


# --- Turning a result into chat messages ---------------------------------
def build_ai_messages(result):
    columns, rows = result["columns"], result["rows"]
    answer = result["answer"] or "_No answer was returned for this question._"
    if not rows:
        answer += "\n\n_No records matched this query._"

    chart_path = render_chart_png(result["chart"], columns, rows)
    inline_table = table_markdown(columns, rows, max_rows=12) if rows else None

    content = [answer]
    if chart_path:
        content.append({"path": chart_path})
    if inline_table:
        content.append("**Result**\n\n" + inline_table)
    content.append(_stamp())

    messages = [{"role": "assistant", "content": content}]
    details = details_markdown(result["sql"], result["trace"], columns, rows,
                               table_shown_inline=inline_table is not None)
    messages.append({
        "role": "assistant",
        "content": details,
        "metadata": {"title": "Details — SQL & agent steps", "status": "done"},
    })
    return messages


def welcome_messages():
    intro = ("**Thanks for reaching out!** I can help you analyze Foreign Economic "
             "Assistance — commitments, disbursements and debt service. Ask me "
             "anything, or tap an example below to get started.")
    return [{"role": "assistant", "content": [intro, _stamp()]}]


# --- Handlers ------------------------------------------------------------
def add_user(question, chat):
    """Append the user's message and clear the box. No-op on empty input.
    Outputs: [question, chatbot, send_btn]."""
    q = (question or "").strip()
    if not q:
        return gr.update(), chat, gr.update()
    user_msg = {"role": "user", "content": q}
    return "", (chat or []) + [user_msg], gr.update(interactive=False)


def bot(chat, thread_id):
    """Run the agent for the latest user message, streaming a loading state
    first. Generator. Outputs: [chatbot, send_btn]."""
    if not chat or chat[-1]["role"] != "user":
        yield chat, gr.update(interactive=True)
        return
    question = _message_text(chat[-1]["content"])
    yield chat + [{"role": "assistant", "content": "_Analysing your question…_"}], \
        gr.update(interactive=False)

    if not llm.has_credentials():
        messages = [{"role": "assistant", "content": CONFIG_ERROR_MD}]
    else:
        try:
            messages = build_ai_messages(ask(question, thread_id=thread_id))
        except Exception:
            # Surface the real cause to the server console (never to the user,
            # and never mislabelled as a config problem).
            print("[error] agent turn failed:", flush=True)
            traceback.print_exc()
            sys.stdout.flush()
            messages = [{"role": "assistant", "content": RUNTIME_ERROR_MD}]
    yield chat + messages, gr.update(interactive=True)


def new_chat():
    """Fresh conversation: new thread (fresh agent memory).
    Outputs: [chatbot, thread, send_btn]."""
    return welcome_messages(), str(uuid.uuid4()), gr.update(interactive=True)


# --- Theme + CSS ---------------------------------------------------------
THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.emerald,
    neutral_hue=gr.themes.colors.slate,
    font=SYSTEM_FONT,        # system stack -> no Google Fonts request
    font_mono=SYSTEM_MONO,
)

# Dark is the default look. Gradio scopes its dark vars to ':root.dark', so adding
# 'dark' to <html> switches Gradio's components AND our chrome together.
FORCE_DARK_JS = "() => { document.documentElement.classList.add('dark'); }"
THEME_TOGGLE_JS = "() => { document.documentElement.classList.toggle('dark'); }"

# A paper-plane, inline (air-gapped): no external icon fetch.
_PLANE = ("url(\"data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'"
          "%20viewBox='0%200%2024%2024'%20fill='white'%3E%3Cpath%20d='M2%2021l21-9L2"
          "%203v7l15%202-15%202z'/%3E%3C/svg%3E\")")

CSS = """
:root { --grad: linear-gradient(100deg,#219a57 0%,#22a89a 52%,#caa54b 100%);
        --page:#f5f6f7; --text:#1a1a1a; --muted:#6b7280;
        --bot:#eef4f1; --user:#15803d; }
:root.dark {
  /* our chrome */
  --page:#0a1826; --text:#e6edf3; --muted:#8b98a5; --bot:#13293d; --user:#166534;
  /* override Gradio's dark greys with navy (our CSS is injected after the theme) */
  --body-background-fill:#0a1826; --background-fill-primary:#0d2033;
  --background-fill-secondary:#0d2033; --block-background-fill:#0d2033;
  --block-border-color:#183350; --border-color-primary:#183350;
  --input-background-fill:#0c1d2e; --input-border-color:#1c3a56;
  --body-text-color:#e6edf3; --body-text-color-subdued:#8b98a5;
  --button-secondary-background-fill:#12283d; --button-secondary-text-color:#e6edf3;
}

gradio-app, body { background: var(--page) !important; }
/* Fluid column: comfortable reading width on desktop, edge-to-edge on phones. */
.gradio-container { width: 100% !important; max-width: 820px !important;
    margin: 0 auto !important; background: var(--page) !important;
    padding: clamp(6px, 2vw, 14px) clamp(6px, 2.5vw, 14px) 8px !important; }

/* Header (gradient) */
#appheader { background: var(--grad) !important; border-radius: 14px !important;
    padding: 13px 18px !important; align-items: center !important;
    justify-content: space-between !important; flex-wrap: nowrap !important; gap: 10px; }
.brand-name { font-size: 19px; font-weight: 800; color: #ffffff; line-height: 1.1; }
.brand-status { font-size: 12.5px; color: rgba(255,255,255,.92); display: flex;
    align-items: center; gap: 6px; margin-top: 3px; }
.online-dot { width: 8px; height: 8px; border-radius: 50%; background: #4ade80;
    box-shadow: 0 0 0 3px rgba(74,222,128,.25); }
#header-actions { flex: 0 0 auto !important; gap: 10px !important; align-items: center !important;
    flex-wrap: nowrap !important; min-width: 0 !important; }
#theme-toggle { min-width: 46px !important; width: 46px !important; height: 26px !important;
    padding: 0 !important; border-radius: 999px !important; position: relative;
    background: rgba(0,0,0,.22) !important; border: 1px solid rgba(255,255,255,.4) !important;
    color: transparent !important; font-size: 0 !important; box-shadow: none !important; }
#theme-toggle::before { content: ''; position: absolute; top: 2px; left: 2px; width: 20px;
    height: 20px; border-radius: 50%; background: #fff; transition: left .2s ease; }
:root:not(.dark) #theme-toggle::before { left: 22px; }
#newchat-btn { min-width: 34px !important; width: 34px !important; height: 34px !important;
    padding: 0 !important; border-radius: 50% !important; font-size: 18px !important;
    line-height: 1 !important; color: #fff !important; background: rgba(255,255,255,.16) !important;
    border: 1px solid rgba(255,255,255,.3) !important; box-shadow: none !important; }

/* Chat surface. dvh (not vh) so mobile browser toolbars don't hide the composer. */
#chatbox { height: min(64dvh, 640px) !important;
    border: 1px solid var(--block-border-color) !important;
    border-radius: 14px !important; margin-top: 10px !important; }

/* Charts and any image inside a bubble scale with the bubble, never overflow. */
#chatbox .message img, #chatbox img { max-width: 100% !important; height: auto !important;
    border-radius: 10px; }
/* Answer + chart share one bubble: give the pieces breathing room. */
#chatbox .message-content > * + * { margin-top: 8px; }
/* Wide result tables scroll inside the bubble instead of breaking the layout. */
#chatbox .message table { display: block; max-width: 100%; overflow-x: auto;
    white-space: nowrap; font-size: 12.5px; }
#chatbox .message th, #chatbox .message td { padding: 4px 10px; }
/* Long refs/SQL wrap instead of stretching bubbles off-screen. */
#chatbox .message { overflow-wrap: anywhere; }
#chatbox .message pre { max-width: 100%; overflow-x: auto; }

/* Composer */
#composer { gap: 10px !important; align-items: center !important; margin-top: 10px !important; }
#composer-input textarea, #composer-input input { border-radius: 22px !important;
    padding: 12px 18px !important; }
#send-btn { min-width: 46px !important; width: 46px !important; height: 46px !important;
    padding: 0 !important; border-radius: 50% !important; border: none !important;
    background-color: #22c55e !important; background-image: """ + _PLANE + """ !important;
    background-repeat: no-repeat !important; background-position: center !important;
    background-size: 20px !important; color: transparent !important; font-size: 0 !important; }
#send-btn:hover { background-color: #16a34a !important; }

/* Example chips */
#chips { gap: 6px !important; margin-top: 8px !important; flex-wrap: wrap !important; }
#chips button { font-size: 12px !important; font-weight: 500 !important;
    border-radius: 999px !important; }

/* Footer */
#powered { text-align: center; font-size: 11.5px; color: var(--muted); margin: 10px 0 4px; }
#powered b { color: #22a89a; }

/* --- Responsive breakpoints ------------------------------------------- */
/* Tablet: slightly tighter chrome, chat gets more of the screen. */
@media (max-width: 900px) {
    #chatbox { height: min(62dvh, 600px) !important; }
}
/* Phone: full-width column, larger tap targets, chips scroll horizontally. */
@media (max-width: 640px) {
    .gradio-container { padding: 6px 6px 4px !important; }
    #appheader { padding: 10px 12px !important; border-radius: 12px !important; }
    .brand-name { font-size: 16.5px; }
    .brand-status { font-size: 11.5px; }
    #chatbox { height: calc(100dvh - 258px) !important; min-height: 320px;
        border-radius: 12px !important; }
    /* 16px stops iOS Safari from auto-zooming the page on focus. */
    #composer-input textarea, #composer-input input { font-size: 16px !important;
        padding: 10px 14px !important; }
    #send-btn { min-width: 44px !important; width: 44px !important; height: 44px !important; }
    #chips { flex-wrap: nowrap !important; overflow-x: auto !important;
        scrollbar-width: none; padding-bottom: 2px !important; }
    #chips::-webkit-scrollbar { display: none; }
    #chips button { flex: 0 0 auto !important; }
}
/* Small phones: compact header, footer trimmed. */
@media (max-width: 400px) {
    .brand-name { font-size: 15px; }
    #theme-toggle { min-width: 40px !important; width: 40px !important; }
    #powered { font-size: 10.5px; }
}
"""


def status_online() -> str:
    ready = llm.has_credentials()
    label = "Online" if ready else "Model not configured"
    color = "#4ade80" if ready else "#f87171"
    return ("<div class='brand'><div class='brand-name'>atombot</div>"
            f"<div class='brand-status'><span class='online-dot' style='background:{color}'></span>"
            f"{html.escape(label)}</div></div>")


# --- Page ----------------------------------------------------------------
# analytics_enabled=False: no outbound Gradio telemetry from an air-gapped host.
with gr.Blocks(title="atombot — EAD Analytics", analytics_enabled=False) as demo:
    thread = gr.State(lambda: str(uuid.uuid4()))

    with gr.Row(elem_id="appheader"):
        gr.HTML(status_online())
        with gr.Row(elem_id="header-actions"):
            theme_btn = gr.Button("theme", elem_id="theme-toggle")
            newchat_btn = gr.Button("+", elem_id="newchat-btn")

    chatbot = gr.Chatbot(
        value=welcome_messages(), elem_id="chatbox", show_label=False,
        autoscroll=True, sanitize_html=False, render_markdown=True, allow_tags=True,
        group_consecutive_messages=False, avatar_images=(None, BOT_AVATAR),
        placeholder="Ask me anything…",
    )
    with gr.Row(elem_id="composer"):
        question = gr.Textbox(
            placeholder="Ask me anything…", show_label=False, container=False,
            scale=8, autofocus=True, max_lines=4, elem_id="composer-input")
        send_btn = gr.Button("Send", scale=1, min_width=46, elem_id="send-btn")
    with gr.Row(elem_id="chips"):
        example_btns = [gr.Button(ex, size="sm") for ex in EXAMPLES]
    gr.HTML("<div id='powered'>Powered by <b>atomcamp AI</b></div>")

    # Wiring: add the user's message, then run the agent (with a loading state).
    turn_in = [question, chatbot]
    turn_out = [question, chatbot, send_btn]
    question.submit(add_user, turn_in, turn_out).then(bot, [chatbot, thread], [chatbot, send_btn])
    send_btn.click(add_user, turn_in, turn_out).then(bot, [chatbot, thread], [chatbot, send_btn])
    for btn in example_btns:
        (btn.click(lambda ex=btn.value: ex, None, question)
            .then(add_user, turn_in, turn_out)
            .then(bot, [chatbot, thread], [chatbot, send_btn]))
    newchat_btn.click(new_chat, None, [chatbot, thread, send_btn])
    theme_btn.click(None, None, None, js=THEME_TOGGLE_JS)
    demo.load(None, None, None, js=FORCE_DARK_JS)  # dark by default


if __name__ == "__main__":
    if llm.has_credentials():
        try:
            llm.verify_tool_calling()
        except Exception as exc:
            print(f"[warning] Model tool-calling check failed: {exc}")
    else:
        print("[warning] No model configured — the chat will open, but questions "
              "cannot be answered until .env is set up.")
    # server_name/share are the security-critical binding and stay exactly as
    # required. theme/css/allowed_paths ride here (Gradio 6 applies theme/css at
    # launch; allowed_paths serves the embedded chart PNGs and the avatar).
    demo.launch(server_name="127.0.0.1", share=False,
                theme=THEME, css=CSS, allowed_paths=[str(CHART_DIR)])
