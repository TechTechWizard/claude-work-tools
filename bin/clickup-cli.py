#!/usr/bin/env python3
"""
ClickUp CLI - Command line interface for ClickUp API
"""

import argparse
import calendar
import json
import mimetypes
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Configuration
CONFIG_DIR = Path(os.environ.get("CLICKUP_CONFIG_DIR") or Path.home() / ".config" / "clickup")
TOKEN_FILE = CONFIG_DIR / "token"
CONFIG_FILE = CONFIG_DIR / "config.json"
API_BASE = "https://api.clickup.com/api/v2"


def parse_due_date(value):
    """Parse a due date into ClickUp's (due_date_ms, due_date_time) pair.

    Due dates are DAY-ONLY by convention — never a time of day. Accepts
    YYYY-MM-DD, 'today', 'tomorrow', or 'none'/'clear' to remove the date.
    Returns (None, None) for a removal.

    The timestamp is noon UTC of that day, not midnight: ClickUp renders the
    date in the workspace timezone, and noon keeps the calendar day identical
    from UTC-11 to UTC+12. Midnight UTC would show up as the previous day for
    anyone west of Greenwich.
    """
    v = value.strip().lower()

    if v in ("none", "clear", "null", ""):
        return None, None

    if v == "today":
        d = date.today()
    elif v == "tomorrow":
        d = date.today() + timedelta(days=1)
    else:
        try:
            d = datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            print(
                "Invalid date. Use YYYY-MM-DD, 'today', 'tomorrow', or 'none' to clear.",
                file=sys.stderr,
            )
            sys.exit(1)

    noon_utc = calendar.timegm((d.year, d.month, d.day, 12, 0, 0, 0, 0, 0))
    return noon_utc * 1000, False


def parse_time_estimate(value):
    """Parse a time estimate given in HOURS into ClickUp's milliseconds.

    Estimates are entered in hours because that is how people think about
    them; ClickUp stores `time_estimate` in milliseconds, so the conversion
    stays inside the CLI and the user never sees a millisecond value.

    Accepts a non-negative number of hours ('56', '1.5') or 'none'/'clear'
    to remove the estimate. Returns None for a removal.
    """
    v = value.strip().lower()

    if v in ("none", "clear", "null", "", "0"):
        return None

    try:
        hours = float(v)
    except ValueError:
        print(
            "Invalid estimate. Use hours as a number, e.g. 56 or 1.5, or 'none' to clear.",
            file=sys.stderr,
        )
        sys.exit(1)

    if hours < 0:
        print("Invalid estimate. Hours cannot be negative.", file=sys.stderr)
        sys.exit(1)

    return int(round(hours * 3600 * 1000))


def normalize_markdown(text):
    """Normalize markdown for ClickUp rendering.

    - Collapse 3+ blank lines into one
    - Remove the blank line after any heading

    ClickUp rebuilds the markdown it stores, and it does that inconsistently:
    the blank line between a heading and a paragraph it drops by itself, but
    the one between a heading and a list it keeps, where it renders as an
    empty block. Hence the blank line is dropped after every ATX heading, not
    only before another heading. For markdown this is safe: a paragraph, a
    list or a quote directly after a heading parses exactly the same.

    Fenced code blocks are left untouched — a '# comment' line inside a code
    sample is not a heading, and the blank line after it must survive.
    """
    # Opening fence, everything up to the matching closing fence, or to the
    # end of the text when the block was never closed.
    fenced_block = r'(^(?:```|~~~).*?(?:^(?:```|~~~)[^\n]*$|\Z))'

    text = text.strip()
    text = re.sub(r'\n{3,}', '\n\n', text)

    # re.split keeps the capturing group, so parts alternate: markdown, code,
    # markdown, ... — only the even ones may contain headings.
    parts = re.split(fenced_block, text, flags=re.MULTILINE | re.DOTALL)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r'(^#{1,6} .+)\n\n', r'\1\n', parts[i], flags=re.MULTILINE)

    return "".join(parts)


