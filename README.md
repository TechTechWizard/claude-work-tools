# claude-work-tools

Command-line tools that a Claude Code session uses to reach the systems work actually
lives in: tasks in ClickUp, and agents running in [herdr](https://herdr.dev) panes.

**Only `clickup` is useful on its own.** The other six — `recruit`, `roster`, `tell`,
`await`, `await-mr`, `fire` — drive agents inside herdr panes and do nothing without
herdr installed.

They are plain executables, not a Claude Code plugin. A plugin cannot carry executables
through a marketplace, so these install on their own and the plugins that use them
declare them as a prerequisite.

## What is here

| Tool | What it does | Needs |
|---|---|---|
| `clickup` | Read and write ClickUp: tasks, lists, comments, attachments, tags, assignees, estimates, task deletion | python3, an API token |
| `recruit` | Hire an agent into its own herdr pane, optionally on a fresh git worktree | herdr |
| `roster` | Show who is hired and what each one is doing | herdr |
| `fire` | Dismiss an agent and close its pane | herdr |
| `tell` | Send text to an agent — safer than `herdr agent prompt`, which can paste without submitting and lose the message | herdr |
| `await` | Wait for an agent and tell apart the three states herdr shows identically: finished, asked a question, drifted | herdr |
| `await-mr` | The same wait, tied to a merge request appearing | herdr, glab |

The six herdr tools are useful only if you drive agents from panes. The ClickUp CLI is
useful on its own, and most people come for that one.

## Install

```sh
git clone https://github.com/TechTechWizard/claude-work-tools.git
cd claude-work-tools
./install.sh
```

The installer links the tools into `~/.local/bin` and then reports which prerequisites
you have and which you do not. Nothing is fatal: a missing prerequisite disables the
tools that need it and leaves the rest working.

It links rather than copies, so the checkout has to stay where you put it — moving it
breaks the links, and `./install.sh` from the new location repairs them. `./install.sh
--check` reports what would change without touching anything, and `BIN_DIR=...` installs
somewhere other than `~/.local/bin`. An existing file of your own is moved aside as
`<name>.backup-<timestamp>` rather than overwritten.

## Updating

```sh
git pull && ./install.sh --check
```

The links point into the checkout, so `git pull` alone already updates the tools. The
`--check` is there for the case where a new tool was added and needs a new link.

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

**herdr** is configured by herdr itself; these tools only read its state and talk to its
socket.

## Roles: where `recruit` finds them

A role is a native Claude Code agent definition, and `recruit` accepts it from either of
the two places Claude Code itself reads. The first is a file in `~/.claude/agents/`, known
by its bare basename. The second is any enabled plugin that ships agents, where Claude Code
lists the role under the namespaced name `<plugin>:<role>` — `recruit --roles` prints every
role from both places under the name you can pass back to it.

Both spellings work when you hire. A qualified name names exactly one definition:

```sh
recruit dev-flow:back "fix the import"    # the role from the dev-flow plugin
recruit local:back "fix the import"       # the file ~/.claude/agents/back.md
```

A bare name is expanded whenever exactly one definition answers to it, which is the usual
case and keeps the command short:

```sh
recruit back "fix the import"
```

When two definitions answer to the same bare name, `recruit` refuses and prints both
candidates rather than picking one. This is deliberate, and it is not redundant with
Claude Code: `claude --agent probe` with two plugins defining `probe` picks one of them
silently, which is the failure this refusal exists to prevent.

The same two spellings are valid keys in a project's `.roster`, so a `.roster` written
before its roles moved into a plugin keeps working with no edit. Tab and agent names stay
bare — a hire of `dev-flow:back` lands in a tab called `back-1` — so a namespaced role
never puts a colon into a herdr label.

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
nothing to pip install), and for the agent tools, herdr.

## Licence

MIT. See [LICENSE](LICENSE).
