# 🔨 agent-frolic 🐓

**See who every open pull request is waiting on, and which ones are waiting on you.**

Aaah!  One pull request has sat for three days, waiting on a review nobody knew was theirs.
Another is approved and stuck behind the one it is stacked on. Dependabot asked your team for
five reviews and everyone on it assumed someone else had them. agent-frolic is a Mac app that
puts the open pull requests of the repositories you choose on one board, with whatever you can
get moving marked in yellow, so the work keeps moving and nothing waits on you without your
knowing. (A frolic is an Amish barn raising: everyone turns up, and the barn goes up in a day.)

It is designed to work with [agent-farm](https://github.com/djp3/agent-farm) and
[agent-grange](https://github.com/djp3/agent-grange), which bring your team's agents onto the
board: see [below](#made-to-work-with-agent-farm-and-agent-grange).

**[Download for Mac](https://github.com/djp3/agent-frolic/releases/latest)** · macOS 27+ on
Apple silicon · free and open source

![agent-frolic showing the open pull requests of two repositories: a card for each person and team something waits on, the pull requests drawn as stacks, each with its age as a bar and a figure, each repository's line ending in the countdown to its next read, what needs effort, the drawer for the selected one, and the keys along the foot](docs/screenshot.png)

### What is yours to move is yellow

A yellow dot marks every move that is yours: a pull request of your own that needs you, a
review asked of you by name, and a review asked of a team you are on, drawn under the team's
name (`● Platform`). Each repository in the top line carries `●n`, how many you can get moving
there, and `n` jumps to the next thing that needs effort.

### It knows who has the next move

For every pull request the board says who must act next, worked out from GitHub's own records:
the author while it is a draft, conflicts or fails its checks; the reviewers once they are
asked; the author again when changes are requested or comments are left to resolve; and whoever
may merge once it is approved. There are no labels to keep up and no conventions to follow.

### Stacks drawn as stacks

Pull requests built on other pull requests are drawn as a tree, under their repository, base
branch and the commit they stand on. One that is ready but waits for the one beneath it says so
(`merge once #212 lands`) and names who that one is really waiting on.

### What needs effort, most urgent first

Below the list, everything that waits on a person or a team, the longest quiet first, with
what it holds up. An agent of yours that is on no pull request and is at its prompt comes
before them. Beside it, a drawer for the selected pull request: owner, who it waits on,
each reviewer's latest word, comments to resolve, checks, branch, the name that puts an agent
on it, and its address. `o` opens it on GitHub, `c` copies the name, and `C` copies it as a
`/rename` command for Claude Code.

### Changes arrive as they happen

Turn on webhooks and GitHub tells the app the moment something changes; the row washes pale
for a moment and the ticker says what happened. When GitHub says which pull request changed
(a review asked for or given, a comment, a check, new commits), that one is read on its own,
within seconds: it costs a small part of what reading the repository does, so the board keeps
up with a review as it goes. What names no pull request (a push to a branch, a branch made or
deleted) has the whole repository read, in its turn. All the reads GitHub's messages ask for
are held to half of what GitHub allows the token in an hour, shared among the repositories
shown, so a busy run of checks cannot use it up.

Each repository also has a timer of its own: one GitHub sends no webhooks for is read again
every five minutes, and one it does, every half hour, to catch a delivery that never came.
Any read of the whole repository starts its timer again, and `u` reads them all now.

The half hour is for a repository whose webhook can be relied on. It is back to five minutes
while its last read failed, while GitHub is still working out whether a pull request there can
merge (it sends no webhook when it knows), and while the tunnel does not lead back to the app.
After the Mac has slept, the repositories with webhooks are read at once: what GitHub sent
meanwhile reached nobody.

### Your token in your Keychain. Your work on your Mac.

The GitHub token, and the two secrets webhooks need, are kept in the macOS Keychain and nowhere
else: never in a file, a log or an error message. The app talks to GitHub, to ngrok if you turn
webhooks on, and to the agent-grange on this Mac if one is running (at `127.0.0.1` only), and it
checks for an update once a day. It reads your pull requests and never changes one. The only
thing it writes on GitHub is its own webhook, on the repositories you ask it to.

## Install

1. Download `agent-frolic-<version>.zip` from the
   [latest release](https://github.com/djp3/agent-frolic/releases/latest), unzip it and drag
   `agent-frolic.app` to Applications.
2. Open it. The app is notarized, so it opens like any other download.

Updates take care of themselves: the app checks GitHub once a day and offers each new release
with its notes. **agent-frolic ▸ Check for Updates…** checks now. An update is accepted only
when it is signed with this project's key, and so is the list that announces it.

You need macOS 27 or later on Apple silicon, and a GitHub token. On macOS 26 or earlier the
app does not open: macOS says it needs a newer system. The Python inside it is built for
macOS 27, and the build holds the whole app to that.

## Setting up

The app opens on its setup screen until it has a token. `s` goes from the board to the setup
and back. It has four parts.

**1. The GitHub token.** Press `t` and paste one. It is checked with GitHub and then kept in
your Keychain. A classic token needs the `repo` scope. A fine-grained token needs, on each
repository, Pull requests (read), Contents (read) and Metadata (read), plus Webhooks (read and
write) if you want webhooks; it sees the teams of one organization only.

**2. The repositories.** Press `a` and type `owner/name` or paste the repository's address.
Space shows or hides one on the board, and `b` names its long-lived branches (such as
`develop`), which are never drawn as a stack.

**3. Webhooks**, if you want changes the moment they happen. GitHub delivers them to a public
address, and [ngrok](https://ngrok.com) gives this Mac one: press `w` and enter your ngrok
authtoken and domain (a free account has one domain). The app opens the tunnel while it is
running, and registers a webhook on each shown repository you administer. Repositories you do
not administer cannot have one, and are read every five minutes instead. The app keeps checking
that the tunnel still leads back to it; while it does not, every repository is read every five
minutes.

**4. Agents.** There is nothing to set here. It says whether an
[agent-grange](https://github.com/djp3/agent-grange) answers on this Mac, how many of your team's
Macs it hears from, how many agents it reports, and how many of those are on the pull requests
on your board. Without a grange the board is of people and teams alone: see
[below](#made-to-work-with-agent-farm-and-agent-grange).

| key | on the setup screen |
|-----|---------------------|
| t / f | paste a token / forget it |
| a / x | add a repository / remove the selected one |
| ↑ ↓ | pick a repository |
| shift+↑ shift+↓, or [ ] | move it up or down the list |
| space | show or hide it on the board |
| b | set its long-lived branches |
| w / W | set up webhooks / turn them off |
| r | register the webhooks again |
| p | switch between the log and a plain list of the pull requests |
| u | read GitHub now |
| s or esc | back to the board |
| q | quit |

## The board

From top to bottom:

- **The top line**: every repository in your list, the ones on screen underlined, each with
  `●n` (what you can get moving there) and `○n` (other people and teams something there waits
  on).
- **The cards**: you, then the teams you are on, then everyone else something waits on, the
  longest wait first. A yellow dot on a card means something on it is yours to act on.
- **The list**, in two columns. On the left, who: owner, state, and who it waits on. On the
  right, the tree: repository, base branch, commit, then the pull requests in stack order, each
  with its reviews, checks, progress and age. Each repository's line ends in its own timer:
  a thin bar whose white is the time left until that repository is next read, and after it
  the minutes left, counted up to nought (`-4m`).
- **The band**: what needs effort, and the drawer for the selected pull request.
- **The ticker**: the last thing that happened.
- **The foot**: the keys, each lit, with what it does beside it. In a narrow window the least
  needed are left out; the way to the setup and the way out always stay.

The title bar reads 🐓 agent-frolic and the version (v0.1.4, say), then one 🔨 for each
repository being watched (on screen, with a token GitHub has accepted), so the Dock, Mission
Control and the window switcher show how many the board is keeping an eye on.

### Keys

| key | action |
|-----|--------|
| ↑ ↓ | pick a pull request |
| click | pick the pull request under the pointer, in the list or where it needs effort |
| o | open it on GitHub in your browser |
| c | copy the name that puts an agent on it (see [agents](#made-to-work-with-agent-farm-and-agent-grange)) |
| page up / page down, home / end | scroll the list (the wheel does too) |
| n | jump to the next thing that needs effort |
| tab / shift+tab | step the focus through the cards; what is not theirs dims |
| esc | back to everyone |
| m | the repository menu (space shows or hides one) |
| u | read GitHub now |
| d | fold or unfold the drawer |
| s | the setup |
| q | quit |

### Reading a row

Each pull request starts with one mark for where it is in its review:

| mark | meaning |
|------|---------|
| ◌ | a draft |
| ⚠ | it has a merge conflict |
| ○ | open, and no review has been asked |
| ◔ | in review |
| ↻ | a re-review has been asked of everyone who reviewed before |
| ✗ | changes requested |
| ✓ | approved, or reviewed with nothing left to do |
| ▲ | ready to merge |

The **review** column shows each reviewer's trail, oldest first, then their name:

| mark | that reviewer |
|------|---------------|
| ✓ | approved |
| ✗ | asked for changes that are not all resolved yet |
| ● (green) | asked for changes, and everything they raised is resolved |
| ◇ | left comments that are still to resolve |
| ◆ | left comments, and none is left to resolve |
| ◔ | has been asked, and has not answered |
| ⣾ (turning) | is Copilot, asked and not answered yet: it is reviewing now |
| – | had their review dismissed |

`◇2` at the end of the column counts the comments still to resolve on the pull request.

Copilot sets to work the moment it is asked, so while its review is awaited its mark turns, as
an agent's does while it works: in this column, before its name where the pull request waits
on it, on its card at the top, and in the drawer, which says "reviewing". Where the pull
request waits on it and on its card the wheel is green and a green light runs through its
name, as for an agent at work. GitHub says only that Copilot was asked, not how far along it
is: the wheel turns until its review arrives.

The **ci** column is `✓` when the checks pass, `✗` when they fail, `…` while they run, `·`
when there are none, and `$` when GitHub did not run them because the account could not pay
for them, which is nobody's to fix in the code.

**Age** is how long since the pull request last changed: a thin bar, then the figure in days
and hours (`128d 11h`, `21h`, and `0h` within the hour). The bar fills from the left, in
violet, a quarter of it each for minutes, hours, days and weeks, and is full at three months.
Within the hour, where the figure is `0h` throughout, the bar still tells roughly how long:
none of it is filled for the first four minutes, one cell until a quarter of an hour, two
until the hour. A narrowing window gives up progress first, then the age's bar, then its
figure. A repository's line keeps its bar and its countdown for as long as they fit there.

The column is time from an event or to one, and the filled part of a bar is anchored at the
event. A pull request's starts at its last change, at the left, and grows away from it. A
repository's ends at its next read, at the right, and shrinks toward it: when it is gone and the
figure is `0m`, the repository is read again. The two time different things, so they are not
one color: an age is violet, a repository's timer white.

### Teams

A review asked of a team you are on is yours to give, so it is drawn as yours, under the
team's name, and the team has a card of its own right after yours. It is on the team's card
and on nobody else's: one request is one job. A team you are not on reads `team/slug` and is
someone else's. So is a request to your own team on a pull request you wrote, since GitHub
does not let you review your own.

The app asks GitHub only which teams you are on, never who else is on them, once when the
token is accepted and again on `u`. The list is kept in memory and written to no file.

## Who has the next move

The rule, checked top to bottom. The first line that fits decides.

| what GitHub shows | who must act | their move |
|-------------------|--------------|------------|
| it is a draft | the author | finish the draft |
| it has a merge conflict | the author | resolve the conflict |
| its checks are failing | the author | fix the checks |
| its checks are still running | nobody | |
| reviewers were asked and have not answered | those reviewers | review, or re-review |
| changes were requested, or comments are left to resolve | the author | resolve them |
| it is approved, or nothing is left to do | the author | merge (after the one it is stacked on) |
| nobody was asked and nobody has reviewed | the author | request a review |

Someone whose pull request comes from a fork can neither merge nor ask for a review, so those
two moves pass to the repository's maintainer. When an organization owns the repository and
your access there lets you make the move, it is yours: it shows as "you @ Acme" (the
organization's name) with the yellow dot.

## Made to work with agent-farm and agent-grange

agent-frolic is one of three programs for teams where coding agents do much of the work.
agent-farm and agent-frolic each work without the others. Together with agent-grange they are
designed to show a whole team's agents on the team's pull requests:

- **[agent-farm](https://github.com/djp3/agent-farm)** watches the agents on your Mac: which
  are at work, which are idle, and which are waiting for you.
- **[agent-grange](https://github.com/djp3/agent-grange)** carries that between Macs. Each
  person runs one. Their agent-farm reports to it, and the granges of a team pass what they
  know on to each other over a private network.
- **agent-frolic** puts it on the pull requests: whose agents are on which one, beside who
  each pull request waits on.

```
   your Mac                                       a teammate's Mac

   agent-farm ──▶ agent-grange ◀──────────────▶ agent-grange ◀── agent-farm
                       │                              │
                       ▼                              ▼
                  agent-frolic                   agent-frolic
```

On the board an agent sits beneath the person who runs it, on the pull request it is working
on, and a pull request that an agent is at work on comes off your list: it is moving. Someone
the pull request does not wait on, who has an agent on it all the same, is listed after those
it waits on, in fainter ink.

| mark | that agent |
|------|------------|
| ⣾ (turning, green) | is at work |
| ⣾ (turning, white) | is idle, and waits on a command it started that is still running: a test run, a watch on the checks |
| ▷ | is at its prompt: it has asked its human something, or it is idle and the pull request's next move is its human's |
| · | is idle, and the pull request waits on someone else |
| ? | cannot be said: the agent-farm on its Mac has gone quiet, or says something this version does not know |

An agent whose wheel turns is the one thing on the board that moves, so it is found at a
glance: the wheel goes round once a second, and a light runs through the agent's name and its
state's word, in the wheel's color. The state's word is "working" for the green one and
"command" for the white.

A command is not work. An agent that waits on one is idle as far as the rest of the board
goes: its pull request stays on your list when the next move is yours, with the yellow dot,
and the agent is counted among those that sit idle for it. A command can run for hours, a
server left up or a watch on something, and it would otherwise read as work for as long.

An agent is tied to its pull request by the name of its session, which says the repository and
the pull request's number: `<owner>-<repo>-pr-<number>`. An agent on pull request 98 of
`acme/web` is named `acme-web-pr-98`.

- The owner is the user or organization the repository belongs to, whoever runs the agent.
- Upper and lower case are the same.
- It may come anywhere in the name, as long as it starts the name or follows a space.
- Whatever the name goes on to say (`acme-web-pr-98 fix tests`) is shown on the agent's line,
  as what it is at.
- What the pull request's branch and title say plays no part.

Nobody has to work the name out. The drawer shows it for the selected pull request, and `c`
copies it, ready to paste where the session is named. `C` copies the whole command that
names a Claude Code session so, `/rename acme-web-pr-98`: paste it at the agent's prompt and
press return.

**Before there is a pull request**, an agent's name says the repository alone, as a word of
its own: `acme-web`, or `acme-web reflow fix`. (Run on with more, as in `acme-web-reflow-fix`,
it is not read: where the repository's name ends could only be guessed.)

- The agent is drawn on a line of its own under that repository's heading, with the person who
  runs it, what it is doing, and what its name goes on to say.
- Idle, it is at its prompt: it has nobody but its human to wait on. Your own, at its prompt,
  counts among what you can get moving (●) and is first in the list of what needs effort.
- It is not a pull request, so the arrows and `n` pass it by.
- When you open a pull request in a repository where you have such an agent, the ticker gives
  the name to rename its session to, and the board selects the new pull request, so `c` copies
  that name and `C` the `/rename` command with it. The agent moves onto the pull request once
  its session has the new name. (When an agent of yours is on the new pull request already,
  named ahead of it, there is nothing to rename, and the ticker says only that you opened it.)

**A name that says a pull request that is not open** is read as its repository alone. That is
an agent named ahead of the pull request it is about to open, or one still named for a pull
request that has merged or closed: `acme-web-pr-98`, when `acme/web` has no pull request 98
open.

- The agent is drawn under the repository's heading, as one with no pull request yet is, and
  all of the above goes for it too. The end of its line says which pull request is not open
  (`#98 is not open`).
- It moves onto the pull request as soon as GitHub has been read and the pull request is open.
- Until a repository has been read at all, nothing is known of its pull requests, and an agent
  named for one of them is not drawn yet.

**Each agent has a number**, shown beside it: `agent 07`. Each person's agents are numbered
together across the whole board, whichever pull request or repository each is on. Yours run
from 01 to however many you have on the board, with none left out, and so do a teammate's.
"you · answer agent 07" means one session of yours.

- A new agent takes the next number.
- When an agent leaves the board, the one with the highest number takes the number it left, so
  no number is ever missing. Every other agent keeps its own.
- An agent keeps its number when it moves: onto its pull request when that opens, and back
  under its repository when that has merged.
- A session you rename keeps its number, when the board draws it under both names: the new name
  takes the number the old one left.
- An agent that is not drawn has no number. One whose repository you hide gives its number up,
  and takes the next one when it is back.
- The numbers are this board's own. A teammate's board may number the same agents differently,
  and the numbers start again when agent-frolic does.

An agent whose name says neither a pull request nor a repository is not drawn, and nothing is
wrong with it. One whose name has `-pr-<number>` in it and is still drawn nowhere is listed in
part 4 of the setup, with why: the repository is not on your list, or the name leaves out
whose repository it is.

Two repositories can be written the same way (`acme/web-api` and `acme-web/api` are both
`acme-web-api`). If each has an open pull request of the same number, a name fits both. The
agent is then drawn on neither, since the board does not guess. For as long as that lasts, the
end of the ticker's line says an agent is not shown, and the setup says which two pull requests
its name fits. The same goes for an agent on no pull request whose name fits two such
repositories.

Nothing needs setting up in agent-frolic. It asks the agent-grange on your own Mac (at
`127.0.0.1`, and nowhere else), and when one answers, the agents appear. The grange holds each
question until something changes, so an agent that starts or stops shows within moments. Part 4
of the setup says what the grange reports:

- **No grange on this Mac**: the board shows people and teams, from GitHub's records alone.
- **A grange that stops answering**: what it last reported stays for a minute, and is then
  taken off the board.
- **A grange that is out of date** answers nothing until it is updated. The setup shows the
  command to run in the grange's folder (`make update`).

In either of the last two cases the board says so at the end of the ticker's line, for as long
as it lasts: without their agents, pull requests that agents are at work on look as if nobody
were on them.

Part 4 also counts the agents reported, how many of them are on the pull requests shown and
how many have no open pull request, and lists the ones that are not shown though their names
say where they are.

## Files and logs

| what | where |
|------|-------|
| settings | `~/Library/Application Support/agent-frolic/settings.json` |
| your list of repositories | `~/Library/Application Support/agent-frolic/repos.json` |
| log | `~/Library/Logs/agent-frolic/agent-frolic.log` |
| GitHub token, webhook secret, ngrok authtoken | the macOS Keychain, under `agent-frolic` |

The log records what was read and what changed, and why anything failed. It starts afresh
past 2 MB and keeps the three before it.

`poll_minutes` in the settings is how many minutes a repository's timer runs: 5 unless you
change it, from 1 to 1440. A repository with a webhook that can be relied on is read every six
times that.

`grange_port` is the port the agent-grange on this Mac listens on: 7788 unless you change it,
to match `GRANGE_PORT` in the grange's `.env`.

## How it works

The app asks GitHub for the open pull requests of each shown repository with one query: at
start, when that repository's timer runs out, when you press `u`, and when a webhook says
something changed there. The webhook receiver listens on 127.0.0.1 only; deliveries reach it
through the ngrok tunnel and are accepted only with GitHub's signature. The team's agents are
read from the agent-grange on this Mac, which keeps a question waiting until it has news.
Everything on the board is worked out from what was read; the board itself never talks to
GitHub or to the grange.

It is a Python program (Textual) running inside a native window. To run it from source:

```sh
python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/python main.py
.venv/bin/python -m pytest        # the tests: no network, no real Keychain
```

`scripts/build_app.sh` builds the Mac app, and `scripts/release.sh` cuts a release. A copy you
build yourself does not update itself: only a release carries the address of the updates.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

The artwork has licenses of its own: the app icon's rooster is from
[Noto Emoji](https://github.com/googlefonts/noto-emoji). See
[credits/README.txt](credits/README.txt).

The app has other people's programs inside it, each under its own license. The texts of
[Sparkle](https://sparkle-project.org)'s and
[SwiftTerm](https://github.com/migueldeicaza/SwiftTerm)'s are in the app, beside its own:
`LICENSE-Sparkle` and `LICENSE-SwiftTerm` in `Contents/Resources`.
