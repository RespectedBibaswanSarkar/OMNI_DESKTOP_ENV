from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

Verifier = Callable[[Path], tuple[bool, str]]
Fixture = Callable[[Path], None]


@dataclass(frozen=True)
class BenchTask:
    id: str
    category: str
    goal: str
    files: dict[str, str] = field(default_factory=dict)
    verify: Verifier | None = None
    fixture: Fixture | None = None
    timeout_s: float = 120.0

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "category": self.category,
            "goal": self.goal,
            "files": sorted(self.files),
            "timeout_s": self.timeout_s,
            "has_fixture": self.fixture is not None,
        }


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _read(root: Path, name: str) -> str | None:
    try:
        return (root / name).read_text(encoding="utf-8")
    except OSError:
        return None


def _run(root: Path, command: list[str], timeout: float = 60.0) -> tuple[bool, str]:
    try:
        proc = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-800:]


def _load(root: Path, relative: str, module_name: str):
    path = root / relative
    if not path.exists():
        raise FileNotFoundError(relative)
    # Drop workspace-local modules cached from a previous run (seeds reuse names).
    for cached in list(sys.modules):
        top = cached.split(".")[0]
        if (root / top).exists() or (root / f"{top}.py").exists():
            sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    inserted = str(root) not in sys.path
    if inserted:
        sys.path.insert(0, str(root))
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            sys.path.remove(str(root))
    return module


# --------------------------------------------------------------------------- verifiers
def v_notes(root: Path) -> tuple[bool, str]:
    text = _read(root, "notes.md")
    ok = bool(text and "# OMNI Notes" in text and "benchmark" in text)
    return ok, "notes.md exists with heading and benchmark line" if ok else "notes.md missing required heading/line"


def v_rename(root: Path) -> tuple[bool, str]:
    ok = (root / "final.txt").exists() and not (root / "draft.txt").exists()
    content = _read(root, "final.txt") or ""
    ok = ok and content.strip() == "draft content"
    return ok, "draft.txt renamed to final.txt with content preserved" if ok else "rename incomplete"


def v_move(root: Path) -> tuple[bool, str]:
    ok = (root / "archive" / "data.txt").exists() and not (root / "src" / "data.txt").exists()
    return ok, "src/data.txt moved to archive/data.txt" if ok else "move incomplete"


def v_delete_dupes(root: Path) -> tuple[bool, str]:
    ok = (root / "keep.txt").exists() and not (root / "copy1.txt").exists() and not (root / "copy2.txt").exists()
    return ok, "duplicates removed, keep.txt retained" if ok else "duplicates remain"


def v_manifest(root: Path) -> tuple[bool, str]:
    text = _read(root, "manifest.txt")
    expected = ["a.txt", "b.txt", "c.txt"]
    got = [line.strip() for line in (text or "").splitlines() if line.strip()]
    ok = got == expected
    return ok, f"manifest {got}" if ok else f"expected {expected}, got {got}"


def v_heading(root: Path) -> tuple[bool, str]:
    text = _read(root, "README.md") or ""
    ok = text.lstrip().startswith("# Project")
    return ok, "README starts with # Project" if ok else "missing # Project heading"


def v_insert_section(root: Path) -> tuple[bool, str]:
    text = _read(root, "README.md") or ""
    ok = text.find("## Usage") != -1 and text.find("## Usage") < text.find("## License")
    return ok, "## Usage inserted before ## License" if ok else "section order wrong"


def v_typo(root: Path) -> tuple[bool, str]:
    text = (_read(root, "notes.txt") or "").lower()
    ok = "teh" not in text and "the" in text
    return ok, "typo fixed" if ok else "typo remains"


def v_bullets(root: Path) -> tuple[bool, str]:
    text = _read(root, "todo.md") or ""
    lines = [line.strip() for line in text.splitlines()]
    bullets = [line for line in lines if line.startswith("- ")]
    ok = [item.lower() for item in bullets] == ["- write tests", "- fix bug", "- ship"]
    return ok, f"bullets {bullets}" if ok else f"unexpected bullets {bullets}"


