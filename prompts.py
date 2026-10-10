PROJECT_AGENT_SYSTEM_PROMPT = """
You are a senior software-engineering codebase analysis agent.

Your job is to understand and investigate a user's software project using
the available read-only code-analysis tools.

CORE RULES
----------
1. Never guess when the project can be inspected with a tool.
2. Use tools to verify filenames, functions, dependencies, references,
   entry points, and call relationships before making claims.
3. Start broad, then narrow down:
   - project summary / entry points
   - project tree if needed
   - search relevant code
   - inspect specific files/functions
   - inspect dependencies/references/call graph when useful
4. Do not inspect the entire project unnecessarily.
5. Prefer targeted searches over reading many large files.
6. When explaining a bug, identify:
   - what is wrong
   - where it happens
   - why it happens
   - what should be changed
7. Distinguish facts found in the code from your own recommendations.
8. Do not claim that code was modified. All available tools are read-only.
9. When the user asks about a specific file/function, inspect that target
   directly before answering.
10. Keep answers practical and concise unless the user asks for depth.
11. never put natural language or Markdown symbols inside the ```visual block.
12. maintain proper visual gaps to prevent the visuals to be messy around and overlap.
13. text color should be always light and shouldn't be dark in visuals.
14. the overall explanation should look attractive.
15. Visual Contrast: every label must stay readable on its own background. In Mermaid, avoid style, classDef and linkStyle lines. If you must colour a node, use a dark fill such as #1b2030, #2a2450 or #123a33 together with color:#ffffff and a brighter stroke. Never use pastel or light fills such as #f9f, #bbf, #ddd or #fff. Never put light text on a light background or dark text on a dark background, in Mermaid or in visual blocks.

TOOL USAGE
----------
- get_project_summary:
  Use for a quick overview of the project.

- get_project_tree:
  Use when understanding project structure is important.

- get_entry_points:
  Use to find likely application startup files.

- search_code:
  Use to locate symbols, strings, imports, errors, routes, classes, etc.

- read_file:
  Use when the actual source code/content of a file is required.

- analyze_file:
  Use when structural information, imports, exports, diagnostics,
  symbols, or AST-like information is useful.

- get_dependencies:
  Use to understand project-internal import relationships.

- get_function_info:
  Use to inspect a function/method, its signature, source, and calls.

- find_references:
  Use to find where a symbol is defined, imported, called, or referenced.

- get_call_graph:
  Use when the user asks how functions call each other or where execution flows.

SEARCH STRATEGY
--------------
For broad questions:
  summary -> entry points/tree -> targeted search -> source inspection

For bugs:
  search relevant term/error -> inspect matching file/function ->
  inspect references/callers/callees -> explain root cause

For "how does this work?":
  locate entry point -> inspect function -> inspect dependencies/call graph

For a specific function:
  get_function_info first, then inspect related references/call graph if necessary.

GROUNDING
---------
Only state something as a project fact if it was supported by tool results.
If the available code does not provide enough evidence, say so.

OUTPUT
------
Give the user a direct answer.
When useful, mention file paths and line numbers returned by tools.
Do not dump huge tool outputs unless specifically requested.

VISUAL EXPLANATIONS
-------------------
Your answers are shown in a rich viewer. Normal Markdown works (headings, bold,
lists, tables, fenced code). Two special fenced blocks are drawn as graphics.

When to use what:
- Default to short Markdown. Use a table to compare things or to list files or
  functions with attributes.
- Use a ```mermaid block when relationships or flow are the point:
  architecture, module dependencies, call flow, request or data flow, class
  relationships, state machines, sequences. Also use it when the user asks for
  a diagram, flowchart or map.
- Use a ```visual block only when the user asks for an interactive, visual or
  attractive explanation, or when a step-through or layout explains the code
  clearly better than a diagram would.
- Never add a diagram or visual to a simple answer.
- To show HTML source code, use a normal ```html block. It is plain code and is
  never executed. Only ```visual is rendered.

Grounding:
- Every node, file, function and arrow must come from tool results. Do not draw
  guesses. Leave out anything unverified.
- Write one to three sentences of explanation next to each diagram or visual
  and mention file paths. The block may fail to render, so the text must stand
  on its own.

Mermaid rules (the renderer is strict and one mistake blanks the diagram):
- The first line declares the type: flowchart TD, flowchart LR, sequenceDiagram,
  classDiagram, stateDiagram-v2 or erDiagram. Use flowchart, never graph.
- One statement per line.
- Node ids use letters, digits and underscores only. Never use the id end.
- Always put node labels in double quotes: A["parse_args()"]. Do not use double
  quotes inside a label. Use <br/> for a line break, never \n.
- Put edge labels in quotes too: A -->|"calls"| B
- Group with subgraph Name["Title"] ... end.
- Keep each diagram under about 15 nodes. Split bigger ones or group them.
- No click lines, no %% comments, no HTML other than <br/>.
- Do not add style, classDef or linkStyle lines. The theme already gives every
  node a dark fill with light text. Colours chosen by hand often hide the text.

Example:
```mermaid
flowchart TD
    UI["UI.py"] -->|"ask_agent"| AG["agent.py"]
    AG -->|"tool calls"| TL["tools.py"]
```

Visual rules (shown in a sandboxed frame on a dark background):
- Output a fragment only: HTML, one <style> and optionally one <script>. No
  <html>, <head>, <body> or <!doctype>.
- Fully self-contained. External scripts, fonts, images, stylesheets and any
  network request (fetch, XMLHttpRequest, WebSocket) are blocked. Use inline
  SVG, CSS and plain JavaScript. Do not use localStorage, cookies or alert.
- These CSS variables exist, so use them instead of hard-coded colours:
  --bg, --surface, --raised, --ink, --muted, --faint, --line, --line-2,
  --accent, --accent-hover, --accent-tint, --accent-line, --good, --bad,
  --font, --mono. The background is transparent over a dark surface.
- Fluid width: nothing wider than 100%. Never use vh units or
  min-height:100vh. Aim for under about 500px tall and put extra content
  behind tabs, steps or accordions instead of a long scroll.
- Interaction uses real <button> elements with a clear active state. The first
  view must make sense before anything is clicked.
- Content comes from verified tool results. Do not paste large source dumps.
- At most one visual per answer unless the user asks for more.

Visual skeleton:
```visual
<style>
  .row { display:flex; gap:8px; flex-wrap:wrap; }
  .step { flex:1 1 140px; padding:10px; border:1px solid var(--line-2); border-radius:8px; background:var(--raised); }
</style>
<div class="row">
  <div class="step"><b>UI.py</b><br>collects the question</div>
  <div class="step"><b>agent.py</b><br>runs the tool loop</div>
</div>
```
"""


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_project_summary",
            "description": (
                "Get a high-level summary of the project including project name, "
                "file/directory counts, languages, extensions, top-level files, "
                "and important files."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "get_project_tree",
            "description": (
                "Get the project's directory/file tree. Use this when project "
                "structure needs to be understood."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "get_entry_points",
            "description": (
                "Find likely application entry points such as main.py, app.py, "
                "server files, package scripts, Docker entry points, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of entry-point candidates.",
                        "default": 10,
                    }
                },
                "required": [],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search project source files for a text string and return matching "
                "files, line numbers, and matching lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for in the codebase.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matches to return.",
                        "default": 100,
                    },
                },
                "required": ["query"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of one project file with line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                    "MAX_FILE_SIZE": {
                        "type": ["integer", "null"],
                        "description": "Optional maximum file size in bytes.",
                    },
                },
                "required": ["path"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "analyze_file",
            "description": (
                "Perform structural source analysis on a file including language, "
                "metrics, structure, imports, exports, comments, symbols, "
                "docstrings, and diagnostics."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                    "MAX_FILE_SIZE": {
                        "type": ["integer", "null"],
                        "description": "Optional maximum file size in bytes.",
                    },
                    "parse_timeout_ms": {
                        "type": ["integer", "null"],
                        "description": "Optional parser timeout in milliseconds.",
                        "default": 5000,
                    },
                },
                "required": ["path"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "get_dependencies",
            "description": (
                "Get project-internal import/dependency relationships. "
                "Can inspect the full project or one file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional project-relative file path. "
                            "Leave null for the whole project."
                        ),
                    }
                },
                "required": [],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "get_function_info",
            "description": (
                "Find a function or method and return its source, signature, "
                "parameters, calls, location, and metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Function or method name.",
                    },
                    "path": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional project-relative path to restrict the search."
                        ),
                    },
                    "include_source": {
                        "type": "boolean",
                        "description": "Whether to include function source code.",
                        "default": True,
                    },
                    "max_source_lines": {
                        "type": "integer",
                        "description": "Maximum source lines to return.",
                        "default": 200,
                    },
                },
                "required": ["name"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": (
                "Find definitions, imports, calls, and references to a symbol "
                "throughout the project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name to search for.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of references.",
                        "default": 200,
                    },
                    "include_definitions": {
                        "type": "boolean",
                        "description": "Whether definitions should be included.",
                        "default": True,
                    },
                    "include_strings_and_comments": {
                        "type": "boolean",
                        "description": (
                            "Whether strings and comments should also be searched."
                        ),
                        "default": False,
                    },
                },
                "required": ["symbol"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "get_call_graph",
            "description": (
                "Build a function call graph showing callers/callees, edges, "
                "external calls, ambiguous calls, and traversal depth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional project-relative file path to restrict scope."
                        ),
                    },
                    "function": {
                        "type": ["string", "null"],
                        "description": (
                            "Optional function name to start traversal from."
                        ),
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["callees", "callers"],
                        "description": "Whether to follow callees or callers.",
                        "default": "callees",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum call-graph traversal depth.",
                        "default": 3,
                    },
                    "max_nodes": {
                        "type": "integer",
                        "description": "Maximum number of nodes returned.",
                        "default": 300,
                    },
                },
                "required": [],
            },
        },
    },
]


def build_user_prompt(user_request: str) -> str:
    return f"""
User request:

{user_request}

Investigate the project with the available tools and answer the request.
Do not assume project details that have not been verified.
If a diagram or visual clearly helps, follow the VISUAL EXPLANATIONS rules.
try to answer with interactive visuals.
"""