_MARKDOWN_ESCAPE = re.compile(r'([*`])')
_LINE_START_MARKER = re.compile(r'^(\s*)(#{1,6} |> |[-+] |\d+[.)] |```|~~~)')


def _escape_markdown(text):
    """Escape the characters in a plain segment that the renderer uses as markup.

    Without this a comment posted with `--plain` and containing a literal
    `**x**` would print exactly like one where ClickUp rendered the bold, and
    the whole point of rendering is that the two look different. Only `*` and
    the backtick are escaped inside a line: the renderer never emits `_` as
    markup (italic comes out as `*text*`), and snake_case identifiers are far
    more common in comments than italic underscores. A block marker at the
    start of a plain line (`# `, `> `, `- `, `1. `, a fence) is escaped for
    the same reason.
    """
    text = _MARKDOWN_ESCAPE.sub(r'\\\1', text)
    return _LINE_START_MARKER.sub(r'\1\\\2', text)


def _render_inline(runs):
    """Render one line's inline runs — (text, attributes) pairs — as markdown.

    Adjacent runs with the same inline attributes are merged first, because
    ClickUp splits a formatted span wherever it likes and `**a****b**` is not
    bold. Whitespace at either edge of a run is moved outside the markers,
    since `**text **` does not parse as bold either.
    """
    merged = []
    for text, attrs in runs:
        inline = {k: v for k, v in (attrs or {}).items() if k in ("bold", "italic", "code", "link")}
        if merged and merged[-1][1] == inline:
            merged[-1][0] += text
        else:
            merged.append([text, inline])

    out = []
    for text, inline in merged:
        if not text:
            continue
        if inline.get("code"):
            fence = "``" if "`" in text else "`"
            out.append(f"{fence}{text}{fence}")
            continue
        core = text.strip()
        if not core:
            out.append(text)
            continue
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = _escape_markdown(core)
        marker = ("**" if inline.get("bold") else "") + ("*" if inline.get("italic") else "")
        core = f"{marker}{core}{marker}"
        if inline.get("link"):
            core = f"[{core}]({inline['link']})"
        out.append(f"{lead}{core}{trail}")
    return "".join(out)


def _table_to_markdown(table):
    """Render a `table-embed` segment as markdown table rows.

    `comment_markdown` turns a markdown table into this segment: cells keyed
    "row:column" from 1, each holding its own inserts. Seen on 2026-09-29.
    """
    cells = table.get("cells") or {}
    width = len(table.get("columns") or [])

    def cell(row, column):
        # Inserts carry the same inline attributes as comment segments; a
        # table row has to stay on one line, so a break inside a cell is a space.
        content = (cells.get(f"{row}:{column}") or {}).get("content") or []
        runs = [(i["insert"].replace("\n", " "), i.get("attributes"))
                for i in content if isinstance(i.get("insert"), str)]
        return _render_inline(runs).strip().replace("|", "\\|")

    rows = [
        "| " + " | ".join(cell(row, column) for column in range(1, width + 1)) + " |"
        for row in range(1, len(table.get("rows") or []) + 1)
    ]
    if rows:
        rows.insert(1, "|" + "---|" * width)
    return rows


