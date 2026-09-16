# Working contract

Read this at the start of every session. It is short on purpose.

The owner of this repository is the foreman. No agent manages another agent.

## Done means a person can do something

A task's `## Acceptance` is one sentence a person performs on a real machine. That is the
definition of done and nothing else is. Not a passing test, not a clean type-check, not a
rendered widget. Those are how you know you finished. They are not what finished means.

**No task is done until its `## Hand-walk` section says what happened when someone
actually did the thing, including the parts that did not work.** Only the owner can write
that section.

If you finish the code and the walk needs something that does not exist yet, say so and
leave the task open. Refusing to mark your own work done is the most useful thing you can
do here.

## Write only what you own

`.dev/CHARTER.md` names the paths you may write. Nothing else in this repository is
yours. Not the docs, not the workspace manifest, not another session's files.

If something outside your paths must change, that is a message to the owner, not an edit.

## A bug you cannot fix needs a failing artifact

An ignored test or a filed task, not a note in a message. A bug that gets routed around
in a test is a bug with a green suite in front of it.

## Three failures, then stop

If a change fails its check three times, stop and hand it back to the owner with what you
tried. Do not try a fourth approach. The fourth is where the afternoons go.

## Go and look

Read the file before the third guess, ideally before the first. Do not reason forward
from what an API probably does, and do not describe this repository from recollection.
`cat` it. Every wrong diagnosis this method has produced would have been caught by
opening the file, and each one took ten seconds to check.

If you state a fact about external behaviour, name where you verified it.

## Git

**Stage files, never directories.** `git add <dir>` while anything else is in flight
takes whatever happens to be there and commits it under whatever message you were
writing. A directory is a guess about who has finished.

**A task's Status changes in the commit that lands it.** Not afterwards, not in a sweep.

**No whole-tree command while anyone else is working.** Not `git stash`, not
`git checkout .`, not `git reset --hard`, not a repository-wide formatter. Scoped forms
only: `git diff -- <file>`, `git stash push -- <file>`. `stash` is the worst of the set
because it looks temporary and it reverts every dirty file in the repository.

## Cost

You are spending real money. Prefer the cheap read over the clever inference, and stop to
ask rather than burning a long loop on a guess. Noticing that you are repeating an
approach that is not working is the three-failure rule arriving early.

## When someone else is also working

**One writer at a time.** A path has an owner, who is who to ask and who reviews, but an
idle session may take work in another's files if the owner is not currently in them and
it says so first, naming the file and what for.

**A fork of you is not you.** A helper you spawn never writes a shared file, never sends
mail in your name, and never edits shared state. It reads, it reasons, it reports back,
and you make the change.

Messages carry their urgency in the first line:

- `[fyi]` read it at your next checkpoint. Nothing you are doing now is wrong.
- `[change]` finish the unit you are in, then read it before starting the next.
- `[stop]` stop before your next edit. You are building the wrong thing.

A correction owes the reader four things, in this order. What I observed, which is the
fact and not the judgement. Why it matters to me, which is who is blocked or misled. What
to do, specific and small. And whether to stop, said plainly, because the reader cannot
infer it from tone.

No apology is expected and none should be offered. A correction is routing information,
not a complaint.

## The log

`.dev/log.md` is append-only, one line per mistake, dated, signed by whoever made it. Add
to it when you get something wrong in a way worth not repeating.

It is not for blame. It exists because a rule that lives in a message is not a rule.
