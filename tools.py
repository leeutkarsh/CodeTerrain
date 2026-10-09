import configparser
import importlib
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from pathspec import GitIgnoreSpec
from pyletree import FileTree
from tree_sitter_language_pack import ProcessConfig, detect_language_from_path, process

def load_toml_module():
    for name in ("tomllib", "tomli"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


tomllib = load_toml_module()

PROJECT_ROOT: Path | None = None

DEFAULT_MAX_FILE_SIZE_MB = 2
HEAD_READ_LIMIT = 65536

DECLARED_WEIGHT = 120
COMMAND_WEIGHT = 100
CONVENTION_WEIGHT = 80

EXTS = (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".kt", ".kts", ".scala", ".go", ".rs")
INDEXES = ("__init__.py", "index.js", "index.jsx", "index.ts", "index.tsx")
JS_EXTS = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
JVM_EXTS = (".java", ".kt", ".scala")
JVM_ROOTS = ("src/main/java", "src/main/kotlin", "src/main/scala", "src")
SOURCE_ROOTS = ("", *JVM_ROOTS)

METRIC_FIELDS = (
    "total_lines", "code_lines", "comment_lines", "blank_lines",
    "total_bytes", "node_count", "error_count", "max_depth",
)

IMPORTANT_NAMES = {
    "README", "README.md", "README.txt", "requirements.txt", "pyproject.toml",
    "setup.py", "setup.cfg", "Pipfile", "package.json", "deno.json", "composer.json",
    "pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml", "go.mod",
    "pubspec.yaml", "Package.swift", "Makefile", "Procfile", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
}

FILENAME_SCORES = {
    "main.py": 100, "app.py": 95, "run.py": 90, "server.py": 85, "cli.py": 80, "manage.py": 75,
    "__main__.py": 95, "wsgi.py": 85, "asgi.py": 85,
    "index.js": 95, "index.jsx": 95, "index.ts": 95, "index.tsx": 95,
    "main.js": 90, "main.jsx": 90, "main.ts": 90, "main.tsx": 90,
    "app.js": 90, "app.ts": 90, "server.js": 85, "server.ts": 85,
    "main.go": 100, "main.rs": 100, "main.c": 100, "main.cpp": 100, "main.cc": 100,
    "Program.cs": 100, "Startup.cs": 90,
    "Main.java": 100, "Main.kt": 100, "main.kt": 100, "main.scala": 100,
    "main.php": 95, "index.php": 90, "artisan": 80,
    "main.rb": 100, "config.ru": 90,
    "main.dart": 100, "main.swift": 100,
    "main.zig": 100, "main.hs": 100, "main.ml": 100, "main.lua": 90,
}

CANDIDATE_STEMS = {
    "main", "app", "server", "run", "index", "program", "startup",
    "__main__", "wsgi", "asgi", "bootstrap", "cli", "manage",
}

JS_FAMILY = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}

CONTENT_PATTERNS = [
    (re.compile(r"""if\s+__name__\s*==\s*["']__main__["']"""), 100, {".py"}),
    (re.compile(r"\buvicorn\.run\s*\("), 90, {".py"}),
    (re.compile(r"\b(?:app|application)\.run\s*\("), 80, {".py"}),
    (re.compile(r"@SpringBootApplication"), 100, {".java", ".kt"}),
    (re.compile(r"^package\s+main\b", re.M), 95, {".go"}),
    (re.compile(r"\bfunc\s+main\s*\("), 100, {".go"}),
    (re.compile(r"\bpub\s+fn\s+main\s*\("), 100, {".zig"}),
    (re.compile(r"\bfn\s+main\s*\("), 100, {".rs"}),
    (re.compile(r"\b(?:int|void)\s+main\s*\("), 100, {".c", ".cc", ".cpp", ".h"}),
    (re.compile(r"\bstatic\s+void\s+Main\s*\("), 100, {".cs"}),
    (re.compile(r"\bstatic\s+void\s+main\s*\("), 100, {".java"}),
    (re.compile(r"\bfun\s+main\s*\("), 100, {".kt"}),
    (re.compile(r"\bdef\s+main\s*\("), 100, {".scala"}),
    (re.compile(r"\bextends\s+(?:App|IOApp)\b"), 100, {".scala"}),
    (re.compile(r"\bvoid\s+main\s*\("), 100, {".dart"}),
    (re.compile(r"^\s*@main\b", re.M), 90, {".swift"}),
    (re.compile(r"^main\s*::", re.M), 100, {".hs"}),
    (re.compile(r"__FILE__\s*==\s*\$(?:0|PROGRAM_NAME)"), 100, {".rb"}),
    (re.compile(r"\.listen\s*\("), 60, JS_FAMILY),
]

SCRIPT_RE = re.compile(r"[\w./\\@-]+\.(?:py|[cm]?[jt]sx?|rb|php|go|rs|sh|dart|lua|pl)\b")
MODULE_RE = re.compile(r"-m[\s\"',]+([A-Za-z_][\w.]*)")
ASGI_RE = re.compile(
    r"\b(?:uvicorn|gunicorn|hypercorn|daphne|granian)\b[^\n]*?([A-Za-z_][\w.]*):[A-Za-z_]\w*"
)
ENTRY_POINT_RE = re.compile(r"""["'][\w.-]+\s*=\s*([\w.]+):[\w.]+\s*["']""")
GRADLE_RE = re.compile(
    r"""(?:mainClass(?:Name)?\s*(?:=|\.set\()|Main-Class["']?\s*(?:[:=,]|to\b))\s*["']([\w.$]+)["']"""
)
SWIFT_RE = re.compile(r'executableTarget\(\s*name:\s*"(\w+)"')
GEMSPEC_RE = re.compile(r"executables\s*=\s*(?:%w[\[({]([^\])}]*)|\[([^\]]*)\])")
DOCKER_RE = re.compile(r"^\s*(?:CMD|ENTRYPOINT)\b(.*)$", re.I | re.M)
COMPOSE_RE = re.compile(r"^\s*(?:command|entrypoint)\s*:(.*(?:\n[ \t]*-.*)*)", re.M)
MAKE_RE = re.compile(r"^(?:run|start|serve|dev)[ \t]*:[^\n]*\n((?:[ \t]+.*\n?)*)", re.M)
PY_RELATIVE_RE = re.compile(r"^(\.+)([\w.]*)$")
POM_KEYS = {"mainclass", "start-class", "main.class", "exec.mainclass"}

