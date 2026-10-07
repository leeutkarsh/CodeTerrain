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
"""


# ---------------------------------------------------------------------------
# OLLAMA TOOL SCHEMAS
# ---------------------------------------------------------------------------

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
"""