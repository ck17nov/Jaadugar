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
                                element=note)
    finally:
        db.close()
    if not ok:
        console.print(f"[red]no entry[/red] {entry_id}")
        raise typer.Exit(code=2)
    console.print(f"[green]{verdict}[/green] recorded for {entry_id} by {by}")


@stories_app.command("status")
def stories_status(
    group: str = typer.Option("", "--group", "-g"),
) -> None:
    """How many entries are left, per group / language / format."""
    db = _db()
    try:
        counts = db.bank_counts()
        rows = db.bank_entries(group=group, limit=5000) if group else []
    finally:
        db.close()

    if not counts:
        console.print("The bank is empty. Start with:\n"
                      "  autotube stories prompt --group kids --language en")
        return

    table = Table(title="script bank")
    for column in ("group", "language", "format", "unused", "total"):
        table.add_column(column)
    for row in counts:
        if group and row["grp"] != group.lower():
            continue
        unused = int(row["unused"] or 0)
        colour = "green" if unused > 5 else "yellow" if unused else "red"
        table.add_row(row["grp"], row["language"], row["video_format"],
                      f"[{colour}]{unused}[/{colour}]", str(row["total"]))
    console.print(table)

    if rows:
        detail = Table(title=f"{group} entries")
        for column in ("entry_id", "topic", "secs", "used", "title"):
            detail.add_column(column)
        for row in rows[:40]:
            detail.add_row(row["entry_id"], row["topic"] or "-",
                           f"{row['est_seconds']:.0f}",
                           "yes" if row["used_at"] else "-",
                           (row["title"] or "")[:48])
        console.print(detail)
        if len(rows) > 40:
            console.print(f"[dim]... and {len(rows) - 40} more[/dim]")
