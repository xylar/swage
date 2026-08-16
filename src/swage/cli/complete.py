"""`swage completion` -- the commands, and the names, on the TAB key.

**The script does the completing, not swage.** The obvious design is the one
`gh` and `pip` use: the shell calls the tool back on every TAB and the tool
answers with candidates. swage cannot afford it. Importing the CLI costs about
a third of a second -- pydantic, ruamel.yaml and the recipe model are loaded
before argparse sees a word -- and a completion that pauses that long is one a
maintainer turns off within a day. So the commands and their options are baked
into the script when it is generated, and the names are read from files, and a
TAB press runs no Python at all.

That costs one thing: the script is a snapshot. An option added to swage after
the script was generated will not complete until it is regenerated, which is
why the script says so in its own header.

**Names come from a cache, because the authoritative answer is a network
call.** Which feedstocks the maintainer has is one paginated GitHub read over
~490 teams (`discover_feedstocks`) -- fine once a run, impossible on a
keystroke. Any run that discovers writes what it found under the cache root,
`swage completion --refresh` writes it on demand, and the shell reads the file.
A file that is not there completes nothing rather than failing, which is the
same answer swage gives for a cache it cannot write.

The cache path is therefore spelled twice, here and in shell inside each
generated script, because the script has to find it without running swage. It
is one line of mirror -- `cache.py`'s `XDG_CACHE_HOME`, else `~/.cache` -- and
the tests hold the two together.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from swage.cache import cache_root

__all__ = [
    "FAMILIES",
    "FEEDSTOCKS",
    "SHELLS",
    "Argument",
    "Command",
    "CommandLine",
    "Option",
    "Values",
    "completion_script",
    "describe",
    "names_directory",
    "recall",
    "remember",
]

#: The shells `swage completion` writes a script for.
SHELLS = ("bash", "zsh")

#: The two name files, which are also the words the generated scripts pass to
#: their reader function. One name per line and sorted, so that the file is
#: worth reading with `grep` as well as with TAB.
FEEDSTOCKS = "feedstocks"
FAMILIES = "families"

_NAMES = "names"


def names_directory(root: Path | None = None) -> Path:
    """Where the names completion offers live."""
    return (root or cache_root()) / _NAMES


def remember(kind: str, names: Iterable[str], root: Path | None = None) -> None:
    """Record the names completion should offer for ``kind``.

    Called from whatever has just worked them out, and never on its own
    account: discovery is expensive enough that running it to fill a completion
    cache would be the tail wagging the dog.
    """
    path = names_directory(root) / kind
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{name}\n" for name in sorted(set(names))), encoding="utf-8"
        )
    except OSError:
        # A cache swage cannot write is a completion that offers less, not a
        # command that failed -- the run this was a side effect of did its job.
        pass


def recall(kind: str, root: Path | None = None) -> tuple[str, ...]:
    """The names completion currently offers for ``kind``."""
    try:
        text = (names_directory(root) / kind).read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(line for line in text.splitlines() if line)


class Values(Enum):
    """What kind of word goes in a place on the command line."""

    #: A flag: the word after it is not its value.
    NONE = "none"
    FEEDSTOCK = "feedstock"
    FAMILY = "family"
    DIRECTORY = "directory"
    #: One of a fixed set the parser already knows.
    CHOICE = "choice"
    #: A value swage cannot enumerate, such as `--since 7d`. Offering filenames
    #: for one of those would be worse than offering nothing.
    OPAQUE = "opaque"


@dataclass(frozen=True)
class Argument:
    """A place on the command line, and what belongs in it."""

    values: Values
    #: What the parser calls it -- `FEEDSTOCK`, `WINDOW` -- which is the word
    #: zsh shows above the candidates while you choose one.
    label: str = ""
    choices: tuple[str, ...] = ()
    #: Whether several values follow one flag, as `--feedstock a b c` does. A
    #: bare word after one of those is another value rather than a positional.
    repeated: bool = False


@dataclass(frozen=True)
class Option:
    flags: tuple[str, ...]
    help: str
    argument: Argument


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    options: tuple[Option, ...]
    #: What a bare word after the command completes to, where it takes one.
    positional: Argument | None


@dataclass(frozen=True)
class CommandLine:
    """The whole of what a generated script needs to know about swage."""

    #: The options that come before the command, not after it.
    options: tuple[Option, ...]
    commands: tuple[Command, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(command.name for command in self.commands)


def describe(parser: argparse.ArgumentParser) -> CommandLine:
    """Read the parser, so a script cannot describe a different tool.

    Generated from `build_parser` rather than written out beside it, because a
    hand-kept list goes stale in the way nobody notices: a completion offering
    an option that was renamed looks like a broken shell rather than an old
    script.
    """
    subparsers = _subparsers(parser)
    helps = _command_help(subparsers)
    commands = tuple(
        Command(
            name=name,
            help=helps.get(name, ""),
            options=_options(subparser),
            positional=_positional(subparser),
        )
        for name, subparser in subparsers.choices.items()
    )
    return CommandLine(options=_options(parser), commands=commands)


def completion_script(shell: str, parser: argparse.ArgumentParser) -> str:
    """The completion script for ``shell``, generated from ``parser``."""
    line = describe(parser)
    return _bash_script(line) if shell == "bash" else _zsh_script(line)


def _subparsers(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("swage's parser takes subcommands")  # pragma: no cover


def _command_help(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, str]:
    """The one-line help argparse keeps beside each subcommand, by name."""
    return {action.dest: action.help or "" for action in subparsers._choices_actions}


def _options(parser: argparse.ArgumentParser) -> tuple[Option, ...]:
    return tuple(
        Option(
            flags=tuple(action.option_strings),
            help=action.help or "",
            argument=_argument(action),
        )
        for action in parser._actions
        if action.option_strings
    )


def _positional(parser: argparse.ArgumentParser) -> Argument | None:
    for action in parser._actions:
        if not action.option_strings and not isinstance(
            action, argparse._SubParsersAction
        ):
            return _argument(action)
    return None


def _argument(action: argparse.Action) -> Argument:
    """What completes where ``action``'s value goes.

    Keyed on `dest` rather than on the flag, because the same value means the
    same thing under every command: `--feedstock` names feedstocks whether it
    is `scan`'s or `update`'s, and a table keyed on flags would be a table with
    six ways to disagree with itself.
    """
    if action.nargs == 0:
        # Every flag argparse builds -- store_true, --help, --version -- says
        # so this way, so nothing here reads argparse's private classes.
        return Argument(Values.NONE)
    label = str(action.metavar or action.dest).lower()
    repeated = action.nargs in {"+", "*"}
    if action.choices:
        choices = tuple(str(choice) for choice in action.choices)
        return Argument(Values.CHOICE, label, choices, repeated)
    if action.dest == "feedstock":
        return Argument(Values.FEEDSTOCK, label, repeated=repeated)
    if action.dest == "family":
        return Argument(Values.FAMILY, label, repeated=repeated)
    if action.type is Path:
        return Argument(Values.DIRECTORY, label, repeated=repeated)
    return Argument(Values.OPAQUE, label, repeated=repeated)


def _flags(options: Sequence[Option]) -> list[str]:
    return [flag for option in options for flag in option.flags]


def _valued(line: CommandLine, values: Values) -> list[str]:
    """Every flag anywhere in swage whose value is of this kind."""
    found = [
        flag
        for options in (line.options, *(command.options for command in line.commands))
        for option in options
        if option.argument.values is values
        for flag in option.flags
    ]
    return sorted(dict.fromkeys(found))


def _taking_values(line: CommandLine) -> list[str]:
    """Every flag anywhere in swage that is followed by a value."""
    return sorted(
        flag
        for values in Values
        if values is not Values.NONE
        for flag in _valued(line, values)
    )


def _repeatedly(line: CommandLine, values: Values) -> list[str]:
    found = [
        flag
        for command in line.commands
        for option in command.options
        if option.argument.values is values and option.argument.repeated
        for flag in option.flags
    ]
    return sorted(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# bash
# ---------------------------------------------------------------------------


def _bash_script(line: CommandLine) -> str:
    return f"""\