def blocks_to_markdown(parts):
    """Render the `comment` segments of a ClickUp comment back to markdown.

    The API takes markdown in (`comment_markdown`) but gives only segments
    back, so this side stays ours: inline attributes (bold, italic, code,
    link) become their markers, block attributes (header, blockquote, list,
    code-block) become the line prefix or the fence.
    ClickUp echoes a line's block attributes on every text segment of the
    line and on its terminating newline — confirmed on a real read-back on
    2026-09-25 — so the newline is what decides the line's block format, and
    the text segments are the fallback for a last line with no newline.

    A comment written from markdown by the API itself carries a list's depth
    as `indent` next to `list`, not inside it, and a quote as an empty
    `blockquote: {}` — both seen on a read-back on 2026-09-28.

    Bookmark segments carry no text, only a url, so the url is printed.
    Anything else without text is skipped rather than printed as JSON.
    """
    lines = []           # (block_attrs, rendered_or_raw_text)
    runs = []            # inline runs of the line being assembled
    line_attrs = {}      # block attributes seen on the line's text segments

    def block_of(attrs):
        return {k: v for k, v in (attrs or {}).items() if k in ("header", "blockquote", "list", "indent", "code-block")}

    def flush(newline_attrs):
        attrs = block_of(newline_attrs) or dict(line_attrs)
        if "code-block" in attrs:
            lines.append((attrs, "".join(t for t, _ in runs)))
        else:
            lines.append((attrs, _render_inline(runs)))
        runs.clear()
        line_attrs.clear()

    for part in parts:
        if part.get("type") == "table-embed":
            if runs:
                flush(None)
            lines.extend(({}, row) for row in _table_to_markdown(part.get("table-embed") or {}))
            continue
        text = part.get("text")
        if text is None:
            url = (part.get("bookmark") or {}).get("url") if part.get("type") == "bookmark" else None
            if url:
                runs.append((url, {}))
            continue
        attrs = part.get("attributes") or {}
        pieces = text.split("\n")
        for i, piece in enumerate(pieces):
            if piece:
                runs.append((piece, attrs))
                line_attrs.update(block_of(attrs))
            if i < len(pieces) - 1:
                flush(attrs)
    if runs:
        flush(None)

    out = []
    in_code = False
    ordered_counters = {}
    for attrs, text in lines:
        code = attrs.get("code-block")
        if code:
            if not in_code:
                language = code.get("code-block") if isinstance(code, dict) else code
                out.append("```" + ("" if language in (None, "", "plain") else str(language)))
                in_code = True
            out.append(text)
            continue
        if in_code:
            out.append("```")
            in_code = False

        if "header" in attrs:
            ordered_counters.clear()
            out.append("#" * int(attrs["header"]) + " " + text)
        elif "blockquote" in attrs:
            ordered_counters.clear()
            out.append("> " + text)
        elif "list" in attrs:
            spec = attrs["list"] if isinstance(attrs["list"], dict) else {"list": attrs["list"]}
            indent = int(spec.get("indent") or attrs.get("indent") or 0)
            for deeper in [d for d in ordered_counters if d > indent]:
                del ordered_counters[deeper]
            kind = spec.get("list")
            if kind == "ordered":
                ordered_counters[indent] = ordered_counters.get(indent, 0) + 1
                marker = f"{ordered_counters[indent]}."
            else:
                # The reference also documents checklists and toggle lists;
                # a checkbox keeps its state, every other kind is a bullet.
                ordered_counters.pop(indent, None)
                marker = {"checked": "- [x]", "unchecked": "- [ ]"}.get(kind, "-")
            out.append("  " * indent + f"{marker} {text}")
        else:
            ordered_counters.clear()
            out.append(text)
    if in_code:
        out.append("```")

    return "\n".join(out).strip()


def get_token():
    """Read API token from config file."""
    if not TOKEN_FILE.exists():
        print(f"Error: Token file not found at {TOKEN_FILE}", file=sys.stderr)
        print(f"Create it with: echo 'your_token' > {TOKEN_FILE}", file=sys.stderr)
        sys.exit(1)
    return TOKEN_FILE.read_text().strip()


def load_config():
    """Read the local config, which holds the workspace and the user this token belongs to."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config):
    """Write the config back, creating the directory on first use."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def get_user_id():
    """The numeric id of the token's owner, discovered once and cached.

    Nothing here may be hardcoded: the same script runs for different people, and a
    baked-in id silently assigns everyone's tasks to whoever built the package.
    """
    config = load_config()
    if config.get("user_id"):
        return str(config["user_id"])

    user = api_request("user").get("user", {})
    user_id = user.get("id")
    if not user_id:
        print("Could not determine the user id from the API.", file=sys.stderr)
        print(f'Set it manually: {{"user_id": "<id>"}} in {CONFIG_FILE}', file=sys.stderr)
        sys.exit(1)

    config["user_id"] = str(user_id)
    save_config(config)
    return str(user_id)


