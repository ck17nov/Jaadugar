"""`autotube stories` - the script bank's whole workflow.

Four commands, in the order they are used:

    stories prompt --group kids --language en          write the prompt to paste
    stories import batch.jsonl --group kids            validate, gate, store
    stories review <entry_id> --by chandan --note "..."  record a human read
    stories status                                     what is left, per slot

`prompt` exists as a command rather than a document because the prompt has to
quote the schema, the beat table and the word count implied by the duration -
all of which live in code - AND the names, refrains and titles already banked,
so batch two does not retell batch one.

`import` is safe to re-run. Fix the file, import again: entries already turned
into videos keep their used state, so a correction cannot republish a story
that has already gone out.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from engine.content import bank_import, bank_prompt
from engine.core.config import load_config
from engine.core.db import Database
from engine.core.groups import GROUPS, group as get_group

stories_app = typer.Typer(no_args_is_help=True,
                          help="Pre-written, human-reviewed scripts.")
console = Console()


def _db() -> Database:
    cfg = load_config()
    return Database(cfg.workspace / "autotube.db")


@stories_app.command("prompt")
def stories_prompt(
    group: str = typer.Option(..., "--group", "-g",
                              help="kids | finance | tech | ai | science | "
                                   "programming"),
    language: str = typer.Option("en", "--language"),
    fmt: str = typer.Option("SHORT", "--format", help="SHORT | LONGFORM"),
    length: int = typer.Option(50, "--length", "-l", help="target seconds"),
    count: int = typer.Option(0, "--count",
                              help="0 picks a size that fits one response"),
    shape: str = typer.Option("", "--shape",
                              help="narrative | poem | drill | explainer | "
                                   "procedure"),
    out: str = typer.Option("", "--out", help="write to a file instead"),
) -> None:
    """Print the prompt to paste into Claude or ChatGPT."""
    found = get_group(group)
    if found is None:
        console.print(f"[red]unknown group[/red] {group!r}. Known: "
                      + ", ".join(g.key for g in GROUPS))
        raise typer.Exit(code=2)

    shape = shape or bank_prompt.shape_for(group)
    if count <= 0:
        count = bank_prompt.recommended_count(
            group_key=group, target_seconds=length, shape=shape,
            made_for_kids=found.child_directed)

    db = _db()
    try:
        context = bank_prompt.context_from_bank(db, group_key=group,
                                                language=language)
    finally:
        db.close()

    text = bank_prompt.build(
        group_key=group, language=language, video_format=fmt,
        target_seconds=length, count=count, shape=shape, **context)

    plan = bank_prompt.scene_plan(group_key=group, target_seconds=length,
                                  shape=shape,
                                  made_for_kids=found.child_directed)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        console.print(f"written to [cyan]{out}[/cyan]")
    else:
        # print(), not console.print(): Rich would interpret the square
        # brackets in the JSON example as markup and eat them, which is
        # exactly the part that has to be copied verbatim.
        print(text)

    console.print(
        f"\n[dim]{count} x {shape} / {group} / {language} / {fmt} - "
        f"{plan['words_low']}-{plan['words_high']} words, "
        f"{plan['scenes_low']}-{plan['scenes_high']} scenes, "
        f"~{plan['seconds_per_scene']}s per picture. "
        f"Save the reply as .jsonl, then: autotube stories import "
        f"<file> --group {group}[/dim]")


@stories_app.command("import")
def stories_import(
    path: str = typer.Argument(..., help="the .jsonl file"),
    group: str = typer.Option("", "--group", "-g",
                              help="reject entries for another group"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="report without storing"),
    require_review: bool = typer.Option(
        False, "--require-review",
        help="refuse entries with no human reviewer"),
) -> None:
    """Validate, gate and store a bank file. Safe to re-run."""
    target = Path(path)
    if not target.exists():
        console.print(f"[red]no such file[/red] {path}")
        raise typer.Exit(code=2)

    db = _db()
    try:
        report = bank_import.import_file(
            target, db, expect_group=group, dry_run=dry_run,
            require_review=require_review)
    finally:
        db.close()

    console.print(f"\n[bold]{report.summary()}[/bold]"
                  + ("  [yellow](dry run - nothing stored)[/yellow]"
                     if dry_run else ""))
    for line in report.rejected:
        console.print(f"  [red]{line}[/red]")
    for line in report.warnings[:40]:
        console.print(f"  [yellow]{line}[/yellow]")
    if len(report.warnings) > 40:
        console.print(f"  [dim]... and {len(report.warnings) - 40} more "
                      f"warnings[/dim]")

    stats = report.stats
    if stats.get("total"):
        console.print(
            f"\n[dim]bank now: {stats['total']} entries, "
            f"{stats['seconds_min']}-{stats['seconds_max']}s, "
            f"arcs {stats['arcs']}[/dim]")
    if report.unreviewed:
        console.print(
            f"[yellow]{report.unreviewed} entries have no human reviewer.[/yellow] "
            f"They will not be claimed for rendering until reviewed:\n"
            f"  autotube stories review <entry_id> --by <name>")
    if report.rejected and not dry_run:
        raise typer.Exit(code=1)


@stories_app.command("review")
def stories_review(
    entry_id: str = typer.Argument(...),
    by: str = typer.Option(..., "--by", help="who read it"),
    verdict: str = typer.Option("approve", "--verdict",
                                help="approve | reject"),
    note: str = typer.Option("", "--note",
                             help="what YOU contributed - a rewritten line, "
                                  "a chosen refrain, an author's note"),
    kind: str = typer.Option("human", "--kind",
                             help="human | machine. Use machine when a model "
                                  "approved it, so the record does not claim "
                                  "a review nobody did."),
) -> None:
    """Record that a human read an entry.

    `--note` is kept as data rather than as a claim. YouTube's prohibited
    bullet is about mass-produced content "without adding the creator's
    original, authentic insights", so what was actually added is worth
    recording per entry.
    """
    db = _db()
    try:
        ok = bank_import.review(db, entry_id, reviewer=by, verdict=verdict,
                                element=note, kind=kind)
    finally:
        db.close()
    if not ok:
        console.print(f"[red]no entry[/red] {entry_id}")
        raise typer.Exit(code=2)
    mark = "" if kind == "human" else "  [yellow](machine review)[/yellow]"
    console.print(f"[green]{verdict}[/green] recorded for {entry_id} "
                  f"by {by}{mark}")


@stories_app.command("export")
def stories_export(
    out: str = typer.Argument(..., help="the .jsonl file to write"),
    group: str = typer.Option("", "--group", "-g"),
    language: str = typer.Option("", "--language"),
    used: bool = typer.Option(False, "--include-used",
                              help="also export entries already published"),
) -> None:
    """Write banked entries back out as JSONL, WITH their ids.

    This is what makes an entry correctable. An entry_id is derived from a
    hash of the narration only when the file does not carry one - so an
    authored file, which never does, gets a fresh id on every import, and
    editing a line therefore produces a SECOND entry that the variety gate
    then rejects as a near-duplicate of the first.

    Export keeps the id. Edit the exported file and re-import, and the entry
    is updated in place: the variety gate skips its own id, and its used
    state survives so a correction cannot republish something already out.
    """
    import json as _json

    from engine.content.bank import BankEntry, write_jsonl

    db = _db()
    try:
        rows = db.bank_entries(group=group, language=language, limit=5000)
    finally:
        db.close()

    entries, unreadable = [], 0
    for row in rows:
        if row["used_at"] and not used:
            continue
        try:
            entries.append(BankEntry.from_dict(_json.loads(row["payload"])))
        except Exception:                       # noqa: BLE001
            unreadable += 1

    if not entries:
        console.print("[yellow]nothing to export[/yellow] for that filter")
        raise typer.Exit(code=1)

    written = write_jsonl(entries, Path(out))
    console.print(f"[green]{written}[/green] entries -> [cyan]{out}[/cyan]")
    if unreadable:
        console.print(f"[red]{unreadable} rows would not parse[/red] and were "
                      f"skipped")
    console.print("[dim]Edit it and re-import: the ids are in the file, so "
                  "each entry is updated in place rather than added "
                  "again.[/dim]")


@stories_app.command("remove")
def stories_remove(
    entry_id: str = typer.Argument(...),
    force: bool = typer.Option(
        False, "--force",
        help="remove even if it has already become a video"),
) -> None:
    """Retire an entry, so a corrected version can be imported.

    An entry_id is a hash of its narration, so editing a line produces a
    NEW id - and the variety gate then compares the correction against the
    original still in the table and rejects it as a near-duplicate. Retire
    the old one first.

    Refuses a used entry without --force: `used_job_id` is the only record of
    which script became which published video, and losing it means an
    originality question about a live video can no longer be answered.
    """
    db = _db()
    try:
        row = next((r for r in db.bank_entries(limit=5000)
                    if r["entry_id"] == entry_id), None)
        if row is None:
            console.print(f"[red]no entry[/red] {entry_id}")
            raise typer.Exit(code=2)
        if row["used_at"] and not force:
            console.print(
                f"[yellow]{entry_id} has already become a video[/yellow] "
                f"(job {row['used_job_id'] or 'unknown'}). "
                f"Removing it loses the link between that video and its "
                f"script. Pass --force if you mean it.")
            raise typer.Exit(code=1)
        title = row["title"]
        db.delete_bank_entry(entry_id)
    finally:
        db.close()
    console.print(f"[green]removed[/green] {entry_id}  {title[:60]}")


@stories_app.command("status")
def stories_status(
    group: str = typer.Option("", "--group", "-g"),
) -> None:
    """How many entries are left, per group / language / format."""
    import json as _json

    from engine.content.bank import BankEntry
    from engine.content.bank_use import approved, reviewed_by_human

    db = _db()
    try:
        counts = db.bank_counts()
        rows = db.bank_entries(group=group, limit=5000) if group else []
        # Per slot: how many are actually CLAIMABLE, and how many of those a
        # person signed off. "unused" alone overstates what a render can take,
        # because an unapproved entry is skipped.
        ready: dict[str, list[int]] = {}
        for row in db.bank_entries(limit=5000):
            if row["used_at"]:
                continue
            try:
                entry = BankEntry.from_dict(_json.loads(row["payload"]))
            except Exception:                   # noqa: BLE001
                continue
            key = f'{row["grp"]}|{row["language"]}|{row["video_format"]}'
            slot = ready.setdefault(key, [0, 0])
            if approved(entry):
                slot[0] += 1
                if reviewed_by_human(entry):
                    slot[1] += 1
    finally:
        db.close()

    if not counts:
        console.print("The bank is empty. Start with:\n"
                      "  autotube stories prompt --group kids --language en")
        return

    table = Table(title="script bank")
    for column in ("group", "language", "format", "ready", "of which human",
                   "unused", "total"):
        table.add_column(column)
    for row in counts:
        if group and row["grp"] != group.lower():
            continue
        unused = int(row["unused"] or 0)
        key = f'{row["grp"]}|{row["language"]}|{row["video_format"]}'
        claimable, by_human = ready.get(key, [0, 0])
        colour = ("green" if claimable > 5 else
                  "yellow" if claimable else "red")
        table.add_row(row["grp"], row["language"], row["video_format"],
                      f"[{colour}]{claimable}[/{colour}]",
                      str(by_human) if by_human else "[dim]0[/dim]",
                      str(unused), str(row["total"]))
    console.print(table)
    console.print("[dim]ready = unused AND approved, which is what a render "
                  "will actually claim. \"of which human\" is how many a "
                  "person signed off rather than a model.[/dim]")

    if rows:
        detail = Table(title=f"{group} entries")
        for column in ("entry_id", "topic", "secs", "review", "used", "title"):
            detail.add_column(column)
        for row in rows[:40]:
            try:
                entry = BankEntry.from_dict(_json.loads(row["payload"]))
                state = ("human" if reviewed_by_human(entry)
                         else "machine" if approved(entry)
                         else "[red]none[/red]")
            except Exception:                   # noqa: BLE001
                state = "[red]unreadable[/red]"
            detail.add_row(row["entry_id"], row["topic"] or "-",
                           f"{row['est_seconds']:.0f}", state,
                           "yes" if row["used_at"] else "-",
                           (row["title"] or "")[:48])
        console.print(detail)
        if len(rows) > 40:
            console.print(f"[dim]... and {len(rows) - 40} more[/dim]")
