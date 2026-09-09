from __future__ import annotations

from dataclasses import dataclass
import re
import textwrap


COMMAND_REF_RE = re.compile(r"^:ref:`(?P<label>.+?) <(?P<anchor>[^>]+)>`$")
HEADING_UNDERLINES = {"=": 1, "-": 2, "~": 3, "^": 4}
RST_TYPE_LINK_RE = re.compile(r"`(?P<label>.+?) <(?P<url>\.\./[^>]+)>`_")
RST_INLINE_LINK_RE = re.compile(
    r"`(?P<label>[^`]+?) <(?P<url>(?:https?://|\.\./)[^>]+)>`_"
)
RST_CODE_RE = re.compile(r"``([^`]+)``")


@dataclass(frozen=True)
class ConsoleCommand:
    anchor: str
    name: str
    scope: str
    summary: str
    arguments: tuple[tuple[str, str, str | None], ...]
    return_type: str
    return_url: str | None
    description: str


def _strip_rst_link(value: str) -> tuple[str, str | None]:
    match = RST_TYPE_LINK_RE.fullmatch(value)
    if match is None:
        return value, None
    return match.group("label"), match.group("url")


def _parse_command(lines: list[str], start: int) -> tuple[ConsoleCommand | None, int]:
    anchor_match = re.fullmatch(r"\.\. _([^:]+):", lines[start])
    if anchor_match is None:
        return None, start + 1

    cursor = start + 1
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    if cursor >= len(lines):
        return None, cursor

    ref_match = COMMAND_REF_RE.fullmatch(lines[cursor])
    if ref_match is None:
        return None, start + 1

    anchor = anchor_match.group(1)
    if ref_match.group("anchor").casefold() != anchor.casefold():
        raise ValueError(
            f"Console command anchor mismatch: {anchor!r} != {ref_match.group('anchor')!r}"
        )
    label = ref_match.group("label")
    scope = "Stable"
    scope_match = re.fullmatch(r"(.+) \((Preview|Testing|Repair)\)", label)
    if scope_match is not None:
        name = scope_match.group(1)
        scope = scope_match.group(2)
    else:
        name = label

    cursor += 1
    block_start = cursor
    while cursor < len(lines):
        line = lines[cursor]
        if line and not line[0].isspace():
            break
        cursor += 1
    block = lines[block_start:cursor]

    summary = ""
    arguments: list[tuple[str, str, str | None]] = []
    return_type = ""
    return_url: str | None = None
    description = ""
    index = 0
    while index < len(block):
        stripped = block[index].strip()
        if stripped.startswith("* **Summary**:"):
            summary = stripped.removeprefix("* **Summary**:").strip()
            index += 1
            continue
        if stripped.startswith("* **Arguments**:"):
            index += 1
            while index < len(block):
                argument_match = re.fullmatch(
                    r"\* ``(?P<name>[^`]+)``: (?P<type>.*)", block[index].strip()
                )
                if argument_match is None:
                    if block[index].strip():
                        break
                    index += 1
                    continue
                argument_type, argument_url = _strip_rst_link(
                    argument_match.group("type")
                )
                arguments.append(
                    (argument_match.group("name"), argument_type, argument_url)
                )
                index += 1
            continue
        if stripped.startswith("* **Return type**:"):
            index += 1
            while index < len(block) and not block[index].strip():
                index += 1
            if index < len(block):
                return_match = re.fullmatch(r"\* (?P<type>.*)", block[index].strip())
                if return_match is not None:
                    return_type, return_url = _strip_rst_link(
                        return_match.group("type")
                    )
                    index += 1
            continue
        if stripped.startswith("* **Description**:"):
            index += 1
            while index < len(block) and not block[index].strip():
                index += 1
            if index < len(block) and block[index].strip() == ".. code-block:: none":
                index += 1
            while index < len(block) and not block[index].strip():
                index += 1
            description_lines = block[index:]
            description = textwrap.dedent("\n".join(description_lines)).strip()
            break
        index += 1

    if not summary:
        raise ValueError(f"Console command {anchor!r} has no summary")
    return (
        ConsoleCommand(
            anchor=anchor,
            name=name,
            scope=scope,
            summary=summary,
            arguments=tuple(arguments),
            return_type=return_type,
            return_url=return_url,
            description=description,
        ),
        cursor,
    )