def get_workspace_id():
    """The workspace (ClickUp calls it a team) this token works against, cached after discovery.

    A token with access to exactly one workspace resolves itself. With several there is no
    right guess, so the script prints them and stops rather than picking one.
    """
    config = load_config()
    if config.get("workspace_id"):
        return str(config["workspace_id"])

    teams = api_request("team").get("teams", [])
    if len(teams) == 1:
        workspace_id = str(teams[0]["id"])
        config["workspace_id"] = workspace_id
        save_config(config)
        return workspace_id

    if not teams:
        print("This token has access to no workspace.", file=sys.stderr)
    else:
        print("This token has access to several workspaces:", file=sys.stderr)
        for team in teams:
            print(f'  {team.get("id")}  {team.get("name")}', file=sys.stderr)
        print(f'Pick one: {{"workspace_id": "<id>"}} in {CONFIG_FILE}', file=sys.stderr)
    sys.exit(1)


def resolve_task_id(task_id):
    """Turn a custom task id such as PRD-2854 into the internal one every endpoint accepts.

    The API reads a custom id only with custom_task_ids and team_id in the query; without
    them it answers 401 "Team not authorized", as for an id that does not exist. Internal
    ids never contain a dash, so anything else is passed through untouched.
    """
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*-\d+", task_id):
        return task_id
    task = api_request(f"task/{task_id}?custom_task_ids=true&team_id={get_workspace_id()}")
    return task["id"]


def api_request(endpoint, method="GET", data=None, body=None, content_type="application/json"):
    """Make API request to ClickUp.

    `data` is sent as JSON; `body` with its own `content_type` is sent as is,
    which is how `attach` sends a multipart upload.
    """
    if data:
        body = json.dumps(data).encode("utf-8")

    headers = {"Authorization": get_token(), "Content-Type": content_type}
    req = Request(f"{API_BASE}/{endpoint}", data=body, headers=headers, method=method)

    try:
        with urlopen(req) as response:
            body = response.read().decode("utf-8")
            # A successful DELETE answers with an empty body; everything else is JSON.
            return json.loads(body) if body.strip() else {}
    except HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"API Error {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)


def format_date(timestamp_ms):
    """Convert millisecond timestamp to readable date."""
    if not timestamp_ms:
        return "—"
    try:
        dt = datetime.fromtimestamp(int(timestamp_ms) / 1000)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "—"


def format_hours(duration_ms):
    """Convert millisecond duration to readable hours."""
    if not duration_ms:
        return "—"
    try:
        hours = int(duration_ms) / 3600 / 1000
    except (ValueError, TypeError):
        return "—"
    return f"{hours:.2f}".rstrip("0").rstrip(".") + "h"