# swage completion for bash.
#
# Generated by `swage completion bash`, from the swage that generated it:
# regenerate it after an upgrade, or options added since will not complete.
#
#   swage completion bash > ~/.local/share/bash-completion/completions/swage
#   eval "$(swage completion bash)"     # or this, from ~/.bashrc
#
# Feedstock and family names are read from the files swage caches under
# ${{XDG_CACHE_HOME:-$HOME/.cache}}/swage/names. Any run that reads the fleet
# fills them in, `swage completion --refresh` fills them in on demand, and a
# file that is not there completes nothing.

_swage_names() {{
    local file="${{XDG_CACHE_HOME:-$HOME/.cache}}/swage/names/$1"
    [[ -r $file ]] && cat -- "$file"
}}

_swage() {{
    local cur prev command word candidates skip i
    COMPREPLY=()
    cur=${{COMP_WORDS[COMP_CWORD]}}
    prev=${{COMP_WORDS[COMP_CWORD-1]}}

    # A word the option before it is waiting for.
    case $prev in
{_bash_values(line)}
    esac

    # Which command is this? The first word that names one -- skipping the
    # value an option is waiting for, because `--config-root config` names a
    # directory rather than the `config` command, and that is what the
    # maintainer's own tree is called.
    command=
    skip=
    for (( i = 1; i < COMP_CWORD; i++ )); do
        word=${{COMP_WORDS[i]}}
        if [[ -n $skip ]]; then skip=; continue; fi
        case $word in
            {"|".join(_taking_values(line))}) skip=yes; continue ;;
        esac
        case " {" ".join(line.names)} " in
            *" $word "*) command=$word; break ;;
        esac
    done
{_bash_repeated(line)}
    case $command in
{_bash_commands(line)}
    esac
    COMPREPLY=( $(compgen -W "$candidates" -- "$cur") )
}}