def parse_generated_console_rst(rst: str) -> tuple[list[ConsoleCommand], list[str]]:
    lines = rst.splitlines()
    commands: list[ConsoleCommand] = []
    body: list[str] = []
    index = 0
    while index < len(lines):
        command, next_index = _parse_command(lines, index)
        if command is not None:
            commands.append(command)
            body.append(f"<console-command: {len(commands) - 1}>")
            index = next_index
            continue
        body.append(lines[index])
        index += 1
    return commands, body


def _inline_rst_to_mdx(
    value: str, *, escape_angles: bool = True, escape_braces: bool = False
) -> str:
    value = RST_INLINE_LINK_RE.sub(
        lambda match: f"[{match.group('label')}]({match.group('url')})", value
    )
    value = RST_CODE_RE.sub(r"`\1`", value)
    if escape_angles or escape_braces:
        segments = value.split("`")
        for index in range(0, len(segments), 2):
            if escape_angles:
                segments[index] = (
                    segments[index].replace("<", r"\<").replace(">", r"\>")
                )
            if escape_braces:
                segments[index] = (
                    segments[index].replace("{", r"\{").replace("}", r"\}")
                )
        value = "`".join(segments)
    return value


def _summary(value: str) -> str:
    value = _inline_rst_to_mdx(value.strip())
    value = re.sub(
        r"^(export|if|import|let|class)\b",
        lambda match: match.group(0).capitalize(),
        value,
    )
    if value and value[-1] not in ".!?":
        value += "."
    return value


def _description(
    value: str, *, argument_names: tuple[str, ...], escape_mdx: bool
) -> str:
    if not value:
        return ""
    value = _inline_rst_to_mdx(
        value, escape_angles=escape_mdx, escape_braces=escape_mdx
    )
    rendered: list[str] = []
    for raw_paragraph in re.split(r"\n\s*\n", value):
        raw_lines = [
            line.strip() for line in raw_paragraph.splitlines() if line.strip()
        ]
        raw_lines = [re.sub(r'^["|]+\s*(?=- )', "", line).strip() for line in raw_lines]
        expanded_lines: list[str] = []
        for line in raw_lines:
            if ": - " in line:
                prefix, _separator, suffix = line.partition(": - ")
                expanded_lines.extend((f"{prefix}:", f"- {suffix}"))
            else:
                expanded_lines.append(line)
        raw_lines = expanded_lines
        if not raw_lines:
            continue
        first_list_item = next(
            (index for index, line in enumerate(raw_lines) if line.startswith("- ")),
            None,
        )
        if first_list_item is not None:
            intro = " ".join(raw_lines[:first_list_item]).strip()
            if intro:
                rendered.append(intro)
            list_items: list[str] = []
            for line in raw_lines[first_list_item:]:
                if line.startswith("- "):
                    list_items.append(line)
                elif list_items:
                    list_items[-1] += f" {line}"
                else:
                    raise ValueError("Console description list has no leading item")
            for item in list_items:
                parts = re.split(r"\s+-\s+(?=[A-Za-z])", item)
                rendered.extend(
                    part if part.startswith("- ") else f"- {part}" for part in parts
                )
            continue

        paragraph = " ".join(raw_lines)
        paragraph = re.sub(r'\s*["|]+\s*(?=- )', " ", paragraph).strip()
        argument_pattern = "|".join(
            re.escape(name) for name in sorted(argument_names, key=len, reverse=True)
        )
        named_boundaries = (
            list(
                re.finditer(
                    rf"(?<![A-Za-z0-9_.-])(?P<name>{argument_pattern})\s+-\s+",
                    paragraph,
                )
            )
            if argument_pattern
            else []
        )
        if len(named_boundaries) >= 2:
            intro = paragraph[: named_boundaries[0].start()].strip()
            if intro:
                rendered.append(intro)
            starts = [boundary.end() for boundary in named_boundaries]
            ends = [boundary.start() for boundary in named_boundaries[1:]] + [
                len(paragraph)
            ]
            for boundary, start, end in zip(
                named_boundaries, starts, ends, strict=True
            ):
                rendered.append(
                    f"- `{boundary.group('name')}`: {paragraph[start:end].strip()}"
                )
            continue

        dash_boundaries = list(re.finditer(r"\s+-\s+(?=[A-Za-z`\"])", paragraph))
        if len(dash_boundaries) >= 2:
            intro = paragraph[: dash_boundaries[0].start()].strip()
            if intro:
                rendered.append(intro)
            starts = [boundary.end() for boundary in dash_boundaries]
            ends = [boundary.start() for boundary in dash_boundaries[1:]] + [
                len(paragraph)
            ]
            for start, end in zip(starts, ends, strict=True):
                rendered.append(f"- {paragraph[start:end].strip()}")
            continue

        rendered.append(paragraph)

    normalized: list[str] = []
    for paragraph in rendered:
        identifier = re.fullmatch(r"- ([A-Za-z][A-Za-z0-9_.-]*):(.*)", paragraph)
        if identifier is not None:
            paragraph = f"- `{identifier.group(1)}`:{identifier.group(2)}"
        if normalized and not (
            paragraph.startswith("- ") and normalized[-1].startswith("- ")
        ):
            normalized.append("")
        normalized.append(paragraph)
    text = "\n".join(normalized)
    text = re.sub(r"^export ", "Export ", text, flags=re.MULTILINE)
    text = re.sub(r"^if ", "If ", text, flags=re.MULTILINE)
    text = text.replace('"<synchronizer id>"', '`"<synchronizer id>"`')
    return text