def format_size(size_bytes):
    """Convert a byte count to a readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"

    kilobytes = size_bytes / 1024
    if kilobytes < 1024:
        return f"{kilobytes:.1f} KB"

    return f"{kilobytes / 1024:.1f} MB"


def build_file_multipart_body(boundary, field_name, filename, content, content_type):
    """Assemble a multipart/form-data body carrying a single file part.

    urllib has no multipart support and this CLI stays on the standard
    library, so the body is built by hand: opening boundary, part headers,
    the raw bytes, closing boundary. Line endings must be CRLF — that is what
    the format requires, and servers do reject LF-only parts.
    """
    crlf = b"\r\n"
    # RFC 7578 gives no escaping for a quote inside a filename, so a quote is
    # dropped rather than emitted into a header no parser could read.
    safe_filename = filename.replace('"', "")
    disposition = f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_filename}"'

    return b"".join([
        b"--", boundary, crlf,
        disposition.encode("utf-8"), crlf,
        f"Content-Type: {content_type}".encode("utf-8"), crlf,
        crlf,
        content, crlf,
        b"--", boundary, b"--", crlf,
    ])


def print_task_table(title, tasks, column, width, cell):
    """Print tasks as Status | Task | <column> | ID, the shape `my-tasks` and `tasks` share."""
    print(f"\n{title} ({len(tasks)}):\n")
    print(f"{'Status':<15} {'Task':<50} {column:<{width}} {'ID'}")
    print("-" * 95)

    for task in tasks:
        status = task.get("status", {}).get("status", "?")[:14]
        name = task.get("name", "")[:49]
        print(f"{status:<15} {name:<50} {cell(task):<{width}} {task.get('id', '')}")


def cmd_my_tasks(args):
    """Show tasks assigned to me."""
    params = f"assignees[]={get_user_id()}&subtasks=true&include_closed=false"
    if args.status:
        params += f"&statuses[]={args.status}"

    result = api_request(f"team/{get_workspace_id()}/task?{params}")
    tasks = result.get("tasks", [])

    if not tasks:
        print("No tasks found.")
        return

    print_task_table("My Tasks", tasks, "Due", 12, lambda task: format_date(task.get("due_date")))


def cmd_shared(args):
    """List the folders and lists shared with this token.

    This is the entry point to the hierarchy for anyone who is not an admin of the
    workspace: the space endpoints answer an empty list to a member who reaches
    projects through shared folders. The shared payload carries
    each folder's lists inline, so one request is the whole map.
    """
    shared = api_request(f"team/{get_workspace_id()}/shared").get("shared", {})
    folders = shared.get("folders") or []
    lists = shared.get("lists") or []

    if not folders and not lists:
        print("\nNothing is shared with this token.")
        return

    if folders:
        print(f"\nFolders ({len(folders)}):\n")
        print(f"{'Name':<40} {'ID':<16} Tasks")
        print("-" * 66)
        for folder in folders:
            name = folder.get("name", "")[:39]
            print(f"{name:<40} {folder.get('id', ''):<16} {folder.get('task_count', '')}")
            for lst in folder.get("lists") or []:
                list_name = "  └─ " + lst.get("name", "")[:34]
                print(f"{list_name:<40} {lst.get('id', ''):<16} {lst.get('task_count', '')}")

    if lists:
        print(f"\nLists outside folders ({len(lists)}):\n")
        print(f"{'Name':<40} {'ID':<16} Tasks")
        print("-" * 66)
        for lst in lists:
            name = lst.get("name", "")[:39]
            print(f"{name:<40} {lst.get('id', ''):<16} {lst.get('task_count', '')}")


def cmd_tasks(args):
    """Show tasks from a list."""
    params = "include_closed=false"
    if args.assignee:
        params += f"&assignees[]={args.assignee}"

    result = api_request(f"list/{args.list_id}/task?{params}")
    tasks = result.get("tasks", [])

    if not tasks:
        print("No tasks found.")
        return

    def first_assignee(task):
        assignees = task.get("assignees", [])
        return assignees[0].get("username", "—")[:14] if assignees else "—"

    print_task_table("Tasks", tasks, "Assignee", 15, first_assignee)


def cmd_task_markdown(args):
    """Print the markdown ClickUp actually stores for a task.

    ClickUp rebuilds the markdown it is given on save, so the stored version
    can differ from what was sent. The plain `task` output shows the
    `description` field, which is the same text with the markup stripped and
    therefore useless for checking that. This prints `markdown_description`
    verbatim — no normalization, no truncation — so the raw stored markup can
    be compared against what was intended.
    """
    task = api_request(f"task/{args.task_id}?include_markdown_description=true")
    markdown = task.get("markdown_description") or ""

    if not markdown.strip():
        print(f"Task {args.task_id} has no markdown description.")
        return

    print(markdown)


def cmd_task(args):
    """Show task details."""
    if args.markdown:
        cmd_task_markdown(args)
        return

    task = api_request(f"task/{args.task_id}")

    print(f"\n{'='*60}")
    print(f"Task: {task.get('name', 'N/A')}")
    print(f"{'='*60}")
    print(f"ID:       {task.get('id', 'N/A')}")
    # A subtask reports its parent's id here, and `—` means there is none. Without
    # this line the only way to confirm `create --parent` did its job was to import
    # the script as a module and call the API by hand.
    print(f"Parent:   {task.get('parent') or '—'}")
    print(f"Status:   {task.get('status', {}).get('status', 'N/A')}")
    print(f"Priority: {task.get('priority', {}).get('priority', 'none') if task.get('priority') else 'none'}")

    assignees = [a.get("username", "") for a in task.get("assignees", [])]
    print(f"Assignees: {', '.join(assignees) if assignees else '—'}")

    print(f"Due:      {format_date(task.get('due_date'))}")
    print(f"Estimate: {format_hours(task.get('time_estimate'))}")
    print(f"Spent:    {format_hours(task.get('time_spent'))}")
    print(f"Created:  {format_date(task.get('date_created'))}")
    print(f"URL:      {task.get('url', 'N/A')}")

    description = task.get("description", "")
    if description:
        print(f"\nDescription:\n{'-'*40}")
        print(description)


def cmd_create(args):
    """Create a new task."""
    data = {
        "name": args.name,
        "assignees": [int(get_user_id())]
    }

    if args.parent:
        data["parent"] = args.parent
    if args.description:
        data["markdown_description"] = normalize_markdown(args.description)
    if args.priority:
        data["priority"] = args.priority
    if args.due:
        due_ms, due_time = parse_due_date(args.due)
        data["due_date"] = due_ms
        data["due_date_time"] = due_time

    result = api_request(f"list/{args.list_id}/task", method="POST", data=data)

    print(f"\nTask created!")
    print(f"Name: {result.get('name')}")
    print(f"ID:   {result.get('id')}")
    print(f"URL:  {result.get('url')}")


def cmd_comments(args):
    """Show comments on a task, each rendered back to markdown.

    The API answers with `comment_text`, which is the text with every
    attribute stripped, and `comment`, the segments with their attributes.
    Printing the stripped text made a rendered bold and a plain word look the
    same, so nothing about the formatting of a posted comment could be checked
    from this output. The segments are rendered instead, and a literal `*` or
    backtick in plain text is escaped, so that `**x**` in the output always
    means ClickUp holds it as bold.
    """
    result = api_request(f"task/{args.task_id}/comment")
    comments = result.get("comments", [])

    if not comments:
        print("No comments found.")
        return

    for c in comments:
        user = c.get("user", {}).get("username", "?")
        date = format_date(c.get("date"))
        print(f"--- {user} ({date}) ---")
        print(blocks_to_markdown(c.get("comment", [])))
        print()


def cmd_comment(args):
    """Add comment to a task.

    Markdown goes into `comment_markdown`, and ClickUp turns it into rich text
    itself. The field is not in the API reference: it surfaced on 2026-09-28
    in the 400 answer "Provide either comment_text or comment_markdown", and a
    read-back confirmed headings, bold, italic, code, links, all three list
    kinds including checklists, quotes and fenced code. `comment_text` stores
    the markup literally and `markdown_content` is ignored.

    `--plain` posts through `comment_text` when the text must land verbatim.
    """
    if getattr(args, "plain", False):
        data = {"comment_text": args.text, "notify_all": False}
    else:
        data = {"comment_markdown": normalize_markdown(args.text), "notify_all": False}

    result = api_request(f"task/{args.task_id}/comment", method="POST", data=data)

    print(f"Comment added (ID: {result.get('id')})")


def cmd_delete_comment(args):
    """Delete a comment."""
    api_request(f"comment/{args.comment_id}", method="DELETE")
    print(f"Comment {args.comment_id} deleted.")


def cmd_delete(args):
    """Delete a task."""
    # Read the task first so the confirmation names what went, not just an id — an id
    # alone tells you nothing about whether you deleted the right thing.
    task = api_request(f"task/{args.task_id}")
    name = task.get("name", "(no name)")
    api_request(f"task/{args.task_id}", method="DELETE")
    print(f"Deleted {args.task_id}: {name}")
    print("ClickUp keeps deleted tasks in the workspace Trash for 30 days.")


def cmd_tag(args):
    """Add or remove a tag on a task."""
    from urllib.parse import quote
    tag = quote(args.tag_name)
    if args.remove:
        api_request(f"task/{args.task_id}/tag/{tag}", method="DELETE")
        print(f"Tag '{args.tag_name}' removed from {args.task_id}.")
    else:
        api_request(f"task/{args.task_id}/tag/{tag}", method="POST")
        print(f"Tag '{args.tag_name}' added to {args.task_id}.")


def cmd_attach(args):
    """Attach a file to a task."""
    path = Path(args.file_path).expanduser()

    if not path.is_file():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_bytes()
    if not content:
        print(f"Error: File is empty: {path}", file=sys.stderr)
        sys.exit(1)

    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boundary = uuid.uuid4().hex
    body = build_file_multipart_body(
        boundary.encode("ascii"), "attachment", path.name, content, content_type
    )

    result = api_request(
        f"task/{args.task_id}/attachment", method="POST", body=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )

    print("Attachment uploaded!")
    print(f"File: {result.get('title') or path.name}")
    print(f"Size: {format_size(len(content))}")
    print(f"URL:  {result.get('url', 'N/A')}")


def cmd_update(args):
    """Update task."""
    data = {}
    if args.status:
        data["status"] = args.status
    if args.name:
        data["name"] = args.name
    if args.priority:
        data["priority"] = int(args.priority)
    if args.description:
        data["markdown_description"] = normalize_markdown(args.description)
    # Assignees are a delta, not a replacement: "add" and "rem" can go in one request,
    # which is how a task is handed over (--assignee <id> --unassign me). "me" resolves
    # to the token owner, discovered from the API on first use.
    assignees = {}
    if args.assignee:
        assignee_id = get_user_id() if args.assignee.lower() == "me" else args.assignee
        assignees["add"] = [int(assignee_id)]
    if args.unassign:
        unassign_id = get_user_id() if args.unassign.lower() == "me" else args.unassign
        assignees["rem"] = [int(unassign_id)]
    if assignees:
        data["assignees"] = assignees
    if args.due:
        due_ms, due_time = parse_due_date(args.due)
        data["due_date"] = due_ms
        data["due_date_time"] = due_time
    if args.time_estimate is not None:
        data["time_estimate"] = parse_time_estimate(args.time_estimate)

    if not data:
        print("Nothing to update. Use --status, --name, --priority, --description, --assignee, --unassign, --due, or --time-estimate", file=sys.stderr)
        sys.exit(1)

    result = api_request(f"task/{args.task_id}", method="PUT", data=data)

    print(f"Task updated!")
    print(f"Name:   {result.get('name')}")
    print(f"Status: {result.get('status', {}).get('status')}")
    if args.due:
        print(f"Due:    {format_date(result.get('due_date'))}")
    if args.time_estimate is not None:
        print(f"Estimate: {format_hours(result.get('time_estimate'))}")
    assignees = [a.get("username", "") for a in result.get("assignees", [])]
    if assignees or args.unassign:
        print(f"Assignees: {', '.join(assignees) if assignees else '—'}")


def main():
    parser = argparse.ArgumentParser(
        description="ClickUp CLI - Command line interface for ClickUp",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # my-tasks
    p_my = subparsers.add_parser("my-tasks", help="Show my tasks")
    p_my.add_argument("--status", help="Filter by status")
    p_my.set_defaults(func=cmd_my_tasks)

    # tasks
    p_tasks = subparsers.add_parser("tasks", help="Show tasks from list")
    p_tasks.add_argument("list_id", help="List ID")
    p_tasks.add_argument("--assignee", help="Filter by assignee ID")
    p_tasks.set_defaults(func=cmd_tasks)

    # task
    p_task = subparsers.add_parser("task", help="Show task details")
    p_task.add_argument("task_id", help="Task ID")
    p_task.add_argument("--markdown", "-m", action="store_true", help="Print the stored markdown description instead of the task card")
    p_task.set_defaults(func=cmd_task)

    # create
    p_create = subparsers.add_parser("create", help="Create task")
    p_create.add_argument("list_id", help="List ID")
    p_create.add_argument("name", help="Task name")
    p_create.add_argument("--description", "-d", help="Task description")
    p_create.add_argument("--priority", "-p", type=int, choices=[1, 2, 3, 4], help="Priority (1=urgent, 4=low)")
    p_create.add_argument("--due", help="Due date, day only: YYYY-MM-DD | today | tomorrow")
    p_create.add_argument("--parent", help="Parent task ID (creates subtask)")
    p_create.set_defaults(func=cmd_create)

    # comments (read)
    p_comments = subparsers.add_parser("comments", help="Show comments on task")
    p_comments.add_argument("task_id", help="Task ID")
    p_comments.set_defaults(func=cmd_comments)

    # comment (add)
    p_comment = subparsers.add_parser("comment", help="Add comment to task (markdown is rendered)")
    p_comment.add_argument("task_id", help="Task ID")
    p_comment.add_argument("text", help="Comment text; markdown is converted to ClickUp rich text")
    p_comment.add_argument("--plain", action="store_true", help="Post verbatim, without converting markdown")
    p_comment.set_defaults(func=cmd_comment)

    # delete-comment
    p_del_comment = subparsers.add_parser("delete-comment", help="Delete a comment")
    p_del_comment.add_argument("comment_id", help="Comment ID")
    p_del_comment.set_defaults(func=cmd_delete_comment)

    # shared
    p_shared = subparsers.add_parser("shared", help="List folders and lists shared with you")
    p_shared.set_defaults(func=cmd_shared)

    # delete
    p_delete = subparsers.add_parser("delete", help="Delete a task")
    p_delete.add_argument("task_id", help="Task ID")
    p_delete.set_defaults(func=cmd_delete)

    # tag
    p_tag = subparsers.add_parser("tag", help="Add/remove a tag on a task")
    p_tag.add_argument("task_id", help="Task ID")
    p_tag.add_argument("tag_name", help="Tag name (e.g. web, api)")
    p_tag.add_argument("--remove", action="store_true", help="Remove the tag instead of adding")
    p_tag.set_defaults(func=cmd_tag)

    # attach
    p_attach = subparsers.add_parser("attach", help="Attach a file to a task")
    p_attach.add_argument("task_id", help="Task ID")
    p_attach.add_argument("file_path", help="Path to the file to attach")
    p_attach.set_defaults(func=cmd_attach)

    # update
    p_update = subparsers.add_parser("update", help="Update task")
    p_update.add_argument("task_id", help="Task ID")
    p_update.add_argument("--status", "-s", help="New status")
    p_update.add_argument("--name", "-n", help="New name")
    p_update.add_argument("--priority", "-p", choices=["1", "2", "3", "4"], help="Priority")
    p_update.add_argument("--description", "-d", help="Task description")
    p_update.add_argument("--assignee", "-a", help="Add an assignee: user ID or 'me'")
    p_update.add_argument("--unassign", help="Remove an assignee: user ID or 'me'. `create` always assigns the creator; this is how to undo it or hand a task over")
    p_update.add_argument("--due", help="Due date, day only: YYYY-MM-DD | today | tomorrow | none (clears it)")
    p_update.add_argument("--time-estimate", help="Estimate in hours: 56 | 1.5 | none (clears it)")
    p_update.set_defaults(func=cmd_update)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if getattr(args, "task_id", None):
        args.task_id = resolve_task_id(args.task_id)

    args.func(args)


if __name__ == "__main__":
    main()
