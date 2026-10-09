from __future__ import annotations

import base64
import html
import itertools
import mimetypes
import os
import tkinter as tk
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from tkinter import filedialog

import streamlit as st

from agent import ask_agent
from status import Status
from visuals import context_text, render_rich_markdown


LOGO_PATH: Path | None = Path(__file__).resolve().parent / "logo.png"
LOGO_MAX_BYTES = 4 * 1024 * 1024
RESPONSE_CANVAS_MAX_HEIGHT = 520
MAX_FILE_ROWS = 3000
NEW_CONVERSATION_ID = "__new_conversation__"
CONTEXT_MESSAGE_LIMIT = 8
CONTEXT_MESSAGE_CHARS = 3000

_RENDER_PASSES = itertools.count(1)

IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".turbo",
    ".vscode",
    "__pycache__",
    ".mypy_cache",
    "bower_components",
    "build",
    "coverage",
    "dist",
    "env",
    "node_modules",
    "target",
    "venv",
    ".venv",
}
IGNORED_FILES = {".ds_store", "desktop.ini", "thumbs.db"}


def _streamlit_version() -> tuple[int, int]:
    try:
        major, minor = st.__version__.split(".")[:2]
        return int(major), int(minor)
    except (AttributeError, ValueError):
        return (0, 0)


STRETCH: dict[str, object] = (
    {"width": "stretch"}
    if _streamlit_version() >= (1, 50)
    else {"use_container_width": True}
)


st.set_page_config(
    page_title="CodeTerrain",
    page_icon="⌘",
    layout="wide",
    initial_sidebar_state="expanded",
)


