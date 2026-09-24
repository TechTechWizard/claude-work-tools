# claude-work-tools

The command-line tool a Claude Code session uses to reach ClickUp: tasks, lists,
comments, attachments, tags, assignees, estimates. It is a plain executable, not a skill:
a skill cannot carry an executable, so this installs on its own and the `clickup` skill
from [skills](https://github.com/TechTechWizard/skills) names it as a prerequisite.

The six wrappers that drove agents in herdr panes used to live here too. They are the
teamlead set now and moved to [orchestrator](https://github.com/TechTechWizard/orchestrator)
on 24.09.2026; a developer who comes for the ClickUp CLI does not need them.

## What is here

| Tool | What it does | Needs |
|---|---|---|
| `clickup` | Read and write ClickUp: tasks, lists, comments, attachments, tags, assignees, estimates, task deletion | python3, an API token |

## Install

```sh
git clone https://github.com/TechTechWizard/claude-work-tools.git
cd claude-work-tools
./install.sh
```

The installer links the CLI into `~/.local/bin` as `clickup` and then reports which
prerequisites you have and which you do not: python3 and the token.

It links rather than copies, so the checkout has to stay where you put it — moving it
breaks the links, and `./install.sh` from the new location repairs them. `./install.sh
--check` reports what would change without touching anything, and `BIN_DIR=...` installs
somewhere other than `~/.local/bin`. An existing file of your own is moved aside as
`<name>.backup-<timestamp>` rather than overwritten.

## Updating

```sh
git pull && ./install.sh --check
```

The link points into the checkout, so `git pull` alone already updates the CLI. The
`--check` is there for the case where the link went missing.

## Configuration

**ClickUp token.** The only thing you have to supply:

```sh
mkdir -p ~/.config/clickup
echo 'pk_your_personal_token' > ~/.config/clickup/token
```

Get the token in ClickUp under Settings → Apps → API Token. It is personal: everything
the CLI does, it does as you.

**Everything else discovers itself.** On first use the CLI asks the API who the token
belongs to and which workspace it can see, and caches both in
`~/.config/clickup/config.json`. A token with access to several workspaces cannot be
guessed at, so the CLI prints them and asks you to pick one by writing
`{"workspace_id": "<id>"}` into that file.

**A second configuration, for tests.** `CLICKUP_CONFIG_DIR` moves both the token and the
cached ids somewhere else for one run:

```sh
CLICKUP_CONFIG_DIR=/tmp/clickup-test clickup my-tasks
```

That keeps an automated run away from your own credentials. It does not isolate ClickUp
itself: there is no sandbox workspace, so anything a test creates is a real task. Point
such tests at a list kept for them, and clean up with `clickup delete`.

## Two things the ClickUp API does not tell you

**`create` assigns you.** Every task created through the CLI lands in the creator's list.
To hand it over, or to leave it unassigned:

```sh
clickup update <task_id> --unassign me
clickup update <task_id> --assignee <their_id> --unassign me   # hand-over in one call
```

Assignees are a delta on the API, never a replacement, which is why both flags travel
together.

**A bad id looks like an auth error.** `task`, `comments` and `update` on an id that does
not exist — or that lives in a workspace your token cannot see — both return
`401 {"err":"Team not authorized","ECODE":"OAUTH_027"}`. Check the id before you go
looking at your token.

**Deleting a task.** `clickup delete <task_id>` names what it removed before it goes, and
ClickUp keeps deleted tasks in the workspace Trash for 30 days, so a wrong id is
recoverable through the web interface.

**Finding a list id.** `clickup shared` prints every folder shared with you and the lists
inside it, with task counts — that id is what `tasks`, `create` and the rest take.

Start there rather than with `spaces`: `GET /team/{id}/space` answers `{"spaces":[]}` for
anyone who reaches projects through shared folders rather than by owning the space, which
is most people, and both `folders` and `lists` need a space id that such a token can never
obtain. `clickup spaces` says so plainly instead of printing an empty table.

## Two GitLab queries the tools do not wrap

Both are one `glab` command with no assembly around them, so they are written down here
instead of being given a tool or a skill of their own:

```sh
glab mr list --reviewer=@me   # what is waiting on me for review
glab ci status                # the pipeline on the current branch
```

## Known issues

None recorded. What used to be here — an empty `spaces` listing with no way forward — is
the paragraph above, and it now has a route that works.

## Requirements

macOS or Linux, python3 (3.7 or newer; only the standard library is used, there is
nothing to pip install).

## Licence

MIT. See [LICENSE](LICENSE).