complete -F _swage swage
"""


def _bash_values(line: CommandLine) -> str:
    """The branch per kind of value, for the option the cursor sits after.

    A kind swage has no option for emits no branch at all: `case $prev in )`
    is a syntax error, and a generated script that only parses while every
    kind happens to be in use is a script that breaks on an unrelated edit.
    """
    branches = [
        _bash_branch(
            _valued(line, Values.DIRECTORY),
            [
                'COMPREPLY=( $(compgen -d -- "$cur") )',
                "compopt -o filenames 2>/dev/null",
                "return 0",
            ],
        ),
        _bash_branch(
            _valued(line, Values.FEEDSTOCK),
            [
                f'COMPREPLY=( $(compgen -W "$(_swage_names {FEEDSTOCKS})" -- "$cur") )',
                "return",
            ],
        ),
        _bash_branch(
            _valued(line, Values.FAMILY),
            [
                f'COMPREPLY=( $(compgen -W "$(_swage_names {FAMILIES})" -- "$cur") )',
                "return",
            ],
        ),
        # Nothing sensible to offer, and offering filenames instead is how a
        # completion teaches you to distrust it.
        _bash_branch(_valued(line, Values.OPAQUE), ["return"]),
    ]
    return "\n".join(branch for branch in branches if branch)


def _bash_branch(flags: Sequence[str], body: Sequence[str]) -> str:
    if not flags:
        return ""
    lines = "\n".join(f"            {statement}" for statement in body)
    return f"        {'|'.join(flags)})\n{lines}\n            ;;"


def _bash_repeated(line: CommandLine) -> str:
    """`--feedstock a b c` keeps taking names until the next option.

    Without this, the second name onward would be completed as whatever the
    command's positional takes -- which for `swage update --feedstock a b` is
    nothing at all.
    """
    flags = _repeatedly(line, Values.FEEDSTOCK)
    if not flags:
        return ""  # pragma: no cover
    return f"""
    # `--feedstock a b c` takes names until the next option, so a bare word
    # after one is another feedstock rather than the command's positional.
    if [[ $cur != -* ]]; then
        for (( i = COMP_CWORD - 1; i > 0; i-- )); do
            word=${{COMP_WORDS[i]}}
            [[ $word == -* ]] || continue
            case $word in
                {"|".join(flags)})
                    COMPREPLY=( $(compgen -W "$(_swage_names {FEEDSTOCKS})" -- "$cur") )
                    return
                    ;;
            esac
            break
        done
    fi
"""


def _bash_commands(line: CommandLine) -> str:
    """One branch per command, naming what may follow it.

    Options and names go in one list rather than in two branches on whether the
    word starts with `-`: `compgen` filters by prefix anyway, so a single list
    gives `swage explain --<TAB>` the options and `swage explain goo<TAB>` the
    feedstocks without the script having to decide which case it is in.
    """
    branches = [
        _bash_command('""', [*line.names, *_flags(line.options)], None),
        *(
            _bash_command(command.name, _flags(command.options), command.positional)
            for command in line.commands
        ),
    ]
    return "\n".join(branches)


def _bash_command(
    pattern: str, words: Sequence[str], positional: Argument | None
) -> str:
    listed = list(words)
    names = ""
    if positional is not None and positional.values is Values.CHOICE:
        listed.extend(positional.choices)
    elif positional is not None and positional.values is Values.FEEDSTOCK:
        names = f" $(_swage_names {FEEDSTOCKS})"
    return f'        {pattern}) candidates="{" ".join(listed)}{names}" ;;'


# ---------------------------------------------------------------------------
# zsh
# ---------------------------------------------------------------------------


def _zsh_script(line: CommandLine) -> str:
    commands = "\n".join(
        f"        {_zsh_quote(f'{command.name}:{_zsh_colons(command.help)}')}"
        for command in line.commands
    )
    before = " \\\n".join(f"        {spec}" for spec in _zsh_specs(line.options))
    return f"""\
