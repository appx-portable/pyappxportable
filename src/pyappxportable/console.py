"""Terminal output and prompts, kept in order with each other."""

from typing import Callable, Dict, Optional

Ask = Callable[[str], str]


def say(message: str = "") -> None:
    """Print a line now, so it lands before the next prompt even when piped."""
    print(message, flush=True)


def warn(message: str) -> None:
    """Print a warning line."""
    say(f"Warning: {message}")


def confirm(ask: Ask, question: str) -> bool:
    """A yes/no question that defaults to yes."""
    return ask(f"{question} [Y/n]: ").strip().lower() not in ("n", "no")


def choose(
    ask: Ask, title: str, choices: Dict[str, str], current: str
) -> Optional[str]:
    """A numbered menu of choices (value: label); Enter keeps current and returns
    None. The number or the value itself is accepted."""
    values = list(choices)
    say(title)
    for number, value in enumerate(values):
        say(f"  {number}  {choices[value]}")
    while True:
        answer = ask(f"Choice [Enter keeps {current}]: ").strip().lower()
        if not answer:
            return None
        if answer.isdigit() and int(answer) < len(values):
            return values[int(answer)]
        if answer in choices:
            return answer
        say(f"Enter a number from 0 to {len(values) - 1}, or press Enter.")