Entry = tuple[list[Path], str, int]


def mb_to_bytes(megabytes: float) -> int:
    return int(megabytes * 1024 * 1024)


def load_ignore_patterns(ignore_file: Path) -> list[str]:
    ignore_file = Path(ignore_file)
    if not ignore_file.exists():
        raise ValueError(f"Ignore file does not exist: {ignore_file}")

    return [
        line.strip()
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


ignores = load_ignore_patterns(Path(__file__).parent / ".ignore")
ignore_spec = GitIgnoreSpec.from_lines(ignores)


def require_root() -> Path:
    if PROJECT_ROOT is None:
        raise RuntimeError("Project root has not been set.")
    return PROJECT_ROOT


def set_project_root(path: str) -> dict:
    global PROJECT_ROOT

    root = Path(path).resolve()

    if not root.exists():
        raise ValueError(f"Project root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")

    PROJECT_ROOT = root

    return {"project_root": str(root), "status": True}


def rel(path: Path) -> str:
    return path.relative_to(require_root()).as_posix()


def is_ignored(path: Path, is_dir: bool = False) -> bool:
    posix = rel(path)
    return ignore_spec.match_file(posix) or (is_dir and ignore_spec.match_file(posix + "/"))


def resolve_path(path: str) -> Path:
    root = require_root()
    target = (root / path).resolve()

    if target != root and root not in target.parents:
        raise ValueError("Access outside project root is not allowed.")

    return target


def resolve_file(path: str) -> Path:
    target = resolve_path(path)

    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not target.is_file():
        raise ValueError(f"Path is not a file: {path}")

    return target


def read_limited(target: Path, path: str, max_mb: float | None, action: str) -> tuple[str, int, int]:
    limit = mb_to_bytes(DEFAULT_MAX_FILE_SIZE_MB if max_mb is None else max_mb)
    size = target.stat().st_size

    if size > limit:
        raise ValueError(
            f"File is too large to {action}: {path} "
            f"({size / 1024 / 1024:.2f} MB). "
            f"Maximum allowed size is {limit / 1024 / 1024:.0f} MB."
        )

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {path}") from None

    return content, size, limit


def read_head(file: Path, limit: int = HEAD_READ_LIMIT) -> str | None:
    try:
        with file.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(limit)
    except OSError:
        return None


def walk_project() -> Iterator[tuple[Path, list[str], list[str]]]:
    for current, dirnames, filenames in os.walk(require_root()):
        base = Path(current)
        dirnames[:] = sorted(d for d in dirnames if not is_ignored(base / d, is_dir=True))
        yield base, dirnames, sorted(n for n in filenames if not is_ignored(base / n))


def iter_project_files() -> Iterator[Path]:
    for base, _, filenames in walk_project():
        for name in filenames:
            yield base / name


def get_project_tree():
    tree = FileTree(root_dir=require_root(), ignore=ignores)
    return tree.get_dict_tree()


def read_file(path: str, MAX_FILE_SIZE: int | None = None) -> dict:
    target = resolve_file(path)
    content, size, _ = read_limited(target, path, MAX_FILE_SIZE, "read")
    lines = content.splitlines()

    return {
        "path": rel(target),
        "size_bytes": size,
        "line_count": len(lines),
        "content": "\n".join(f"{index:>4}: {line}" for index, line in enumerate(lines, start=1)),
    }


def search_code(query: str, max_results: int = 100) -> dict:
    require_root()

    if not query.strip():
        raise ValueError("Search query cannot be empty.")

    needle = query.casefold()
    matches = []

    for file in iter_project_files():
        try:
            with file.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if needle not in line.casefold():
                        continue

                    matches.append({
                        "file": rel(file),
                        "line": line_number,
                        "content": line.rstrip(),
                    })

                    if len(matches) >= max_results:
                        return {
                            "query": query,
                            "matches": matches,
                            "match_count": len(matches),
                            "truncated": True,
                        }
        except (UnicodeDecodeError, OSError):
            continue

    return {
        "query": query,
        "matches": matches,
        "match_count": len(matches),
        "truncated": False,
    }


def line_span(item) -> dict:
    return {
        "start_line": item.span.start_line + 1,
        "end_line": item.span.end_line + 1,
    }


def full_span(item) -> dict:
    return {
        "start_line": item.span.start_line + 1,
        "start_column": item.span.start_column,
        "end_line": item.span.end_line + 1,
        "end_column": item.span.end_column,
    }


def describe_structure(item, nested: bool = True) -> dict:
    data = {
        "kind": str(item.kind),
        "name": item.name,
        "visibility": item.visibility,
        "signature": item.signature,
        "doc_comment": item.doc_comment,
        "span": full_span(item),
    }
    if nested:
        data["children"] = [describe_structure(child, nested=False) for child in item.children]
    return data


def analyze_file(
    path: str,
    MAX_FILE_SIZE: int | None = None,
    parse_timeout_ms: int | None = 5000,
) -> dict:
    target = resolve_file(path)
    source, _, limit = read_limited(target, path, MAX_FILE_SIZE, "analyze")
    relative_path = rel(target)
    language = detect_language_from_path(str(target))

    if language is None:
        return {
            "path": relative_path,
            "language": None,
            "supported": False,
            "reason": "Could not detect a supported language from the file extension.",
        }

    try:
        result = process(
            source,
            ProcessConfig(
                language=language,
                structure=True,
                imports=True,
                exports=True,
                comments=True,
                docstrings=True,
                symbols=True,
                diagnostics=True,
                max_source_bytes=limit,
                parse_timeout_ms=parse_timeout_ms,
            ),
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to analyze {relative_path}: {exc}") from exc

    return {
        "path": relative_path,
        "language": result.language,
        "supported": True,
        "metrics": {name: getattr(result.metrics, name) for name in METRIC_FIELDS},
        "structure": [describe_structure(item) for item in result.structure],
        "imports": [
            {
                "source": item.source,
                "items": item.items,
                "alias": item.alias,
                "is_wildcard": item.is_wildcard,
                "span": line_span(item),
            }
            for item in result.imports
        ],
        "exports": [
            {"name": item.name, "kind": str(item.kind), "span": line_span(item)}
            for item in result.exports
        ],
        "comments": [
            {"text": item.text, "kind": str(item.kind), "span": line_span(item)}
            for item in result.comments
        ],
        "docstrings": [
            {
                "text": item.text,
                "format": str(item.format),
                "associated_item": item.associated_item,
                "span": line_span(item),
            }
            for item in result.docstrings
        ],
        "symbols": [
            {
                "name": item.name,
                "kind": str(item.kind),
                "type_annotation": item.type_annotation,
                "doc": item.doc,
                "span": line_span(item),
            }
            for item in result.symbols
        ],
        "diagnostics": [
            {"message": item.message, "severity": str(item.severity), "span": full_span(item)}
            for item in result.diagnostics
        ],
    }


def imports_only_config(language: str) -> ProcessConfig:
    return ProcessConfig(
        language=language,
        structure=False,
        imports=True,
        exports=False,
        comments=False,
        docstrings=False,
        symbols=False,
        diagnostics=False,
    )


def import_bases(name: str, source_file: Path, root: Path) -> list[Path]:
    if source_file.suffix == ".py" and (match := PY_RELATIVE_RE.match(name)):
        dots, rest = match.groups()
        anchor = source_file.parent
        for _ in range(len(dots) - 1):
            anchor = anchor.parent
        return [anchor.joinpath(*(part for part in rest.split(".") if part))]

    if name.startswith((".", "/")):
        return [(source_file.parent / name).resolve()]

    dotted = Path(*name.split("."))
    return [root / source_root / dotted for source_root in SOURCE_ROOTS]


def candidate_paths(base: Path) -> list[Path]:
    return [
        base,
        *(Path(f"{base}{ext}") for ext in EXTS),
        *(base / index for index in INDEXES),
    ]


def resolve_dependency(import_name: str, source_file: Path) -> Path | None:
    root = require_root()
    name = import_name.strip("\"'")

    if not name:
        return None

    own = source_file.resolve()

    for base in import_bases(name, source_file, root):
        for candidate in candidate_paths(base):
            try:
                resolved = candidate.resolve()
                if resolved.is_file() and resolved != own and root in resolved.parents:
                    return resolved
            except OSError:
                continue

    return None


def get_dependencies(path: str | None = None) -> dict:
    require_root()

    files = [resolve_file(path)] if path is not None else list(iter_project_files())

    nodes, edges, unresolved = set(), [], []

    for file in files:
        source_rel = rel(file)
        nodes.add(source_rel)

        language = detect_language_from_path(str(file))
        if language is None:
            continue

        try:
            result = process(file.read_text(encoding="utf-8"), imports_only_config(language))
        except Exception:
            continue

        for imp in result.imports:
            if not imp.source:
                continue

            dependency = resolve_dependency(imp.source, file)

            if dependency is None:
                unresolved.append({"file": source_rel, "import": imp.source, "language": language})
                continue

            target_rel = rel(dependency)
            nodes.add(target_rel)
            edges.append({
                "source": source_rel,
                "target": target_rel,
                "import": imp.source,
                "language": language,
            })

    return {"nodes": sorted(nodes), "edges": edges, "unresolved": unresolved}


def get_project_summary() -> dict:
    root = require_root()

    files, directories = [], []

    for base, dirnames, filenames in walk_project():
        directories.extend(base / name for name in dirnames)
        files.extend(base / name for name in filenames)

    languages = Counter(
        language for file in files if (language := detect_language_from_path(str(file)))
    )
    extensions = Counter(file.suffix.lower() for file in files if file.suffix)

    return {
        "project_name": root.name,
        "project_root": str(root),
        "statistics": {"files": len(files), "directories": len(directories)},
        "languages": dict(languages.most_common()),
        "extensions": dict(extensions.most_common()),
        "top_level": {
            "files": sorted(f.name for f in files if f.parent == root),
            "directories": sorted(d.name for d in directories if d.parent == root),
        },
        "important_files": sorted(rel(f) for f in files if f.name in IMPORTANT_NAMES),
    }


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def load_json(path: Path) -> dict:
    try:
        data = json.loads(read_text(path))
    except ValueError:
        return {}
    return as_dict(data)


def load_toml(path: Path) -> dict:
    if tomllib is None:
        return {}
    try:
        return tomllib.loads(read_text(path))
    except ValueError:
        return {}


def load_xml(path: Path):
    try:
        return ET.fromstring(read_text(path))
    except ET.ParseError:
        return None


def js_options(path: Path) -> list[Path]:
    return [
        path,
        *(Path(f"{path}{ext}") for ext in JS_EXTS),
        *(path / f"index{ext}" for ext in JS_EXTS),
    ]


def py_options(base: Path, dotted: str) -> list[Path]:
    module = Path(*dotted.split("."))
    options = []
    for root in (base, base / "src"):
        options.extend([
            Path(f"{root / module}.py"),
            root / module / "__main__.py",
            root / module / "__init__.py",
        ])
    return options


def jvm_options(base: Path, dotted: str) -> list[Path]:
    dotted = dotted.split("$")[0]
    names = [dotted, dotted[:-2]] if dotted.endswith("Kt") else [dotted]
    return [
        Path(f"{base / root / Path(*name.split('.'))}{ext}")
        for name in names
        for root in JVM_ROOTS
        for ext in JVM_EXTS
    ]


def tail_options(base: Path, raw: str) -> list[Path]:
    parts = [p for p in re.split(r"[\\/]+", raw) if p and p != "."]
    return [base.joinpath(*parts[i:]) for i in range(len(parts))]


def string_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, str)]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def command_entries(base: Path, text: str, reason: str) -> list[Entry]:
    entries = [
        (tail_options(base, raw), reason, COMMAND_WEIGHT)
        for raw in dict.fromkeys(SCRIPT_RE.findall(text))
    ]
    for module in dict.fromkeys(MODULE_RE.findall(text) + ASGI_RE.findall(text)):
        entries.append((py_options(base, module), reason, COMMAND_WEIGHT))
    return entries