def v_summary(root: Path) -> tuple[bool, str]:
    text = (_read(root, "summary.md") or "").lower()
    bullets = [line for line in text.splitlines() if line.strip().startswith("- ")]
    ok = len(bullets) == 2 and "renewable" in text and "storage" in text
    return ok, "two-point summary present" if ok else "summary needs two bullets covering renewable/storage"


def _math(root: Path):
    return _load(root, "math_utils.py", f"bench_math_{abs(hash(str(root))) % 10**8}")


def v_add(root: Path) -> tuple[bool, str]:
    try:
        module = _math(root)
        ok = module.add(2, 3) == 5 and module.add(-1, 1) == 0
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    return ok, "add() works" if ok else "add() returned wrong value"


def v_square(root: Path) -> tuple[bool, str]:
    try:
        module = _load(root, "calc.py", f"bench_calc_{abs(hash(str(root))) % 10**8}")
        ok = module.square(5) == 25 and module.square(3) == 9
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    return ok, "square() fixed" if ok else "square() wrong"


def v_cli(root: Path) -> tuple[bool, str]:
    ok, out = _run(root, [sys.executable, "greet.py", "--name", "Ada"])
    return ok and "Hello, Ada" in out, out.strip()[:200]


def v_config(root: Path) -> tuple[bool, str]:
    try:
        module = _load(root, "config_io.py", f"bench_cfg_{abs(hash(str(root))) % 10**8}")
        value = module.load_config(root / "config.json")
        ok = value == {"debug": True, "port": 8000}
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    return ok, "load_config returns parsed JSON" if ok else "load_config returned wrong value"


def v_refactor(root: Path) -> tuple[bool, str]:
    text = (_read(root, "service.py") or "") + (_read(root, "app.py") or "")
    try:
        module = _load(root, "service.py", f"bench_svc_{abs(hash(str(root))) % 10**8}")
        ok = module.new_name() == "ok" and "old_name" not in text
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    return ok, "old_name renamed and callers updated" if ok else "refactor incomplete"


def v_dataclass(root: Path) -> tuple[bool, str]:
    try:
        module = _load(root, "geometry.py", f"bench_geo_{abs(hash(str(root))) % 10**8}")
        point = module.Point(3, 4)
        ok = point.x == 3 and point.y == 4 and abs(point.dist() - 5.0) < 1e-9
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    return ok, "Point dataclass works" if ok else "Point API wrong"


def v_emails(root: Path) -> tuple[bool, str]:
    try:
        module = _load(root, "extract.py", f"bench_ext_{abs(hash(str(root))) % 10**8}")
        got = module.extract_emails("a@b.com and x.y@z.org, not-this")
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    ok = got == ["a@b.com", "x.y@z.org"]
    return ok, f"emails {got}" if ok else f"unexpected emails {got}"


def v_top_n(root: Path) -> tuple[bool, str]:
    try:
        module = _load(root, "ranking.py", f"bench_rank_{abs(hash(str(root))) % 10**8}")
        got = module.top_n([1, 5, 3, 4], 2)
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    ok = got == [5, 4]
    return ok, f"top_n {got}" if ok else f"unexpected {got}"


def v_format_user(root: Path) -> tuple[bool, str]:
    try:
        module = _load(root, "users.py", f"bench_users_{abs(hash(str(root))) % 10**8}")
        ok = module.format_user({}) == "anonymous" and module.format_user({"name": "Ada"}) == "Ada"
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    return ok, "missing name defaults correctly" if ok else "format_user wrong"


def v_csv_summary(root: Path) -> tuple[bool, str]:
    try:
        data = json.loads(_read(root, "summary.json") or "{}")
    except json.JSONDecodeError:
        return False, "summary.json is not valid JSON"
    ok = data.get("total") == 60 and data.get("count") == 3 and data.get("max") == 30
    return ok, f"summary {data}" if ok else f"unexpected summary {data}"