def _type_mdx(value: str, url: str | None, *, source_version: str) -> str:
    value = value.replace(r"\[", "[").replace(r"\]", "]")
    if url is not None and " => " in value:
        absolute = f"https://docs.digitalasset.com/operate/{source_version}/{url.removeprefix('../')}"
        return f"`[{value}]({absolute})`"
    return f"`{value}`"


def render_command(
    command: ConsoleCommand, *, source_version: str, escape_description_mdx: bool
) -> str:
    lines = [
        f'<div id="{command.anchor}" />',
        "",
        f"### `{command.anchor}`",
        "",
        _summary(command.summary),
    ]
    description = _description(
        command.description,
        argument_names=tuple(name for name, _argument_type, _url in command.arguments),
        escape_mdx=escape_description_mdx,
    )
    if description:
        lines.extend(["", description])
    if command.arguments:
        lines.extend(["", "**Arguments**", ""])
        for name, argument_type, url in command.arguments:
            lines.append(
                f"- `{name}`: {_type_mdx(argument_type, url, source_version=source_version)}"
            )
    if command.return_type:
        lines.extend(
            [
                "",
                f"**Returns:** {_type_mdx(command.return_type, command.return_url, source_version=source_version)}",
            ]
        )
    return "\n".join(lines) + "\n"


def _static_rst_to_mdx(lines: list[str]) -> str:
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped == "..":
            index += 1
            while index < len(lines) and (
                not lines[index] or lines[index][0].isspace()
            ):
                index += 1
            continue
        if re.fullmatch(r"\.\. _[^:]+:", line):
            index += 1
            continue
        if stripped == ".. note::":
            note_lines: list[str] = []
            index += 1
            while index < len(lines) and (
                not lines[index] or lines[index][0].isspace()
            ):
                if lines[index].strip():
                    note_lines.append(lines[index].strip())
                index += 1
            output.extend(
                [
                    '<div id="canton_console_reference">',
                    "",
                    "<Note>",
                    " ".join(note_lines),
                    "</Note>",
                    "",
                    "</div>",
                    "",
                ]
            )
            continue
        if index + 1 < len(lines):
            underline = lines[index + 1]
            if (
                underline
                and len(set(underline)) == 1
                and underline[0] in HEADING_UNDERLINES
            ):
                output.extend(
                    [
                        f"{'#' * HEADING_UNDERLINES[underline[0]]} {_inline_rst_to_mdx(line)}",
                        "",
                    ]
                )
                index += 2
                continue
        if stripped.startswith(".. code-block::"):
            language = stripped.partition("::")[2].strip()
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
            code_lines: list[str] = []
            while index < len(lines) and (
                not lines[index] or lines[index][0].isspace()
            ):
                code_lines.append(lines[index])
                index += 1
            code = textwrap.dedent("\n".join(code_lines)).rstrip()
            output.extend([f"```{language}", code, "```", ""])
            continue
        if stripped.startswith("<console-command:"):
            output.extend([stripped, ""])
            index += 1
            continue
        if not stripped:
            index += 1
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip():
                break
            if candidate.startswith("..") or candidate.startswith("<console-command:"):
                break
            if index + 1 < len(lines):
                underline = lines[index + 1]
                if (
                    underline
                    and len(set(underline)) == 1
                    and underline[0] in HEADING_UNDERLINES
                ):
                    break
            paragraph.append(candidate.strip())
            index += 1
        output.extend([_inline_rst_to_mdx(" ".join(paragraph)), ""])

    return "\n".join(output).strip()