def package_json_entries(manifest: Path) -> list[Entry]:
    data = load_json(manifest)
    base = manifest.parent
    entries = []

    for key in ("main", "module"):
        if isinstance(data.get(key), str):
            entries.append((js_options(base / data[key]), f"package.json {key}", DECLARED_WEIGHT))

    for value in string_values(data.get("bin")):
        entries.append((js_options(base / value), "package.json bin", DECLARED_WEIGHT))

    scripts = as_dict(data.get("scripts"))
    for name in ("start", "dev", "serve"):
        if isinstance(scripts.get(name), str):
            entries.extend(command_entries(base, scripts[name], f"package.json scripts.{name}"))

    return entries


def pyproject_entries(manifest: Path) -> list[Entry]:
    data = load_toml(manifest)
    project = as_dict(data.get("project"))
    poetry = as_dict(as_dict(data.get("tool")).get("poetry"))
    tables = {
        "project.scripts": project.get("scripts"),
        "project.gui-scripts": project.get("gui-scripts"),
        "tool.poetry.scripts": poetry.get("scripts"),
    }

    entries = []
    for label, table in tables.items():
        for target in string_values(as_dict(table)):
            module = target.split(":")[0].strip()
            entries.append((py_options(manifest.parent, module), f"pyproject.toml {label}", DECLARED_WEIGHT))
    return entries