def load_css(name: str = "style.css") -> None:
    try:
        css = (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")
    except OSError:
        st.warning(f"{name} was not found. Put it in the same folder as UI.py.")
        return
    canvas = f":root{{--canvas-max-height:{RESPONSE_CANVAS_MAX_HEIGHT}px}}"
    st.markdown(f"<style>{css}\n{canvas}</style>", unsafe_allow_html=True)


@contextmanager
def card(key: str | None = None):
    options: dict[str, object] = {"border": True}
    if key:
        options["key"] = key
    with st.container(**options):
        st.markdown('<div class="card-marker"></div>', unsafe_allow_html=True)
        yield


load_css()


def initialize_state() -> None:
    defaults = {
        "project_root": None,
        "project_files": [],
        "scan_warning_count": 0,
        "conversations": [],
        "history_picker": NEW_CONVERSATION_ID,
        "editing_answer": None,
        "renaming_conversation": None,
        "pending_history_picker": None,
        "status": {
            "step": "Idle",
            "process": "Select a project folder",
            "state": "idle",
        },
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    pending_history_picker = st.session_state.pop("pending_history_picker", None)
    if pending_history_picker:
        st.session_state.history_picker = pending_history_picker

    legacy_messages = st.session_state.pop("messages", None)
    latest_question = st.session_state.pop("latest_question", "")
    latest_answer = st.session_state.pop("latest_answer", "")
    if not st.session_state.conversations:
        if isinstance(legacy_messages, list):
            messages = [
                {"role": message.get("role"), "content": str(message.get("content", ""))}
                for message in legacy_messages
                if isinstance(message, dict)
                and message.get("role") in {"user", "assistant"}
            ]
        else:
            messages = []
            if latest_question:
                messages.append({"role": "user", "content": str(latest_question)})
            if latest_answer:
                messages.append({"role": "assistant", "content": str(latest_answer)})

        if messages:
            conversation = create_conversation(
                st.session_state.project_root,
                messages,
            )
            st.session_state.conversations = [conversation]
            st.session_state.history_picker = conversation["id"]

    st.session_state.pop("project_name", None)
    st.session_state.pop("brand_logo", None)


def conversation_title(messages: list[dict]) -> str:
    first_question = next(
        (
            " ".join(str(item.get("content", "")).split())
            for item in messages
            if item.get("role") == "user"
        ),
        "New conversation",
    )
    if len(first_question) > 44:
        return first_question[:41].rstrip() + "…"
    return first_question or "New conversation"


def create_conversation(
    project_root: str | None,
    messages: list[dict] | None = None,
) -> dict:
    messages = messages or []
    return {
        "id": uuid.uuid4().hex,
        "title": conversation_title(messages),
        "project_root": project_root,
        "messages": messages,
        "updated_at": time.time(),
    }


def project_conversations(project_root: str | None) -> list[dict]:
    return sorted(
        (
            conversation
            for conversation in st.session_state.conversations
            if conversation.get("project_root") == project_root
        ),
        key=lambda conversation: conversation.get("updated_at", 0),
        reverse=True,
    )


def conversation_by_id(conversation_id: str | None) -> dict | None:
    return next(
        (
            conversation
            for conversation in st.session_state.conversations
            if conversation.get("id") == conversation_id
        ),
        None,
    )


def delete_conversation(conversation_id: str) -> None:
    st.session_state.conversations = [
        conversation
        for conversation in st.session_state.conversations
        if conversation.get("id") != conversation_id
    ]
    editing_answer = st.session_state.get("editing_answer")
    if editing_answer and editing_answer[0] == conversation_id:
        st.session_state.editing_answer = None
    if st.session_state.get("renaming_conversation") == conversation_id:
        st.session_state.renaming_conversation = None
    if st.session_state.history_picker == conversation_id:
        st.session_state.history_picker = NEW_CONVERSATION_ID


def select_conversation(conversation_id: str) -> None:
    st.session_state.history_picker = conversation_id
    st.session_state.editing_answer = None
    st.session_state.renaming_conversation = None


def start_new_conversation() -> None:
    st.session_state.history_picker = NEW_CONVERSATION_ID
    st.session_state.editing_answer = None
    st.session_state.renaming_conversation = None


def start_conversation_rename(conversation_id: str) -> None:
    st.session_state.renaming_conversation = conversation_id


def cancel_conversation_rename(conversation_id: str) -> None:
    st.session_state.pop(f"rename-title-{conversation_id}", None)
    st.session_state.renaming_conversation = None


def rename_conversation(conversation_id: str) -> None:
    title_key = f"rename-title-{conversation_id}"
    new_title = " ".join(
        st.session_state.get(title_key, "").split()
    )
    if not new_title:
        return
    for conversation in st.session_state.conversations:
        if conversation.get("id") == conversation_id:
            conversation["title"] = new_title[:80]
            conversation["updated_at"] = time.time()
            break
    st.session_state.conversations = list(st.session_state.conversations)
    st.session_state.pop(title_key, None)
    st.session_state.renaming_conversation = None


def start_answer_edit(conversation_id: str, message_index: int) -> None:
    st.session_state.editing_answer = (conversation_id, message_index)


def cancel_answer_edit(editor_key: str) -> None:
    st.session_state.pop(editor_key, None)
    st.session_state.editing_answer = None


def save_answer_edit(
    conversation_id: str,
    message_index: int,
    editor_key: str,
) -> None:
    conversation = conversation_by_id(conversation_id)
    edited_answer = st.session_state.get(editor_key)
    if (
        conversation
        and isinstance(edited_answer, str)
        and edited_answer.strip()
        and 0 <= message_index < len(conversation.get("messages", []))
        and conversation["messages"][message_index].get("role") == "assistant"
    ):
        conversation["messages"][message_index]["content"] = edited_answer
        conversation["messages"][message_index].pop("error", None)
        conversation["updated_at"] = time.time()
        st.session_state.conversations = list(st.session_state.conversations)
    st.session_state.pop(editor_key, None)
    st.session_state.editing_answer = None


def agent_question_with_context(messages: list[dict], question: str) -> str:
    previous_messages = messages[-CONTEXT_MESSAGE_LIMIT:]
    if not previous_messages:
        return question

    context = []
    for message in previous_messages:
        role = "User" if message.get("role") == "user" else "Assistant"
        content = str(message.get("content", "")).strip()
        if message.get("role") != "user":
            content = context_text(content)
        if len(content) > CONTEXT_MESSAGE_CHARS:
            content = content[:CONTEXT_MESSAGE_CHARS].rstrip() + "… [truncated]"
        context.append(f"{role}: {content}")

    return (
        "Continue this CodeTerrain conversation. Use the previous turns as context, "
        "but verify codebase-specific details from the selected project.\n\n"
        "Previous turns:\n"
        + "\n\n".join(context)
        + "\n\nCurrent user question:\n"
        + question
    )


initialize_state()


def select_folder() -> str:
    root = tk.Tk()
    try:
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        icon = Path(__file__).resolve().parent / "idk.ico"
        if icon.exists():
            try:
                root.iconbitmap(str(icon))
            except Exception:
                pass

        root.update_idletasks()
        return filedialog.askdirectory(
            parent=root,
            title="Select CodeTerrain Project",
        )
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def scan_project(folder: str) -> tuple[list[Path], int]:
    root = Path(folder).expanduser().resolve()
    files: list[Path] = []
    scan_errors = 0

    def record_scan_error(_error: OSError) -> None:
        nonlocal scan_errors
        scan_errors += 1

    for current, directories, filenames in os.walk(
        root,
        topdown=True,
        onerror=record_scan_error,
        followlinks=False,
    ):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if name.casefold() not in IGNORED_DIRECTORIES
            and not (current_path / name).is_symlink()
        ]

        for filename in filenames:
            if filename.casefold() in IGNORED_FILES:
                continue
            path = current_path / filename
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                scan_errors += 1
                continue
            files.append(path)

    return sorted(files, key=lambda item: str(item).casefold()), scan_errors


def markdown_files(files: list[Path]) -> list[Path]:
    return sorted(
        (path for path in files if path.suffix.casefold() == ".md"),
        key=lambda item: str(item).casefold(),
    )


def choose_project() -> None:
    try:
        folder = select_folder()
    except Exception as error:
        st.error(
            "Could not open the local folder picker. Run CodeTerrain on a "
            f"desktop session with Tk support. ({type(error).__name__}: {error})"
        )
        return

    if not folder:
        return

    try:
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            st.error("The selected path is not an available folder.")
            return
        if root == Path(root.anchor):
            st.error("Choose the project folder, not the root of the drive.")
            return

        with st.spinner("Indexing project files…"):
            files, warning_count = scan_project(str(root))

        st.session_state.project_root = str(root)
        st.session_state.project_files = files
        st.session_state.scan_warning_count = warning_count
        st.session_state.history_picker = NEW_CONVERSATION_ID
        st.session_state.status = {
            "step": "Project selected",
            "process": "Project ready",
            "state": "done",
        }
        st.rerun()
    except Exception as error:
        st.error(f"Could not load the project: {type(error).__name__}: {error}")


def refresh_project() -> None:
    project_root = st.session_state.project_root
    if not project_root or not Path(project_root).is_dir():
        st.error("The selected project folder is no longer available.")
        return
    try:
        with st.spinner("Refreshing project index…"):
            files, warning_count = scan_project(project_root)
        st.session_state.project_files = files
        st.session_state.scan_warning_count = warning_count
        st.session_state.status = {
            "step": "Project refreshed",
            "process": "Project file index updated",
            "state": "done",
        }
        st.rerun()
    except Exception as error:
        st.error(f"Could not refresh the project: {type(error).__name__}: {error}")


def reset_project() -> None:
    st.session_state.project_root = None
    st.session_state.project_files = []
    st.session_state.scan_warning_count = 0
    st.session_state.history_picker = NEW_CONVERSATION_ID
    st.session_state.status = {
        "step": "Idle",
        "process": "Select a project folder",
        "state": "idle",
    }


def logo_html() -> str:
    if LOGO_PATH is not None:
        path = Path(LOGO_PATH).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        try:
            mime = mimetypes.guess_type(path.name)[0]
            if (
                path.is_file()
                and mime in {"image/png", "image/jpeg", "image/webp"}
                and path.stat().st_size <= LOGO_MAX_BYTES
            ):
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return (
                    f'<img class="topbar-logo" src="data:{mime};base64,{encoded}" '
                    'alt="CodeTerrain logo">'
                )
        except OSError:
            pass
    return '<span class="topbar-mark" aria-hidden="true">⌘</span>'


def progress_for(status: dict) -> int:
    state = status.get("state", "idle")
    step = str(status.get("step") or "")
    if state in {"done", "failed"}:
        return 100
    if step == "Project setup":
        return 8
    if step.startswith("Agent step"):
        try:
            return min(92, 10 + int(step.split()[-1]) * 4)
        except (ValueError, IndexError):
            return 20
    if step.startswith("Tool:"):
        return 55
    if step == "Final response":
        return 95
    return 10


def status_html(step: object, process: object, state: str, progress: int) -> str:
    state_class = {
        "idle": "state-idle",
        "done": "state-done",
        "failed": "state-failed",
    }.get(state, "state-running")
    progress = max(0, min(100, int(progress)))
    return f"""
    <div class="analysis-state {state_class}">
      <div class="analysis-heading">
        <span class="analysis-dot"></span>
        <strong>{html.escape(str(step or "Working"))}</strong>
        <span class="analysis-percent">{progress}%</span>
      </div>
      <p>{html.escape(str(process or "Working…"))}</p>
      <div class="analysis-track"><i style="width:{progress}%"></i></div>
    </div>
    """


def render_sidebar_history(project_root: str) -> None:
    conversations = project_conversations(project_root)
    conversation_ids = {item["id"] for item in conversations}
    if st.session_state.history_picker not in conversation_ids | {NEW_CONVERSATION_ID}:
        st.session_state.history_picker = NEW_CONVERSATION_ID

    st.button(
        "＋ New chat",
        key="new-conversation",
        **STRETCH,
        on_click=start_new_conversation,
    )

    history = st.container(key="history-list")
    for conversation in conversations:
        conversation_id = conversation["id"]
        selected = st.session_state.history_picker == conversation_id
        with history.container(key=f"conversation-row-{conversation_id}"):
            title_col, menu_col = st.columns(
                [0.86, 0.14],
                gap="small",
                vertical_alignment="center",
            )
            with title_col:
                st.button(
                    conversation.get("title", "Conversation"),
                    key=f"select-conversation-{conversation_id}",
                    type="primary" if selected else "secondary",
                    **STRETCH,
                    help=conversation.get("title", "Conversation"),
                    on_click=select_conversation,
                    args=(conversation_id,),
                )
            with menu_col:
                with st.popover("⋮"):
                    if (
                        st.session_state.get("renaming_conversation")
                        == conversation_id
                    ):
                        st.text_input(
                            "Conversation name",
                            value=conversation.get("title", "Conversation"),
                            max_chars=80,
                            key=f"rename-title-{conversation_id}",
                        )
                        rename_col, cancel_col = st.columns(2, gap="small")
                        with rename_col:
                            st.button(
                                "Save",
                                key=f"rename-conversation-{conversation_id}",
                                **STRETCH,
                                on_click=rename_conversation,
                                args=(conversation_id,),
                            )
                        with cancel_col:
                            st.button(
                                "Cancel",
                                key=f"cancel-rename-conversation-{conversation_id}",
                                **STRETCH,
                                on_click=cancel_conversation_rename,
                                args=(conversation_id,),
                            )
                    else:
                        st.button(
                            "Rename",
                            key=f"start-rename-conversation-{conversation_id}",
                            **STRETCH,
                            on_click=start_conversation_rename,
                            args=(conversation_id,),
                        )
                        st.button(
                            "Delete",
                            key=f"delete-conversation-{conversation_id}",
                            **STRETCH,
                            on_click=delete_conversation,
                            args=(conversation_id,),
                        )


def render_answer_copy_button(answer: str) -> None:
    encoded_answer = base64.b64encode(answer.encode("utf-8")).decode("ascii")
    st.components.v1.html(
        f"""
        <meta name="color-scheme" content="dark">
        <style>
          html, body {{ height:100%; margin:0; overflow:hidden; background:#151922; }}
          button {{
            display:block; width:100%; height:100%; padding:0 .6rem; border:0;
            border-radius:8px; color:#e6e9ef; background:transparent;
            text-align:left; cursor:pointer;
            font:550 12.48px/1 Inter, system-ui, "Segoe UI", sans-serif;
            transition:background .12s, color .12s;
          }}
          button:hover, button:focus-visible {{ background:rgba(255,255,255,.06); outline:0; }}
          button.ok {{ color:#4cc9a4; }}
          button.fail {{ color:#ef7b88; }}
        </style>
        <button id="copy-answer" type="button">Copy answer</button>
        <script>
          const encoded = "{encoded_answer}";
          const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
          const answer = new TextDecoder().decode(bytes);
          const button = document.getElementById("copy-answer");
          let timer;
          function flash(text, cls) {{
            button.textContent = text;
            button.className = cls;
            clearTimeout(timer);
            timer = setTimeout(() => {{
              button.textContent = "Copy answer";
              button.className = "";
            }}, 1600);
          }}
          button.addEventListener("click", async () => {{
            try {{
              await navigator.clipboard.writeText(answer);
              flash("Copied \u2713", "ok");
            }} catch (error) {{
              const field = document.createElement("textarea");
              field.value = answer;
              field.style.position = "fixed";
              field.style.opacity = "0";
              document.body.appendChild(field);
              field.select();
              const copied = document.execCommand("copy");
              field.remove();
              flash(copied ? "Copied \u2713" : "Copy failed", copied ? "ok" : "fail");
            }}
          }});
        </script>
        """,
        height=29,
        scrolling=False,
    )


def render_conversation_messages(slot, conversation: dict | None) -> None:
    render_pass = next(_RENDER_PASSES)
    with slot.container():
        messages = conversation.get("messages", []) if conversation else []
        if not messages:
            st.markdown(
                """
                <div class="canvas-welcome">
                  <strong>Ask your codebase something</strong>
                  <p>Architecture, functions, dependencies, files.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            return

        turn_number = 0
        for message_index, message in enumerate(messages):
            if message.get("role") == "user":
                turn_number += 1
                if turn_number > 1:
                    st.markdown('<div class="turn-separator"></div>', unsafe_allow_html=True)
                st.markdown(
                    f"""
                    <div class="question-card">
                      <span>YOU</span>
                      <p>{html.escape(str(message.get("content", "")))}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            elif message.get("role") == "assistant":
                answer = str(message.get("content", ""))
                if message.get("error"):
                    st.error(answer)
                else:
                    conversation_id = conversation.get("id")
                    is_editing = st.session_state.get("editing_answer") == (
                        conversation_id,
                        message_index,
                    )
                    if is_editing:
                        suffix = f"{conversation_id}-{message_index}-r{render_pass}"
                        editor_key = f"answer-editor-{suffix}"
                        st.text_area(
                            "Edit answer",
                            value=answer,
                            key=editor_key,
                            height=220,
                            label_visibility="collapsed",
                        )
                        save_col, cancel_col = st.columns(2, gap="small")
                        with save_col:
                            st.button(
                                "Save changes",
                                key=f"save-answer-{suffix}",
                                type="primary",
                                **STRETCH,
                                on_click=save_answer_edit,
                                args=(conversation_id, message_index, editor_key),
                            )
                        with cancel_col:
                            st.button(
                                "Cancel",
                                key=f"cancel-answer-{suffix}",
                                **STRETCH,
                                on_click=cancel_answer_edit,
                                args=(editor_key,),
                            )
                    else:
                        answer_col, action_col = st.columns(
                            [1, 0.08],
                            gap="small",
                            vertical_alignment="top",
                        )
                        with answer_col:
                            render_rich_markdown(
                                answer,
                                key_prefix=(
                                    f"answer-{conversation_id}-"
                                    f"{message_index}-r{render_pass}"
                                ),
                            )
                        with action_col:
                            with st.popover("⋮"):
                                with st.container(
                                    key=(
                                        f"copy-answer-{conversation_id}-"
                                        f"{message_index}-r{render_pass}"
                                    )
                                ):
                                    render_answer_copy_button(answer)


def make_status_callback(output_slot):
    def on_change(current: dict) -> None:
        st.session_state.status = current
        output_slot.markdown(
            status_html(
                current.get("step"),
                current.get("process"),
                current.get("state", "running"),
                progress_for(current),
            ),
            unsafe_allow_html=True,
        )

    return on_change


def render_topbar() -> None:
    st.markdown(
        f"""
        <div class="topbar">
          {logo_html()}
          <span class="topbar-name">CodeTerrain</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


with st.sidebar:
    render_topbar()

    if st.session_state.project_root:
        root_path = str(st.session_state.project_root)
        root_name = Path(root_path).name or "Project"
        st.markdown(
            f"""
            <div class="side-label">Workspace</div>
            <div class="project-summary">
              <span class="project-pulse"></span>
              <strong title="{html.escape(root_name)}">{html.escape(root_name)}</strong>
            </div>
            <div class="project-path" title="{html.escape(root_path)}">{html.escape(root_path)}</div>
            """,
            unsafe_allow_html=True,
        )
        if st.session_state.scan_warning_count:
            st.warning(
                f"Skipped {st.session_state.scan_warning_count} unreadable item(s)."
            )
        refresh_col, change_col = st.columns(2, gap="small")
        refresh_clicked = refresh_col.button(
            "↻ Refresh",
            key="refresh-project",
            help="Re-scan the project files",
            **STRETCH,
        )
        change_clicked = change_col.button(
            "Change",
            key="change-project",
            help="Pick another project folder",
            **STRETCH,
        )
        if refresh_clicked:
            refresh_project()
        if change_clicked:
            reset_project()
            st.rerun()

        st.markdown('<div class="side-label">Chats</div>', unsafe_allow_html=True)
        render_sidebar_history(st.session_state.project_root)
    else:
        st.caption("Select a local folder to start exploring.")
        if st.button(
            "Select project folder",
            type="primary",
            **STRETCH,
            key="sidebar-select-project",
        ):
            choose_project()


project_root = st.session_state.project_root
project_files = st.session_state.project_files
active_conversation = conversation_by_id(st.session_state.history_picker)
if active_conversation and active_conversation.get("project_root") != project_root:
    active_conversation = None
project_name = (Path(project_root).name or "Project") if project_root else "Project"
total_files = len(project_files)
total_docs = sum(path.suffix.casefold() == ".md" for path in project_files)
total_folders = len(
    {
        str(path.relative_to(project_root).parent)
        for path in project_files
        if str(path.relative_to(project_root).parent) != "."
    }
) if project_root else 0


if project_root and not Path(project_root).is_dir():
    st.error("The selected project folder is no longer available.")
    if st.button("Clear unavailable project", type="primary"):
        reset_project()
        st.rerun()
    st.stop()


if not project_root:
    st.markdown(
        """
        <div class="eyebrow"><span></span> CODEBASE INTELLIGENCE</div>
        <div class="hero-title">Explore the terrain of your code.</div>
        <p class="tagline">Ask questions, map architecture and find details in your local project.</p>
        """,
        unsafe_allow_html=True,
    )
    with card():
        st.markdown(
            """
            <div class="welcome-steps">
              <span><b>01</b> Choose a folder</span>
              <span><b>02</b> Ask your codebase</span>
              <span><b>03</b> Browse docs and files</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(
            "Select project folder",
            type="primary",
            **STRETCH,
            key="main-select-project",
        ):
            choose_project()
    st.stop()


st.markdown(
    f"""
    <div class="page-head">
      <div class="page-title">{html.escape(project_name)}</div>
      <div class="chips">
        <span class="chip"><b>{total_files}</b> files</span>
        <span class="chip"><b>{total_docs}</b> docs</span>
        <span class="chip"><b>{total_folders}</b> folders</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


chat_tab, docs_tab, files_tab = st.tabs(["Chat", "Docs", "Files"])


with chat_tab:
    chat_title = (
        str(active_conversation.get("title") or "Conversation")
        if active_conversation
        else "New chat"
    )
    chat_label = "Active" if active_conversation else "New"

    with card(key="response-canvas"):
        st.markdown(
            f"""
            <div class="card-toolbar">
              <span class="card-title">{html.escape(chat_title)}</span>
              <span class="canvas-status"><i></i>{chat_label}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        conversation_slot = st.empty()
        status_slot = st.empty()
        render_conversation_messages(conversation_slot, active_conversation)
        if (
            active_conversation
            and active_conversation.get("messages")
            and active_conversation["messages"][-1].get("role") == "user"
        ):
            state = st.session_state.status
            status_slot.markdown(
                status_html(
                    state.get("step", "Starting analysis"),
                    state.get("process", "Preparing project context…"),
                    state.get("state", "running"),
                    progress_for(state),
                ),
                unsafe_allow_html=True,
            )

    question = st.chat_input(
        "Ask about architecture, functions, dependencies, files…",
        key="codebase-question",
    )

    if question and question.strip():
        question = question.strip()
        selected_id = st.session_state.history_picker
        conversation = (
            None
            if selected_id == NEW_CONVERSATION_ID
            else conversation_by_id(selected_id)
        )
        is_new_conversation = conversation is None
        if is_new_conversation:
            conversation = create_conversation(project_root)
            st.session_state.conversations.append(conversation)

        st.session_state.editing_answer = None
        previous_messages = list(conversation["messages"])
        conversation["messages"].append({"role": "user", "content": question})
        if len(conversation["messages"]) == 1:
            conversation["title"] = conversation_title(conversation["messages"])
        conversation["updated_at"] = time.time()
        st.session_state.conversations = list(st.session_state.conversations)

        st.session_state.status = {
            "step": "Starting analysis",
            "process": "Preparing project context…",
            "state": "running",
        }
        render_conversation_messages(conversation_slot, conversation)
        status_slot.markdown(
            status_html("Starting analysis", "Preparing project context…", "running", 0),
            unsafe_allow_html=True,
        )

        try:
            answer = ask_agent(
                question=agent_question_with_context(previous_messages, question),
                project_root=project_root,
                status=Status(on_change=make_status_callback(status_slot)),
            )
            if not answer or not answer.strip():
                answer = "The agent did not return a response."
            conversation["messages"].append(
                {"role": "assistant", "content": answer}
            )
        except Exception as error:
            error_message = f"{type(error).__name__}: {error}"
            conversation["messages"].append(
                {
                    "role": "assistant",
                    "content": f"Analysis failed — {error_message}",
                    "error": True,
                }
            )
            st.session_state.status = {
                "step": "Analysis failed",
                "process": error_message,
                "state": "failed",
            }
        finally:
            conversation["updated_at"] = time.time()
            st.session_state.conversations = list(st.session_state.conversations)

        if is_new_conversation:
            st.session_state.pending_history_picker = conversation["id"]
        st.rerun()


with docs_tab:
    docs = markdown_files(project_files)
    if not docs:
        with card():
            st.markdown(
                """
                <div class="empty-state">
                  <div class="empty-icon">≡</div>
                  <div class="empty-title">No Markdown files found</div>
                  <p>Markdown documentation from this project will appear here.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        relative_docs = [str(path.relative_to(project_root)) for path in docs]
        selected_doc = st.selectbox(
            "Markdown file",
            relative_docs,
            key="docs-select",
            label_visibility="collapsed",
        )
        selected_path = Path(project_root) / selected_doc
        try:
            max_preview_bytes = 1_000_000
            with selected_path.open("rb") as markdown_file:
                raw_content = markdown_file.read(max_preview_bytes + 1)
            truncated = len(raw_content) > max_preview_bytes
            content = raw_content[:max_preview_bytes].decode(
                "utf-8",
                errors="replace",
            )
            with card():
                if truncated:
                    st.caption(
                        "Preview limited to the first 1 MB to keep the page responsive."
                    )
                if content.strip():
                    render_rich_markdown(
                        content,
                        key_prefix=f"doc-{relative_docs.index(selected_doc)}",
                        allow_visuals=False,
                    )
                else:
                    st.caption("This Markdown file is empty.")
        except Exception as error:
            st.error(f"Could not read file: {type(error).__name__}: {error}")


with files_tab:
    if not project_files:
        st.info("No files found in this project.")
    else:
        search = st.text_input(
            "Search files",
            placeholder="Filter by file or folder name…",
            key="file-search",
            label_visibility="collapsed",
        )
        term = search.strip().casefold()
        visible_files = [
            str(path.relative_to(project_root))
            for path in project_files
            if not term or term in str(path.relative_to(project_root)).casefold()
        ]
        displayed_files = visible_files[:MAX_FILE_ROWS]
        if len(visible_files) > MAX_FILE_ROWS:
            st.caption(
                f"{len(visible_files)} matching files; showing the first "
                f"{MAX_FILE_ROWS} to keep the table responsive."
            )
        else:
            st.caption(f"{len(visible_files)} of {len(project_files)} files")
        if displayed_files:
            st.dataframe(
                [{"Project path": path} for path in displayed_files],
                hide_index=True,
                height=min(420, 38 + 35 * len(displayed_files)),
                **STRETCH,
            )
        else:
            st.info("No files match that search.")
