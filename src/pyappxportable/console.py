"""Terminal output and prompts in color, kept in order with each other.

Messages are rich Text, never markup, so the brackets in prompts and paths
print as typed. Color turns off by itself when output is not a terminal, and
NO_COLOR or FORCE_COLOR override that."""

from contextlib import contextmanager
from typing import Callable, Dict, Generator, Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.text import Text

Ask = Callable[[str], str]

# soft_wrap: long paths wrap in the terminal instead of being broken by
# inserted newlines, so they can still be copied.
OUT = Console(soft_wrap=True, highlight=False, emoji=False)
ERR = Console(stderr=True, soft_wrap=True, highlight=False, emoji=False)


def say(message: str = "", style: Optional[str] = None) -> None:
    """Print a line now, so it lands before the next prompt even when piped."""
    OUT.print(Text(message, style=style or ""))


def step(message: str) -> None:
    """Print the start of a long step: installing, downloading."""
    say(message, "bold cyan")


def note(message: str) -> None:
    """Print a side remark."""
    say(f"Note: {message}", "dim")


def done(message: str) -> None:
    """Print the outcome of a finished run."""
    say(message, "bold green")


def warn(message: str) -> None:
    """Print a warning line."""
    OUT.print(Text.assemble(("Warning: ", "bold yellow"), (message, "yellow")))


def error(message: str) -> None:
    """Print an error line to stderr."""
    ERR.print(Text.assemble(("Error: ", "bold red"), (message, "red")))


def notice(message: str) -> None:
    """Print a line to stderr, out of the way of the installer's own output."""
    ERR.print(Text(message, style="magenta"))


@contextmanager
def download_progress(
    name: str, total: int, completed: int = 0
) -> Generator[Callable[[int], None], None, None]:
    """A progress bar for downloading total bytes of name, completed of them
    already in hand; yields the function that advances it. Outside a terminal it
    prints nothing."""
    with Progress(
        TextColumn("  {task.description}", style="cyan"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=OUT,
        disable=not OUT.is_terminal,
    ) as progress:
        task = progress.add_task(name, total=total, completed=completed)
        yield lambda size: progress.advance(task, size)


def prompt(question: str) -> str:
    """input() with the question in bold and its [default] hint dimmed."""
    text = Text(question, style="bold")
    hint = question.rfind(" [")
    if hint != -1 and question.endswith("]: "):
        text.stylize("not bold dim", hint + 1, len(question) - 2)
    return OUT.input(text)


def confirm(ask: Ask, question: str) -> bool:
    """A yes/no question that defaults to yes."""
    return ask(f"{question} [Y/n]: ").strip().lower() not in ("n", "no")


def choose(
    ask: Ask, title: str, choices: Dict[str, str], current: str
) -> Optional[str]:
    """A numbered menu of choices (value: label); Enter keeps current and returns
    None. The number or the value itself is accepted."""
    values = list(choices)
    say(title, "bold")
    for number, value in enumerate(values):
        OUT.print(Text.assemble((f"  {number}  ", "bold cyan"), choices[value]))
    while True:
        answer = ask(f"Choice [Enter keeps {current}]: ").strip().lower()
        if not answer:
            return None
        if answer.isdigit() and int(answer) < len(values):
            return values[int(answer)]
        if answer in choices:
            return answer
        say(f"Enter a number from 0 to {len(values) - 1}, or press Enter.", "yellow")