def _apply_current_content_edits(value: str) -> str:
    replacements = {
        "The generated config can be passed to `daml script` via the `participant-config` parameter. More information about the file format can be found in the documentation: It takes three arguments:": (
            "The generated config can be passed to `daml script` via the `--participant-config` flag. "
            "The output is a JSON file with the following structure:\n\n"
            "```json\n"
            "{\n"
            '  "default_participant": {"host": "<host>", "port": <port>},\n'
            '  "participants": {},\n'
            '  "party_participants": {}\n'
            "}\n"
            "```\n\n"
            "It takes three arguments:"
        ),
        "The generated config can be passed to `daml script` via the `participant-config` parameter. More information about the file format can be found in the [documentation](https://docs.daml.com/daml-script/index.html#using-daml-script-in-distributed-topologies): It takes three arguments:": (
            "The generated config can be passed to `daml script` via the `--participant-config` flag. "
            "The output is a JSON file with the following structure:\n\n"
            "```json\n"
            "{\n"
            '  "default_participant": {"host": "<host>", "port": <port>},\n'
            '  "participants": {},\n'
            '  "party_participants": {}\n'
            "}\n"
            "```\n\n"
            "It takes three arguments:"
        ),
        "The generated config can be passed to `daml script` via the `participant-config` parameter.\n\nParameters:": (
            "The generated config can be passed to `daml script` via the `--participant-config` flag. "
            "The output is a JSON file with the following structure:\n\n"
            "```json\n"
            "{\n"
            '  "default_participant": {"host": "<host>", "port": <port>},\n'
            '  "participants": {},\n'
            '  "party_participants": {}\n'
            "}\n"
            "```\n\n"
            "Parameters:"
        ),
        "myparticipaint.synchronizers.to_config": "myparticipant.synchronizers.to_config",
        "Resource limits can only be changed, if the server runs Canton enterprise. In the community edition, the server uses fixed limits that cannot be changed.": "Resource limits can be changed at runtime using this command.",
        "See https://docs.daml.com/app-dev/services.html for documentation of the parameters.": "See the [Ledger API reference](/sdks-tools/api-reference/ledger-api) for documentation of the parameters.",
        r"import-\<random_UUID\>": "import-&lt;random_UUID&gt;",
        "import-<random_UUID>": "import-&lt;random_UUID&gt;",
        "node.topology.transactions.authorize(<synchronizer-id>, <tx-hash>)": (
            "node.topology.transactions.authorize(&lt;synchronizer-id&gt;, &lt;tx-hash&gt;)"
        ),
        r"node.topology.transactions.authorize(\<synchronizer-id\>, \<tx-hash\>)": (
            "node.topology.transactions.authorize(&lt;synchronizer-id&gt;, &lt;tx-hash&gt;)"
        ),
    }
    for before, after in replacements.items():
        value = value.replace(before, after)
    return value