def setup_cfg_entries(manifest: Path) -> list[Entry]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(read_text(manifest))
    except configparser.Error:
        return []

    entries = []
    for key in ("console_scripts", "gui_scripts"):
        raw = parser.get("options.entry_points", key, fallback="")
        for line in raw.splitlines():
            module = line.partition("=")[2].split(":")[0].strip()
            if module:
                entries.append((py_options(manifest.parent, module), f"setup.cfg {key}", DECLARED_WEIGHT))
    return entries


def setup_py_entries(manifest: Path) -> list[Entry]:
    modules = dict.fromkeys(ENTRY_POINT_RE.findall(read_text(manifest)))
    return [(py_options(manifest.parent, m), "setup.py entry_points", DECLARED_WEIGHT) for m in modules]


def pipfile_entries(manifest: Path) -> list[Entry]:
    scripts = as_dict(load_toml(manifest).get("scripts"))
    text = "\n".join(string_values(scripts))
    return command_entries(manifest.parent, text, "Pipfile scripts")


def cargo_entries(manifest: Path) -> list[Entry]:
    data = load_toml(manifest)
    base = manifest.parent
    entries = []

    bins = data.get("bin")
    for item in bins if isinstance(bins, list) else []:
        if not isinstance(item, dict):
            continue
        options = []
        if isinstance(item.get("path"), str):
            options.append(base / item["path"])
        if isinstance(item.get("name"), str):
            name = item["name"]
            options.extend([base / "src" / "bin" / f"{name}.rs", base / "src" / "bin" / name / "main.rs"])
        if options:
            entries.append((options, "Cargo.toml [[bin]]", DECLARED_WEIGHT))

    if "package" in data:
        entries.append(([base / "src" / "main.rs"], "Cargo.toml default binary", CONVENTION_WEIGHT))
        for path in sorted(base.glob("src/bin/*.rs")) + sorted(base.glob("src/bin/*/main.rs")):
            entries.append(([path], "Cargo.toml src/bin target", CONVENTION_WEIGHT))

    return entries


def go_mod_entries(manifest: Path) -> list[Entry]:
    base = manifest.parent
    paths = [base / "main.go", base / "cmd" / "main.go", *sorted(base.glob("cmd/*/main.go"))]
    return [([path], "go.mod module layout", CONVENTION_WEIGHT) for path in paths]


def pom_entries(manifest: Path) -> list[Entry]:
    root = load_xml(manifest)
    if root is None:
        return []

    entries = []
    for element in root.iter():
        tag = local(element.tag).lower()
        if tag in POM_KEYS and element.text and element.text.strip():
            entries.append((jvm_options(manifest.parent, element.text.strip()), f"pom.xml {tag}", DECLARED_WEIGHT))
    return entries


def gradle_entries(manifest: Path) -> list[Entry]:
    names = dict.fromkeys(GRADLE_RE.findall(read_text(manifest)))
    return [(jvm_options(manifest.parent, n), f"{manifest.name} mainClass", DECLARED_WEIGHT) for n in names]


def composer_entries(manifest: Path) -> list[Entry]:
    values = string_values(load_json(manifest).get("bin"))
    return [([manifest.parent / v], "composer.json bin", DECLARED_WEIGHT) for v in values]


def deno_entries(manifest: Path) -> list[Entry]:
    data = load_json(manifest)
    base = manifest.parent
    text = "\n".join(string_values(data.get("tasks")))
    entries = command_entries(base, text, "deno.json tasks")
    entries.extend(
        (tail_options(base, v), "deno.json exports", DECLARED_WEIGHT)
        for v in string_values(data.get("exports"))
    )
    return entries


def csproj_entries(manifest: Path) -> list[Entry]:
    root = load_xml(manifest)
    if root is None:
        return []

    output_types = {
        e.text.strip().lower() for e in root.iter() if local(e.tag) == "OutputType" and e.text
    }
    if not (output_types & {"exe", "winexe"} or root.get("Sdk", "").endswith(".Web")):
        return []

    base = manifest.parent
    reason = f"{manifest.name} executable project"
    return [
        ([base / "Program.cs", base / "Program.fs", base / "Main.cs"], reason, DECLARED_WEIGHT),
        ([base / "Startup.cs"], reason, CONVENTION_WEIGHT),
    ]


def pubspec_entries(manifest: Path) -> list[Entry]:
    base = manifest.parent
    paths = [base / "lib" / "main.dart", *sorted((base / "bin").glob("*.dart"))]
    return [([path], "pubspec.yaml layout", CONVENTION_WEIGHT) for path in paths]


def swift_entries(manifest: Path) -> list[Entry]:
    entries = []
    for name in dict.fromkeys(SWIFT_RE.findall(read_text(manifest))):
        folder = manifest.parent / "Sources" / name
        options = [folder / "main.swift", folder / f"{name}.swift", folder / "App.swift"]
        entries.append((options, "Package.swift executableTarget", DECLARED_WEIGHT))
    return entries


def gemspec_entries(manifest: Path) -> list[Entry]:
    entries = []
    for listed, bracketed in GEMSPEC_RE.findall(read_text(manifest)):
        for name in re.findall(r"[\w.-]+", listed or bracketed):
            options = [manifest.parent / "exe" / name, manifest.parent / "bin" / name]
            entries.append((options, "gemspec executables", DECLARED_WEIGHT))
    return entries


def dockerfile_entries(manifest: Path) -> list[Entry]:
    text = "\n".join(DOCKER_RE.findall(read_text(manifest)))
    return command_entries(manifest.parent, text, "Dockerfile CMD/ENTRYPOINT")