def v_dedupe(root: Path) -> tuple[bool, str]:
    text = _read(root, "unique.jsonl") or ""
    ids = []
    for line in text.splitlines():
        if line.strip():
            try:
                ids.append(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                return False, "invalid JSONL"
    return ids == [1, 2], f"ids {ids}"


def v_sqlite(root: Path) -> tuple[bool, str]:
    try:
        connection = sqlite3.connect(root / "db.sqlite")
        rows = connection.execute("SELECT name FROM items ORDER BY name").fetchall()
        connection.close()
    except sqlite3.Error as exc:
        return False, f"sqlite error: {exc}"
    names = [row[0] for row in rows]
    return names == ["alpha", "beta"], f"rows {names}"


def v_groupby(root: Path) -> tuple[bool, str]:
    try:
        data = json.loads(_read(root, "by_region.json") or "{}")
    except json.JSONDecodeError:
        return False, "by_region.json is not valid JSON"
    ok = data == {"east": 30, "west": 40}
    return ok, f"groups {data}" if ok else f"unexpected groups {data}"


def v_pytest(root: Path) -> tuple[bool, str]:
    return _run(root, [sys.executable, "-m", "pytest", "-q"])


def v_shell(root: Path) -> tuple[bool, str]:
    path = root / "run.sh"
    if not path.exists():
        return False, "run.sh missing"
    if not (path.stat().st_mode & 0o111):
        return False, "run.sh is not executable"
    ok, out = _run(root, ["sh", "run.sh"])
    return ok and out.strip() == "ok", out.strip()[:120]


def v_makefile(root: Path) -> tuple[bool, str]:
    return _run(root, ["make", "test"])


def v_version(root: Path) -> tuple[bool, str]:
    text = (_read(root, "version.py") or "") + (_read(root, "package/__init__.py") or "")
    try:
        module = _load(root, "package/__init__.py", f"bench_pkg_{abs(hash(str(root))) % 10**8}")
        ok = module.VERSION == "1.0" and 'VERSION = "1.0"' in text
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    return ok, "VERSION exported from package" if ok else "VERSION not exported"


def v_consistency(root: Path) -> tuple[bool, str]:
    config = _read(root, "config.py") or ""
    guide = _read(root, "docs/guide.md") or ""
    ok = "8000" in config and "8000" in guide and "9000" not in config and "9000" not in guide
    return ok, "both files use port 8000" if ok else "port mismatch remains"


def v_migration(root: Path) -> tuple[bool, str]:
    schema = (_read(root, "schema.sql") or "").lower()
    query = (_read(root, "query.sql") or "").lower()
    ok = "accounts" in schema and "accounts" in query and not re.search(r"\busers\b", schema + query)
    return ok, "schema and query use accounts" if ok else "users table still referenced"


def v_slug(root: Path) -> tuple[bool, str]:
    try:
        module = _load(root, "app.py", f"bench_app_{abs(hash(str(root))) % 10**8}")
        ok = module.slugify("Hello World!") == "hello-world" and (root / "text" / "slug.py").exists()
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {exc}"
    return ok, "slugify moved to text/slug.py and app imports it" if ok else "slugify split incomplete"


# --------------------------------------------------------------------------- fixtures
def f_notes(root: Path) -> None:
    (root / "notes.md").write_text("# OMNI Notes\n\n- benchmark\n", encoding="utf-8")


def f_rename(root: Path) -> None:
    (root / "draft.txt").rename(root / "final.txt")


def f_move(root: Path) -> None:
    (root / "archive").mkdir(exist_ok=True)
    (root / "src" / "data.txt").rename(root / "archive" / "data.txt")


def f_delete_dupes(root: Path) -> None:
    for name in ("copy1.txt", "copy2.txt"):
        (root / name).unlink()


def f_manifest(root: Path) -> None:
    names = sorted(path.name for path in (root / "data").iterdir())
    (root / "manifest.txt").write_text("\n".join(names) + "\n", encoding="utf-8")


def f_heading(root: Path) -> None:
    (root / "README.md").write_text("# Project\n\nA sample project.\n", encoding="utf-8")


def f_insert_section(root: Path) -> None:
    text = _read(root, "README.md") or ""
    (root / "README.md").write_text(text.replace("## License", "## Usage\n\nRun it.\n\n## License"), encoding="utf-8")


def f_typo(root: Path) -> None:
    text = _read(root, "notes.txt") or ""
    (root / "notes.txt").write_text(text.replace("teh", "the"), encoding="utf-8")


def f_bullets(root: Path) -> None:
    (root / "todo.md").write_text("# Todo\n\n- write tests\n- fix bug\n- ship\n", encoding="utf-8")


def f_summary(root: Path) -> None:
    (root / "summary.md").write_text("- Renewable generation grew.\n- Storage capacity expanded.\n", encoding="utf-8")


def f_add(root: Path) -> None:
    (root / "math_utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")


def f_square(root: Path) -> None:
    (root / "calc.py").write_text("def square(x):\n    return x * x\n", encoding="utf-8")


def f_cli(root: Path) -> None:
    (root / "greet.py").write_text(
        "import argparse\n\nparser = argparse.ArgumentParser()\nparser.add_argument('--name', default='World')\nargs = parser.parse_args()\nprint(f'Hello, {args.name}')\n",
        encoding="utf-8",
    )


def f_config(root: Path) -> None:
    (root / "config_io.py").write_text("import json\n\ndef load_config(path):\n    return json.loads(open(path, encoding='utf-8').read())\n", encoding="utf-8")


def f_refactor(root: Path) -> None:
    (root / "service.py").write_text("def new_name():\n    return 'ok'\n", encoding="utf-8")
    (root / "app.py").write_text("from service import new_name\n\nprint(new_name())\n", encoding="utf-8")


def f_dataclass(root: Path) -> None:
    (root / "geometry.py").write_text(
        "from dataclasses import dataclass\nimport math\n\n@dataclass\nclass Point:\n    x: float\n    y: float\n\n    def dist(self):\n        return math.hypot(self.x, self.y)\n",
        encoding="utf-8",
    )


def f_emails(root: Path) -> None:
    (root / "extract.py").write_text(
        "import re\n\ndef extract_emails(text):\n    return re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', text)\n",
        encoding="utf-8",
    )


def f_top_n(root: Path) -> None:
    (root / "ranking.py").write_text("def top_n(items, n):\n    return sorted(items, reverse=True)[:n]\n", encoding="utf-8")


def f_format_user(root: Path) -> None:
    (root / "users.py").write_text("def format_user(user):\n    return user.get('name', 'anonymous')\n", encoding="utf-8")


def f_csv_summary(root: Path) -> None:
    import csv

    rows = list(csv.DictReader((root / "sales.csv").open(encoding="utf-8")))
    amounts = [int(row["amount"]) for row in rows]
    (root / "summary.json").write_text(json.dumps({"total": sum(amounts), "count": len(amounts), "max": max(amounts)}), encoding="utf-8")


def f_dedupe(root: Path) -> None:
    seen = set()
    output = []
    for line in (_read(root, "events.jsonl") or "").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item["id"] not in seen:
            seen.add(item["id"])
            output.append(json.dumps(item))
    (root / "unique.jsonl").write_text("\n".join(output) + "\n", encoding="utf-8")


def f_sqlite(root: Path) -> None:
    connection = sqlite3.connect(root / "db.sqlite")
    connection.execute("CREATE TABLE IF NOT EXISTS items (name TEXT)")
    connection.executemany("INSERT INTO items (name) VALUES (?)", [("alpha",), ("beta",)])
    connection.commit()
    connection.close()


def f_groupby(root: Path) -> None:
    import csv

    totals: dict[str, int] = {}
    for row in csv.DictReader((root / "orders.csv").open(encoding="utf-8")):
        totals[row["region"]] = totals.get(row["region"], 0) + int(row["amount"])
    (root / "by_region.json").write_text(json.dumps(totals), encoding="utf-8")


def f_pytest(root: Path) -> None:
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")


def f_shell(root: Path) -> None:
    path = root / "run.sh"
    path.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    path.chmod(0o755)


def f_makefile(root: Path) -> None:
    (root / "Makefile").write_text("test:\n\tpython3 -m pytest -q\n", encoding="utf-8")


def f_version(root: Path) -> None:
    (root / "version.py").write_text('VERSION = "1.0"\n', encoding="utf-8")
    (root / "package" / "__init__.py").write_text("from version import VERSION\n\n__all__ = ['VERSION']\n", encoding="utf-8")


def f_consistency(root: Path) -> None:
    (root / "config.py").write_text('PORT = 8000\n', encoding="utf-8")
    (root / "docs" / "guide.md").write_text("# Guide\n\nThe default port is 8000.\n", encoding="utf-8")


def f_migration(root: Path) -> None:
    (root / "schema.sql").write_text("CREATE TABLE accounts (id INTEGER, name TEXT);\n", encoding="utf-8")
    (root / "query.sql").write_text("SELECT * FROM accounts;\n", encoding="utf-8")


def f_slug(root: Path) -> None:
    (root / "text").mkdir(exist_ok=True)
    (root / "text" / "__init__.py").write_text("", encoding="utf-8")
    (root / "text" / "slug.py").write_text(
        "import re\n\ndef slugify(value):\n    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text("from text.slug import slugify\n\nif __name__ == '__main__':\n    print(slugify('Hello World!'))\n", encoding="utf-8")


# --------------------------------------------------------------------------- task suite
TASKS: tuple[BenchTask, ...] = (
    BenchTask("files-create-notes", "files", "Create notes.md with the heading '# OMNI Notes' and a line mentioning 'benchmark'.", {}, v_notes, f_notes, 60),
    BenchTask("files-rename", "files", "Rename draft.txt to final.txt without changing its content.", {"draft.txt": "draft content\n"}, v_rename, f_rename, 60),
    BenchTask("files-move", "files", "Move src/data.txt to archive/data.txt.", {"src/data.txt": "payload\n"}, v_move, f_move, 60),
    BenchTask("files-delete-dupes", "files", "Delete copy1.txt and copy2.txt but keep keep.txt.", {"keep.txt": "keep\n", "copy1.txt": "x\n", "copy2.txt": "x\n"}, v_delete_dupes, f_delete_dupes, 60),
    BenchTask("files-manifest", "files", "Write manifest.txt listing every filename in data/ in sorted order, one per line.", {"data/a.txt": "a\n", "data/b.txt": "b\n", "data/c.txt": "c\n"}, v_manifest, f_manifest, 60),
    BenchTask("doc-heading", "documents", "Add a top-level '# Project' heading to README.md.", {"README.md": "A sample project.\n"}, v_heading, f_heading, 60),
    BenchTask("doc-insert-section", "documents", "Insert a '## Usage' section before '## License' in README.md.", {"README.md": "# Docs\n\n## License\n\nMIT\n"}, v_insert_section, f_insert_section, 60),
    BenchTask("doc-fix-typo", "documents", "Fix the typo 'teh' to 'the' in notes.txt.", {"notes.txt": "teh quick brown fox\n"}, v_typo, f_typo, 60),
    BenchTask("doc-bullets", "documents", "Rewrite todo.md so the three items are markdown bullets under '# Todo'.", {"todo.md": "write tests, fix bug, ship\n"}, v_bullets, f_bullets, 60),
    BenchTask("doc-summary", "documents", "Create summary.md with exactly two bullet points covering renewable energy and storage.", {"article.md": "Renewable energy grew while storage capacity expanded.\n"}, v_summary, f_summary, 60),
    BenchTask("code-add-function", "code", "Implement add(a, b) in math_utils.py.", {"math_utils.py": "def add(a, b):\n    return a - b\n"}, v_add, f_add, 60),
    BenchTask("code-fix-test", "code", "Fix square(x) in calc.py so it returns x squared.", {"calc.py": "def square(x):\n    return x * 2\n"}, v_square, f_square, 60),
    BenchTask("code-cli-arg", "code", "Add a --name argument to greet.py and print 'Hello, <name>'.", {"greet.py": "print('Hello, World')\n"}, v_cli, f_cli, 60),
    BenchTask("code-json-parse", "code", "Implement load_config(path) in config_io.py to return parsed JSON.", {"config_io.py": "def load_config(path):\n    return {}\n", "config.json": '{"debug": true, "port": 8000}\n'}, v_config, f_config, 60),
    BenchTask("code-refactor", "code", "Rename old_name to new_name in service.py and update app.py.", {"service.py": "def old_name():\n    return 'ok'\n", "app.py": "from service import old_name\n\nprint(old_name())\n"}, v_refactor, f_refactor, 60),
    BenchTask("code-dataclass", "code", "Define a Point dataclass with x, y and a dist() method in geometry.py.", {"geometry.py": ""}, v_dataclass, f_dataclass, 60),
    BenchTask("code-regex", "code", "Implement extract_emails(text) returning email addresses in order.", {"extract.py": "def extract_emails(text):\n    return []\n"}, v_emails, f_emails, 60),
    BenchTask("code-sort", "code", "Implement top_n(items, n) returning the n largest values descending.", {"ranking.py": "def top_n(items, n):\n    return items\n"}, v_top_n, f_top_n, 60),
    BenchTask("code-type-fix", "code", "Make format_user({}) return 'anonymous' while keeping named users.", {"users.py": "def format_user(user):\n    return user['name']\n"}, v_format_user, f_format_user, 60),
    BenchTask("data-csv-sum", "data", "Read sales.csv and write summary.json with total, count and max.", {"sales.csv": "amount\n10\n20\n30\n"}, v_csv_summary, f_csv_summary, 60),
    BenchTask("data-jsonl-dedupe", "data", "Deduplicate events.jsonl by id, keeping the first occurrence, into unique.jsonl.", {"events.jsonl": '{"id": 1, "v": "a"}\n{"id": 2, "v": "b"}\n{"id": 1, "v": "c"}\n'}, v_dedupe, f_dedupe, 60),
    BenchTask("data-sqlite", "data", "Create db.sqlite with table items(name) containing alpha and beta.", {}, v_sqlite, f_sqlite, 60),
    BenchTask("data-groupby", "data", "Group orders.csv by region into by_region.json with summed amounts.", {"orders.csv": "region,amount\neast,10\nwest,40\neast,20\n"}, v_groupby, f_groupby, 60),
    BenchTask("term-run-tests", "terminal", "Fix calc.py so the provided pytest suite passes.", {"calc.py": "def add(a, b):\n    return a - b\n", "test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"}, v_pytest, f_pytest, 90),
    BenchTask("term-shell-script", "terminal", "Create an executable run.sh that prints 'ok'.", {}, v_shell, f_shell, 60),
    BenchTask("term-makefile", "terminal", "Add a Makefile target named test that runs the Python test suite.", {"test_ok.py": "def test_ok():\n    assert True\n"}, v_makefile, f_makefile, 90),
    BenchTask("plan-multi-file", "planning", "Add VERSION = '1.0' to version.py and export VERSION from package/__init__.py.", {"version.py": "", "package/__init__.py": ""}, v_version, f_version, 60),
    BenchTask("plan-consistency", "planning", "Make docs/guide.md and config.py agree on the default port 8000.", {"config.py": "PORT = 9000\n", "docs/guide.md": "# Guide\n\nThe default port is 9000.\n"}, v_consistency, f_consistency, 60),
    BenchTask("plan-migration", "planning", "Rename the users table to accounts in schema.sql and query.sql.", {"schema.sql": "CREATE TABLE users (id INTEGER, name TEXT);\n", "query.sql": "SELECT * FROM users;\n"}, v_migration, f_migration, 60),
    BenchTask("plan-cross-module", "planning", "Move slugify from utils.py into text/slug.py and update app.py to import it.", {"utils.py": "import re\n\ndef slugify(value):\n    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')\n", "app.py": "from utils import slugify\n\nif __name__ == '__main__':\n    print(slugify('Hello World!'))\n"}, v_slug, f_slug, 60),
)

TASKS_BY_ID: dict[str, BenchTask] = {task.id: task for task in TASKS}
assert len(TASKS) == 30, f"expected 30 tasks, found {len(TASKS)}"


def select_tasks(spec: str | None) -> list[BenchTask]:
    if not spec or spec == "all":
        return list(TASKS)
    ids = [item.strip() for item in spec.split(",") if item.strip()]
    missing = [item for item in ids if item not in TASKS_BY_ID]
    if missing:
        raise KeyError(f"unknown task ids: {missing}")
    return [TASKS_BY_ID[item] for item in ids]


def materialise(task: BenchTask, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write(root, task.files)