#compdef swage
#
# swage completion for zsh.
#
# Generated by `swage completion zsh`, from the swage that generated it:
# regenerate it after an upgrade, or options added since will not complete.
#
#   swage completion zsh > ~/.zfunc/_swage
#
# with `fpath=(~/.zfunc $fpath)` ahead of `compinit` in ~/.zshrc.
#
# Feedstock and family names are read from the files swage caches under
# ${{XDG_CACHE_HOME:-$HOME/.cache}}/swage/names. Any run that reads the fleet
# fills them in, `swage completion --refresh` fills them in on demand, and a
# file that is not there completes nothing.

_swage_names() {{
    local file="${{XDG_CACHE_HOME:-$HOME/.cache}}/swage/names/$1"
    local -a names
    [[ -r $file ]] && names=( ${{(f)"$(< $file)"}} )
    _describe -t $1 $2 names
}}

_swage_feedstocks() {{ _swage_names {FEEDSTOCKS} feedstock }}

_swage_families() {{ _swage_names {FAMILIES} family }}

_swage() {{
    local context state state_descr line
    typeset -A opt_args
    local -a commands
    commands=(
{commands}
    )

    _arguments -C \\
{before} \\
        '1: :->command' \\
        '*:: :->argument' && return 0

    case $state in
        command)
            _describe -t commands 'swage command' commands
            ;;
        argument)
            case $words[1] in
{_zsh_commands(line)}
            esac
            ;;
    esac
}}

_swage "$@"
"""


def _zsh_commands(line: CommandLine) -> str:
    return "\n".join(_zsh_command(command) for command in line.commands)


def _zsh_command(command: Command) -> str:
    specs = _zsh_specs(command.options)
    if command.positional is not None:
        argument = command.positional
        specs.append(_zsh_quote(f"1:{argument.label}:{_zsh_action(argument)}"))
    if not specs:
        return f"                {command.name}) ;;"  # pragma: no cover
    body = " \\\n".join(f"                        {spec}" for spec in specs)
    return (
        f"                {command.name})\n"
        f"                    _arguments \\\n{body}\n"
        f"                    ;;"
    )


def _zsh_specs(options: Sequence[Option]) -> list[str]:
    return [spec for option in options for spec in _zsh_option(option)]


def _zsh_option(option: Option) -> list[str]:
    """One `_arguments` spec per flag.

    Per flag rather than the `{-h,--help}` brace form, which cannot be written
    inside a single-quoted string and would have the generator interleaving two
    quoting styles for the sake of one line of output.
    """
    described = f"[{_zsh_brackets(option.help)}]"
    if option.argument.values is Values.NONE:
        return [_zsh_quote(f"{flag}{described}") for flag in option.flags]
    argument = option.argument
    tail = f":{argument.label}:{_zsh_action(argument)}"
    if argument.repeated:
        # `:*:` says the flag takes every word up to the next option, which is
        # what `--feedstock a b c` does; a single-valued flag must not say it.
        return [_zsh_quote(f"*{flag}{described}:*{tail}") for flag in option.flags]
    return [_zsh_quote(f"{flag}{described}{tail}") for flag in option.flags]


def _zsh_action(argument: Argument) -> str:
    if argument.values is Values.FEEDSTOCK:
        return "_swage_feedstocks"
    if argument.values is Values.FAMILY:
        return "_swage_families"
    if argument.values is Values.DIRECTORY:
        return "_files -/"
    if argument.values is Values.CHOICE:
        return f"({' '.join(argument.choices)})"
    return " "


def _zsh_brackets(text: str) -> str:
    """Make help text safe inside an `_arguments` spec.

    `[`, `]` and `:` are the spec's own punctuation, so help carrying one --
    `[phase 6]`, `dry run without --execute` -- would be read as structure.
    """
    for character in ("[", "]", ":"):
        text = text.replace(character, f"\\{character}")
    return text


def _zsh_colons(text: str) -> str:
    """Escape what separates a `_describe` entry from its description."""
    return text.replace(":", "\\:")


def _zsh_quote(text: str) -> str:
    """Single-quote for the shell, the only form that survives an apostrophe."""
    escaped = text.replace("'", "'\\''")
    return f"'{escaped}'"