def compose_entries(manifest: Path) -> list[Entry]:
    text = "\n".join(COMPOSE_RE.findall(read_text(manifest)))
    return command_entries(manifest.parent, text, "compose command/entrypoint")


def procfile_entries(manifest: Path) -> list[Entry]:
    return command_entries(manifest.parent, read_text(manifest), "Procfile")


def makefile_entries(manifest: Path) -> list[Entry]:
    text = "\n".join(MAKE_RE.findall(read_text(manifest)))
    return command_entries(manifest.parent, text, "Makefile run target")


Reader = Callable[[Path], list[Entry]]

NAME_READERS: dict[str, Reader] = {
    "package.json": package_json_entries,
    "pyproject.toml": pyproject_entries,
    "setup.cfg": setup_cfg_entries,
    "setup.py": setup_py_entries,
    "pipfile": pipfile_entries,
    "cargo.toml": cargo_entries,
    "go.mod": go_mod_entries,
    "pom.xml": pom_entries,
    "build.gradle": gradle_entries,
    "build.gradle.kts": gradle_entries,
    "composer.json": composer_entries,
    "deno.json": deno_entries,
    "pubspec.yaml": pubspec_entries,
    "package.swift": swift_entries,
    "procfile": procfile_entries,
    "makefile": makefile_entries,
    "gnumakefile": makefile_entries,
    "docker-compose.yml": compose_entries,
    "docker-compose.yaml": compose_entries,
    "compose.yml": compose_entries,
    "compose.yaml": compose_entries,
}

SUFFIX_READERS: dict[str, Reader] = {
    ".csproj": csproj_entries,
    ".fsproj": csproj_entries,
    ".gemspec": gemspec_entries,
}


def manifest_reader(name: str) -> Reader | None:
    lowered = name.lower()

    if lowered in NAME_READERS:
        return NAME_READERS[lowered]

    if lowered == "dockerfile" or lowered.startswith("dockerfile.") or lowered.endswith(".dockerfile"):
        return dockerfile_entries

    return SUFFIX_READERS.get(Path(lowered).suffix)


def first_project_file(options: list[Path]) -> Path | None:
    root = require_root()

    for option in options:
        try:
            resolved = option.resolve()
        except OSError:
            continue
        if resolved.is_file() and root in resolved.parents:
            return resolved

    return None


def new_candidate(key: str, score: int = 0, reasons: list[str] | None = None) -> dict:
    return {
        "file": key,
        "score": score,
        "reasons": reasons if reasons is not None else [],
        "_depth": key.count("/") + 1,
    }


def score_file(file: Path) -> tuple[int, list[str]]:
    stem = file.stem.lower()
    suffix = file.suffix.lower()
    reasons = []

    score = FILENAME_SCORES.get(file.name, 0)
    if score:
        reasons.append(f"Common entry-point filename: {file.name}")
    elif stem.endswith("application"):
        score = 85
        reasons.append("Application-style filename")

    if score or stem in CANDIDATE_STEMS:
        source = read_head(file)
        if source:
            matches = [
                (pattern_score, pattern.pattern)
                for pattern, pattern_score, exts in CONTENT_PATTERNS
                if suffix in exts and pattern.search(source)
            ]
            if matches:
                best_score, best_pattern = max(matches)
                score += best_score
                reasons.append(f"Contains startup pattern: {best_pattern}")

    return score, reasons


def get_entry_points(limit: int = 10) -> dict:
    root = require_root()

    found: dict[str, dict] = {}
    manifests: list[tuple[Path, Reader]] = []
    languages = Counter()

    for file in iter_project_files():
        language = detect_language_from_path(str(file))
        if language:
            languages[language] += 1

        reader = manifest_reader(file.name)
        if reader:
            manifests.append((file, reader))

        score, reasons = score_file(file)
        if score:
            key = rel(file)
            found[key] = new_candidate(key, score, reasons)

    boosts: dict[str, int] = {}
    for manifest, reader in manifests:
        try:
            declared = reader(manifest)
        except Exception:
            continue

        for options, reason, weight in declared:
            target = first_project_file(options)
            if target is None or is_ignored(target):
                continue

            key = rel(target)
            entry = found.setdefault(key, new_candidate(key))
            boosts[key] = max(boosts.get(key, 0), weight)

            message = f"Declared entry point ({reason})"
            if message not in entry["reasons"]:
                entry["reasons"].append(message)

    for key, weight in boosts.items():
        found[key]["score"] += weight

    ranked = sorted(found.values(), key=lambda c: (-c["score"], c["_depth"], c["file"]))
    candidates = [{k: v for k, v in c.items() if k != "_depth"} for c in ranked]

    return {
        "project": root.name,
        "languages": dict(languages.most_common()),
        "entry_points": candidates,
        "top_candidates": candidates[:limit],
    }

CALLABLE_WORDS = ("function", "method", "constructor")
SELF_NAMES = {"self", "this", "cls"}
HASH_COMMENT_SUFFIXES = {".py", ".rb", ".sh", ".pl", ".r"}

NOT_CALLS = {
    "if", "elif", "else", "for", "while", "switch", "case", "catch", "except", "with",
    "return", "not", "and", "or", "in", "is", "assert", "await", "yield", "lambda",
    "def", "fn", "func", "function", "fun", "class", "match", "when", "synchronized",
    "sizeof", "typeof", "throw", "raise", "del", "super",
}