def _apply_legacy_snapshot_edits(value: str) -> str:
    replacements = {
        "- `connection`: The connection string to connect to this synchronizer. I.e. https://url:port\n"
        "- `manualConnect`: Whether this connection should be handled manually and also excluded from automatic re-connect.": (
            "- `connection`: The connection string to connect to this synchronizer. I.e. https://url:port "
            "manualConnect - Whether this connection should be handled manually and also excluded from automatic re-connect."
        ),
        "- `sequencer`: A local sequencer reference\n"
        "- `alias`: The name you will be using to refer to this synchronizer. Can not be changed anymore.": (
            "- `sequencer`: A local sequencer reference alias - The name you will be using to refer to this synchronizer. "
            "Can not be changed anymore."
        ),
        "- `connections`: The sequencer connection definitions (can be an URL) to connect to this synchronizer. I.e. https://url:port\n"
        "- `synchronize`: A timeout duration indicating how long to wait for all topology changes to have been effected on all local nodes.": (
            "- `connections`: The sequencer connection definitions (can be an URL) to connect to this synchronizer. "
            "I.e. https://url:port synchronize - A timeout duration indicating how long to wait for all topology changes "
            "to have been effected on all local nodes."
        ),
        "- `synchronizerAlias`: Alias of the synchronizer\n"
        "- `modifier`: The change to be applied to the config.": (
            "- `synchronizerAlias`: Alias of the synchronizer modifier - The change to be applied to the config."
        ),
        "The arguments are:\n\n"
        "- `ignoreFailures`: If set to true (default), we'll attempt to connect to all, ignoring any failure\n"
        "- `synchronize`: A timeout duration indicating how long to wait for all topology changes to have been effected on all local nodes.": (
            "The arguments are: ignoreFailures - If set to true (default), we'll attempt to connect to all, ignoring any "
            "failure synchronize - A timeout duration indicating how long to wait for all topology changes to have been "
            "effected on all local nodes."
        ),
        "- `synchronizerAlias`: The synchronizer alias to connect to\n"
        "- `retry`: Whether the reconnect should keep on retrying until it succeeded or abort noisily if the connection attempt fails.": (
            "- `synchronizerAlias`: The synchronizer alias to connect to retry - Whether the reconnect should keep on "
            "retrying until it succeeded or abort noisily if the connection attempt fails."
        ),
        "The arguments are: ref\n\n"
        "- The synchronizer reference to connect to retry\n"
        "- Whether the reconnect should keep on retrying until it succeeded or abort noisily if the connection attempt fails. synchronize\n"
        "- A timeout duration indicating how long to wait for all topology changes to have been effected on all local nodes.": (
            "The arguments are:\n\n"
            "- `ref`: The synchronizer reference to connect to retry - Whether the reconnect should keep on retrying until "
            "it succeeded or abort noisily if the connection attempt fails.\n"
            "- `synchronize`: A timeout duration indicating how long to wait for all topology changes to have been effected "
            "on all local nodes."
        ),
        "- `sequencer`: A local sequencer reference\n"
        "- `alias`: The name you will be using to refer to this synchronizer. Cannot be changed anymore.": (
            "- `sequencer`: A local sequencer reference alias - The name you will be using to refer to this synchronizer. "
            "Cannot be changed anymore."
        ),
        "- `config`: Config for the synchronizer connection\n"
        "- `performHandshake`: If true (default), will perform handshake with the synchronizer. If no, will only store configuration without any query to the synchronizer.": (
            "- `config`: Config for the synchronizer connection performHandshake - If true (default), will perform handshake "
            "with the synchronizer. If no, will only store configuration without any query to the synchronizer."
        ),
        "- `validation`: Whether to validate the connectivity and ids of the given sequencers (default All)\n"
        "- `synchronize`: A timeout duration indicating how long to wait for all topology changes to have been effected on all local nodes.": (
            "- `validation`: Whether to validate the connectivity and ids of the given sequencers (default All) synchronize "
            "- A timeout duration indicating how long to wait for all topology changes to have been effected on all local nodes."
        ),
        "- `sequencer`: A local sequencer reference\n"
        "- `alias`: A synchronizer alias to register this connection for.": (
            "- `sequencer`: A local sequencer reference alias - A synchronizer alias to register this connection for."
        ),
    }
    for before, after in replacements.items():
        value = value.replace(before, after)
    return value


def convert_generated_console_rst(
    rst: str,
    *,
    source_version: str,
    header: str,
    apply_current_content_edits: bool,
    apply_legacy_snapshot_edits: bool,
    escape_description_mdx: bool,
    footer: str = "",
) -> str:
    commands, static_lines = parse_generated_console_rst(rst)
    body = _static_rst_to_mdx(static_lines)
    for index, command in enumerate(commands):
        body = body.replace(
            f"<console-command: {index}>",
            render_command(
                command,
                source_version=source_version,
                escape_description_mdx=escape_description_mdx,
            ),
        )
    result = f"{header.rstrip()}\n\n{body.rstrip()}\n"
    if footer:
        result = f"{result.rstrip()}\n\n\n\n{footer.strip()}\n"
    if apply_current_content_edits:
        result = _apply_current_content_edits(result)
    if apply_legacy_snapshot_edits:
        result = _apply_legacy_snapshot_edits(result)
    return result
