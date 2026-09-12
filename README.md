# claude-work-tools

Command-line tools that a Claude Code session uses to reach the systems work actually
lives in: tasks in ClickUp, and agents running in [herdr](https://herdr.dev) panes.

They are plain executables, not a Claude Code plugin. A plugin cannot carry executables
through a marketplace, so these install on their own and the plugins that use them
declare them as a prerequisite.

## What is here

| Tool | What it does | Needs |
|---|---|---|
| `clickup` | Read and write ClickUp: tasks, lists, comments, attachments, tags, assignees, estimates | python3, an API token |
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
git clone git@github.com:TechTechWizard/claude-work-tools.git
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

**herdr** is configured by herdr itself; these tools only read its state and talk to its
socket.

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

## Known issues

`clickup spaces` returns an empty list on some tokens: the API answers `{"spaces":[]}`
for the workspace even when spaces plainly exist. Nothing in the CLI filters them out.
Use `clickup lists` and `clickup my-tasks`, which are unaffected.

## Requirements

macOS or Linux, python3 (3.7 or newer; only the standard library is used, there is
nothing to pip install), and for the agent tools, herdr.