HASH_NOISE_RE = re.compile(
    r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|#[^\n]*'
)
SLASH_NOISE_RE = re.compile(
    r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`'
)

CALL_RE = re.compile(
    r"(?<![\w$])(?P<name>[A-Za-z_$][\w$]*(?:\s*(?:\.|::|->)\s*[A-Za-z_$][\w$]*)*)\s*\("
)
DEF_PREFIX_RE = re.compile(r"\b(?:def|class|fn|func|function|fun|interface|struct|enum|trait)\s+$")


def is_callable(definition: dict) -> bool:
    return any(word in definition["kind"] for word in CALLABLE_WORDS)


def collect_definitions(items, parent: str | None = None) -> list[dict]:
    found = []

    for item in items:
        name = item.name or ""
        qualified = f"{parent}.{name}" if parent and name else name

        found.append({
            "name": name,
            "qualified_name": qualified,
            "kind": str(item.kind).rsplit(".", 1)[-1].lower(),
            "parent": parent,
            "visibility": item.visibility,
            "signature": item.signature,
            "doc_comment": item.doc_comment,
            "start_line": item.span.start_line + 1,
            "end_line": item.span.end_line + 1,
        })
        found.extend(collect_definitions(getattr(item, "children", None) or [], qualified or parent))

    return found


def read_source(file: Path) -> str | None:
    try:
        if file.stat().st_size > mb_to_bytes(DEFAULT_MAX_FILE_SIZE_MB):
            return None
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def parse_source(file: Path, with_imports: bool = False, must_contain: str | None = None) -> dict | None:
    language = detect_language_from_path(str(file))
    source = read_source(file)

    if language is None or source is None:
        return None
    if must_contain is not None and must_contain not in source:
        return None

    config = ProcessConfig(
        language=language,
        structure=True,
        imports=with_imports,
        exports=False,
        comments=False,
        docstrings=False,
        symbols=False,
        diagnostics=False,
    )

    try:
        result = process(source, config)
    except Exception:
        return None

    return {
        "language": language,
        "source": source,
        "definitions": collect_definitions(result.structure),
        "imports": list(result.imports) if with_imports else [],
    }


def blank_noise(source: str, suffix: str) -> str:
    pattern = HASH_NOISE_RE if suffix.lower() in HASH_COMMENT_SUFFIXES else SLASH_NOISE_RE
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), source)


def preceded_by_dot(text: str, index: int) -> bool:
    while index > 0 and text[index - 1] in " \t\r\n":
        index -= 1
    return index > 0 and text[index - 1] == "."


def calls_in_span(
    clean_lines: list[str],
    start_line: int,
    end_line: int,
    own_name: str = "",
    skip: list[tuple[int, int]] | tuple = (),
) -> list[dict]:
    lines = list(clean_lines[start_line - 1:end_line])

    for skip_start, skip_end in skip:
        for number in range(skip_start, skip_end + 1):
            if start_line <= number <= end_line:
                lines[number - start_line] = ""

    text = "\n".join(lines)
    calls = []

    for match in CALL_RE.finditer(text):
        start = match.start("name")
        name = re.sub(r"\s*(?:::|->)\s*", ".", match.group("name"))
        name = re.sub(r"\s+", "", name)

        if name in NOT_CALLS or DEF_PREFIX_RE.search(text[max(0, start - 16):start]):
            continue

        line = start_line + text.count("\n", 0, start)
        if line == start_line and name.split(".")[-1] == own_name:
            continue

        if preceded_by_dot(text, start):
            name = f"?.{name}"

        calls.append({"name": name, "line": line})

    return calls


def group_calls(calls: list[dict]) -> list[dict]:
    grouped: dict[str, list[int]] = {}
    for call in calls:
        grouped.setdefault(call["name"], []).append(call["line"])
    return [{"name": name, "lines": lines} for name, lines in grouped.items()]


def describe_parameter(raw: str, language: str | None) -> dict | None:
    text = " ".join(raw.split())
    if not text:
        return None

    pieces = re.split(r"(?<![=!<>])=(?![=>])", text, maxsplit=1)
    left = pieces[0].strip()
    default = pieces[1].strip() if len(pieces) > 1 else None

    if ":" in left:
        name, _, annotation = left.partition(":")
    elif " " in left:
        tokens = left.split()
        if language == "go":
            name, annotation = tokens[0], " ".join(tokens[1:])
        else:
            name, annotation = tokens[-1], " ".join(tokens[:-1])
    else:
        name, annotation = left, ""

    name = name.strip().lstrip("*&.").rstrip("?")
    if not re.fullmatch(r"[\w$]+", name):
        return None

    return {
        "name": name,
        "annotation": annotation.strip() or None,
        "default": default,
        "raw": text,
    }


def split_parameters(signature: str | None, language: str | None) -> list[dict]:
    text = signature or ""
    start = text.find("(")
    if start != -1 and language == "go" and re.match(r"\s*func\s*\(", text):
        start = text.find("(", text.find(")", start))
    if start == -1:
        return []

    depth, previous, current, pieces = 0, "", [], []

    for char in text[start:]:
        if char in "([{<":
            depth += 1
            if depth == 1:
                previous = char
                continue
        elif char in ")]}>" and not (char == ">" and previous in ("-", "=")):
            depth -= 1
            if depth == 0:
                break

        if char == "," and depth == 1:
            pieces.append("".join(current))
            current = []
        else:
            current.append(char)
        previous = char

    pieces.append("".join(current))
    return [info for raw in pieces if (info := describe_parameter(raw, language))]


def get_function_info(
    name: str,
    path: str | None = None,
    include_source: bool = True,
    max_source_lines: int = 200,
) -> dict:
    require_root()

    wanted = name.strip()
    if not wanted:
        raise ValueError("Function name cannot be empty.")

    short = wanted.rsplit(".", 1)[-1]

    if path is not None:
        target = resolve_file(path)
        if detect_language_from_path(str(target)) is None:
            raise ValueError(f"Could not detect a supported language for: {path}")
        files = [target]
    else:
        files = iter_project_files()

    matches = []

    for file in files:
        parsed = parse_source(file, must_contain=short)
        if parsed is None:
            continue

        definitions = parsed["definitions"]
        source_lines = parsed["source"].split("\n")
        clean_lines = blank_noise(parsed["source"], file.suffix).split("\n")

        for definition in definitions:
            qualified = definition["qualified_name"]
            if not is_callable(definition):
                continue
            if qualified != wanted and not qualified.endswith(f".{wanted}"):
                continue

            start, end = definition["start_line"], definition["end_line"]
            nested = [
                (d["start_line"], d["end_line"])
                for d in definitions
                if is_callable(d) and d["parent"] == qualified
            ]
            calls = calls_in_span(clean_lines, start, end, definition["name"], nested)
            signature = definition["signature"] or source_lines[start - 1].strip()

            info = {
                "file": rel(file),
                "language": parsed["language"],
                "name": definition["name"],
                "qualified_name": qualified,
                "kind": definition["kind"],
                "parent": definition["parent"],
                "visibility": definition["visibility"],
                "signature": signature,
                "parameters": split_parameters(signature, parsed["language"]),
                "doc_comment": definition["doc_comment"],
                "start_line": start,
                "end_line": end,
                "line_count": end - start + 1,
                "calls": group_calls(calls),
            }

            if include_source:
                snippet = source_lines[start - 1:end]
                info["source_truncated"] = len(snippet) > max_source_lines
                info["source"] = "\n".join(
                    f"{number:>4}: {text}"
                    for number, text in enumerate(snippet[:max_source_lines], start=start)
                )

            matches.append(info)

    return {"query": name, "match_count": len(matches), "matches": matches}


def enclosing_function(definitions: list[dict], line: int) -> str | None:
    best = None
    for d in definitions:
        if is_callable(d) and d["start_line"] <= line <= d["end_line"]:
            if best is None or d["end_line"] - d["start_line"] < best["end_line"] - best["start_line"]:
                best = d
    return best["qualified_name"] if best else None


def find_references(
    symbol: str,
    max_results: int = 200,
    include_definitions: bool = True,
    include_strings_and_comments: bool = False,
) -> dict:
    require_root()

    name = symbol.strip().rsplit(".", 1)[-1]
    if not re.fullmatch(r"[\w$]+", name):
        raise ValueError(f"Not a valid symbol name: {symbol!r}")

    pattern = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    references = []

    for file in iter_project_files():
        parsed = parse_source(file, with_imports=True, must_contain=name)
        if parsed is None:
            continue

        source = parsed["source"]
        searchable = source if include_strings_and_comments else blank_noise(source, file.suffix)
        original_lines = source.split("\n")
        definitions = parsed["definitions"]

        defining_lines = {d["start_line"] for d in definitions if d["name"] == name}
        import_lines = set()
        for imp in parsed["imports"]:
            import_lines.update(range(imp.span.start_line + 1, imp.span.end_line + 2))

        for number, text in enumerate(searchable.split("\n"), start=1):
            first = pattern.search(text)
            if first is None:
                continue

            for match in pattern.finditer(text):
                rest = text[match.end():].lstrip()
                is_definition = bool(DEF_PREFIX_RE.search(text[max(0, match.start() - 16):match.start()])) or (
                    number in defining_lines and match.start() == first.start() and rest.startswith("(")
                )

                if is_definition:
                    kind = "definition"
                elif number in import_lines:
                    kind = "import"
                elif rest.startswith("("):
                    kind = "call"
                else:
                    kind = "reference"

                if kind == "definition" and not include_definitions:
                    continue

                references.append({
                    "file": rel(file),
                    "line": number,
                    "column": match.start() + 1,
                    "kind": kind,
                    "in_function": None if kind == "definition" else enclosing_function(definitions, number),
                    "context": original_lines[number - 1].strip(),
                })

        if len(references) > max_results:
            break

    truncated = len(references) > max_results
    references = references[:max_results]

    return {
        "symbol": name,
        "reference_count": len(references),
        "by_kind": dict(Counter(r["kind"] for r in references)),
        "files": dict(Counter(r["file"] for r in references).most_common()),
        "references": references,
        "truncated": truncated,
    }


def import_bindings(file: Path, imports: list, by_file: dict) -> tuple[dict, set]:
    internal: dict[str, tuple[str, str | None]] = {}
    external: set[str] = set()

    for imp in imports:
        if not imp.source:
            continue

        names: list[tuple[str, str | None]] = []
        for item in imp.items or []:
            original, _, alias = str(item).partition(" as ")
            original = original.strip()
            names.append((alias.strip() or original, original))
        if imp.alias:
            names.append((imp.alias, None))
        if not names:
            if imp.source.startswith((".", "/")):
                names.append((Path(imp.source).stem, None))
            else:
                names.append((imp.source.split(".")[0], None))

        target = resolve_dependency(imp.source, file)
        if target is None:
            external.update(local for local, _ in names)
            continue

        target_rel = rel(target)
        for local, original in names:
            if original and original not in by_file.get(target_rel, {}) and "/" not in imp.source:
                separator = "" if imp.source.endswith(".") else "."
                submodule = resolve_dependency(f"{imp.source}{separator}{original}", file)
                if submodule is not None:
                    internal[local] = (rel(submodule), None)
                    continue
            internal[local] = (target_rel, original)

    return internal, external


def resolve_call(call: str, caller: dict, tables: dict) -> tuple[str, list[str]]:
    nodes, by_file = tables["nodes"], tables["by_file"]
    file = caller["file"]
    parts = call.split(".")
    head, last = parts[0], parts[-1]
    in_file = by_file.get(file, {})
    binding = tables["internal"].get(file, {}).get(head)
    external_names = tables["external"].get(file, set())

    def with_parent(ids: list[str], parent: str | None) -> list[str]:
        return [i for i in ids if nodes[i]["parent"] == parent]

    def outcome(ids: list[str], status: str = "resolved") -> tuple[str, list[str]]:
        return ("ambiguous" if len(ids) > 1 else status), ids

    def constructors(file_name: str, class_name: str) -> list[str]:
        found: list[str] = []
        for ctor in ("__init__", "constructor"):
            found += with_parent(by_file.get(file_name, {}).get(ctor, []), class_name)
        return found

    if len(parts) == 1:
        ids = in_file.get(last, [])
        for tier in (
            with_parent(ids, caller["qualified_name"]),
            with_parent(ids, None),
            with_parent(ids, caller["parent"]),
        ):
            if tier:
                return outcome(tier)

        if binding:
            target_file, original = binding
            target_ids = by_file.get(target_file, {}).get(original or last, [])
            found = with_parent(target_ids, None) or constructors(target_file, original or last)
            return outcome(found) if found else ("external", [])

        if last in external_names:
            return "external", []
        if last in tables["classes"].get(file, set()):
            found = constructors(file, last)
            if found:
                return outcome(found)

        ids = with_parent(tables["by_name"].get(last, []), None)
        return outcome(ids, "guessed") if ids else ("external", [])

    if head in SELF_NAMES and len(parts) == 2:
        ids = with_parent(in_file.get(last, []), caller["parent"])
        if ids:
            return outcome(ids)
        ids = [i for i in tables["by_name"].get(last, []) if nodes[i]["parent"]]
        return outcome(ids, "guessed") if ids else ("external", [])

    if binding:
        target_file, original = binding
        in_target = by_file.get(target_file, {}).get(last, [])
        owner = original or (parts[-2] if len(parts) >= 3 else None)
        ids = with_parent(in_target, owner) if owner else []
        if not ids and not original:
            ids = with_parent(in_target, None)
        return outcome(ids) if ids else ("external", [])

    if head in tables["classes"].get(file, set()) and len(parts) == 2:
        ids = with_parent(in_file.get(last, []), head)
        if ids:
            return outcome(ids)

    if head in external_names:
        return "external", []

    if len(parts) == 2:
        ids = [i for i in tables["by_name"].get(last, []) if nodes[i]["parent"]]
        if ids:
            return outcome(ids, "guessed")

    return "external", []


def walk_call_graph(starts: list[str], adjacency: dict, direction: str, max_depth: int) -> tuple[list, dict]:
    depth_of = {node_id: 0 for node_id in starts}
    walked, frontier = [], list(starts)

    while frontier:
        following = []
        for current in frontier:
            if depth_of[current] >= max_depth:
                continue
            for edge in adjacency.get(current, []):
                walked.append(edge)
                neighbour = edge["target"] if direction == "callees" else edge["source"]
                if neighbour not in depth_of:
                    depth_of[neighbour] = depth_of[current] + 1
                    following.append(neighbour)
        frontier = following

    return walked, depth_of


def get_call_graph(
    path: str | None = None,
    function: str | None = None,
    direction: str = "callees",
    max_depth: int = 3,
    max_nodes: int = 300,
) -> dict:
    require_root()

    if direction not in ("callees", "callers"):
        raise ValueError('direction must be "callees" or "callers".')
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1.")

    focus_file = rel(resolve_file(path)) if path is not None else None

    nodes: dict[str, dict] = {}
    by_file: dict[str, dict[str, list[str]]] = {}
    by_name: dict[str, list[str]] = {}
    classes: dict[str, set[str]] = {}
    nested_ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
    parsed_files: dict[str, tuple[Path, dict]] = {}

    for file in iter_project_files():
        parsed = parse_source(file, with_imports=True)
        if parsed is None:
            continue

        file_rel = rel(file)
        parsed_files[file_rel] = (file, parsed)
        classes[file_rel] = {d["name"] for d in parsed["definitions"] if d["name"] and not is_callable(d)}

        for d in parsed["definitions"]:
            if d["parent"]:
                nested_ranges.setdefault((file_rel, d["parent"]), []).append((d["start_line"], d["end_line"]))
            if not d["name"] or not is_callable(d):
                continue

            node_id = f"{file_rel}::{d['qualified_name']}"
            if node_id in nodes:
                node_id = f"{node_id}#{d['start_line']}"

            nodes[node_id] = {
                "id": node_id,
                "file": file_rel,
                "name": d["name"],
                "qualified_name": d["qualified_name"],
                "parent": d["parent"],
                "kind": d["kind"],
                "start_line": d["start_line"],
                "end_line": d["end_line"],
            }
            by_file.setdefault(file_rel, {}).setdefault(d["name"], []).append(node_id)
            by_name.setdefault(d["name"], []).append(node_id)

    tables = {
        "nodes": nodes, "by_file": by_file, "by_name": by_name, "classes": classes,
        "internal": {}, "external": {},
    }
    for file_rel, (file, parsed) in parsed_files.items():
        tables["internal"][file_rel], tables["external"][file_rel] = import_bindings(
            file, parsed["imports"], by_file
        )

    clean_cache: dict[str, list[str]] = {}
    edges: dict[tuple[str, str], dict] = {}
    ambiguous: list[dict] = []
    external_by_caller: dict[str, Counter] = {}
    only_file = focus_file if function is None else None

    for node_id, node in nodes.items():
        if only_file is not None and node["file"] != only_file:
            continue

        file, parsed = parsed_files[node["file"]]
        if node["file"] not in clean_cache:
            clean_cache[node["file"]] = blank_noise(parsed["source"], file.suffix).split("\n")

        skip = nested_ranges.get((node["file"], node["qualified_name"]), [])
        for call in calls_in_span(
            clean_cache[node["file"]], node["start_line"], node["end_line"], node["name"], skip
        ):
            status, targets = resolve_call(call["name"], node, tables)

            if status == "external":
                external_by_caller.setdefault(node_id, Counter())[call["name"].removeprefix("?.")] += 1
            elif status == "ambiguous":
                ambiguous.append({
                    "caller": node_id, "call": call["name"],
                    "line": call["line"], "candidates": targets,
                })
            else:
                edge = edges.setdefault((node_id, targets[0]), {
                    "source": node_id,
                    "target": targets[0],
                    "call": call["name"],
                    "confidence": "high" if status == "resolved" else "low",
                    "lines": [],
                })
                edge["lines"].append(call["line"])
                if status == "resolved":
                    edge["confidence"] = "high"

    adjacency: dict[str, list[dict]] = {}
    key = "source" if direction == "callees" else "target"
    for edge in edges.values():
        adjacency.setdefault(edge[key], []).append(edge)

    depth_of: dict[str, int] = {}

    if function is not None:
        wanted = function.strip()
        starts = [
            i for i, n in nodes.items()
            if (n["qualified_name"] == wanted or n["qualified_name"].endswith(f".{wanted}"))
            and (focus_file is None or n["file"] == focus_file)
        ]
        if not starts:
            raise ValueError(f"Function not found: {function}")
        selected, depth_of = walk_call_graph(starts, adjacency, direction, max_depth)
        node_ids = list(depth_of)
    else:
        selected = [e for e in edges.values() if focus_file is None or nodes[e["source"]]["file"] == focus_file]
        wanted_ids = {e["source"] for e in selected} | {e["target"] for e in selected}
        if focus_file is not None:
            wanted_ids |= {i for i, n in nodes.items() if n["file"] == focus_file}
        node_ids = sorted(wanted_ids)

    truncated = len(node_ids) > max_nodes
    node_ids = node_ids[:max_nodes]
    keep = set(node_ids)
    selected = [e for e in selected if e["source"] in keep and e["target"] in keep]

    external_calls = Counter()
    for node_id in node_ids:
        external_calls.update(external_by_caller.get(node_id, {}))

    return {
        "scope": {"path": focus_file, "function": function, "direction": direction},
        "nodes": [
            {**nodes[i], **({"depth": depth_of[i]} if function is not None else {})} for i in node_ids
        ],
        "edges": selected,
        "roots": sorted({e["source"] for e in selected} - {e["target"] for e in selected}),
        "external_calls": dict(external_calls.most_common(25)),
        "ambiguous_calls": [a for a in ambiguous if a["caller"] in keep][:50],
        "truncated": truncated,
    }
