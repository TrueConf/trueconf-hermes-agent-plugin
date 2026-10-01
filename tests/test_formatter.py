from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_FORMATTER_PATH = Path(__file__).resolve().parents[1] / "formatter.py"
_FORMATTER_SPEC = importlib.util.spec_from_file_location(
    "trueconf_plugin_formatter_tests",
    _FORMATTER_PATH,
)
assert _FORMATTER_SPEC is not None and _FORMATTER_SPEC.loader is not None
_FORMATTER_MODULE = importlib.util.module_from_spec(_FORMATTER_SPEC)
_FORMATTER_SPEC.loader.exec_module(_FORMATTER_MODULE)
render_trueconf_html = _FORMATTER_MODULE.render_trueconf_html
_THEMATIC_BREAK = _FORMATTER_MODULE._THEMATIC_BREAK


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**bold**", "<b>bold</b>"),
        ("__underline__", "<u>underline</u>"),
        ("*italic* _also italic_", "<i>italic</i> <i>also italic</i>"),
        ("~~gone~~", "<s>gone</s>"),
        ("`x < y`", "<b><i>x &lt; y</i></b>"),
        ("||secret||", "[secret]"),
        ("**a _b_ c**", "<b>a <i>b</i> c</b>"),
    ],
)
def test_inline_dialect(source: str, expected: str) -> None:
    assert render_trueconf_html(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("# Heading", "<b>Heading</b>"),
        ("a\nb", "a<br>b"),
        ("a\n\nb", "a<br><br>b"),
        ("a\n\n\n\nb", "a<br><br>b"),
        ("- a\n  - b\n- c", "• a<br>&nbsp;&nbsp;&nbsp;&nbsp;- b<br>• c"),
        ("3. a\n4. b", "3. a<br>4. b"),
        ("> a\n> b", "<i>«a<br>b»</i>"),
        ("> > nested", "<i>«<i>«nested»</i>»</i>"),
        ("---", "——————————"),
        ("```\nx < y\n```", "<i>x &lt; y</i>"),
        ("```python\nx < y\n```", "python:<br><i>x &lt; y</i>"),
    ],
)
def test_block_dialect(source: str, expected: str) -> None:
    assert render_trueconf_html(source) == expected


def test_nested_bullets_switch_markers_and_indent() -> None:
    assert (
        render_trueconf_html("- a\n  - b\n- c")
        == "• a<br>&nbsp;&nbsp;&nbsp;&nbsp;- b<br>• c"
    )


def test_deep_bullets_indent_per_level() -> None:
    assert render_trueconf_html("- a\n  - b\n    - c\n      - d\n- e") == (
        "• a<br>&nbsp;&nbsp;&nbsp;&nbsp;- b<br>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;▪ c<br>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;▸ d<br>• e"
    )


def test_nested_numbered_lists_indent_without_losing_sequence() -> None:
    assert render_trueconf_html("1. a\n   1. b\n2. c") == (
        "1. a<br>&nbsp;&nbsp;&nbsp;&nbsp;1. b<br>2. c"
    )
    assert render_trueconf_html("1. a\n   1. b\n      1. c\n2. d") == (
        "1. a<br>&nbsp;&nbsp;&nbsp;&nbsp;1. b<br>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;1. c<br>2. d"
    )


def test_mixed_nested_list_uses_bullet_marker_and_indent() -> None:
    assert render_trueconf_html("1. первый\n   - подпункт\n2. второй") == (
        "1. первый<br>&nbsp;&nbsp;&nbsp;&nbsp;- подпункт<br>2. второй"
    )


def test_fenced_block_indentation_survives_trueconf() -> None:
    assert render_trueconf_html("```python\ndef f():\n    return 42\n```") == (
        "python:<br><i>def f():<br>&nbsp;&nbsp;&nbsp;&nbsp;return 42</i>"
    )
    assert render_trueconf_html("```\nx = 1\n        y = 2\n```") == (
        "<i>x = 1<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;y = 2</i>"
    )
    assert render_trueconf_html("```\n\ttabbed\n```") == (
        "<i>&nbsp;&nbsp;&nbsp;&nbsp;tabbed</i>"
    )
    assert render_trueconf_html("```\nx < y\n```") == "<i>x &lt; y</i>"


def test_double_hyphens_become_em_dash_in_prose() -> None:
    assert render_trueconf_html("foo -- bar") == "foo — bar"
    assert render_trueconf_html("a--b") == "a—b"
    assert render_trueconf_html("**x--y**") == "<b>x—y</b>"
    assert render_trueconf_html("Срок -- до 2027 года") == "Срок — до 2027 года"
    assert render_trueconf_html("run -- /start") == "run — /start"
    assert render_trueconf_html("/start -- now") == "/start — now"
    assert render_trueconf_html("/a--b") == "/a—b"


def test_double_hyphens_stay_literal_in_code() -> None:
    assert render_trueconf_html("`--flag`") == "<b><i>--flag</i></b>"
    assert render_trueconf_html("`run --x`") == "<b><i>run --x</i></b>"
    assert render_trueconf_html("```\n--flag\n```") == "<i>--flag</i>"


def test_double_hyphens_in_link_labels_are_em_dashes() -> None:
    assert render_trueconf_html("[a--b](https://example.com)") == (
        '<a href="https://example.com">a—b</a>'
    )


def test_thematic_break_stays_em_dash_line() -> None:
    assert render_trueconf_html("---") == _THEMATIC_BREAK


def test_table_is_linearized_without_losing_empty_cells() -> None:
    source = "| **Name** | Value |\n| --- | --- |\n| one | |"

    assert render_trueconf_html(source) == (
        f"<b><b>Name</b> | Value</b><br>{_THEMATIC_BREAK}<br>one | "
    )


def test_ragged_table_pads_fewer_cells() -> None:
    source = "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 |"

    assert render_trueconf_html(source) == (
        f"<b>a | b</b><br>{_THEMATIC_BREAK}<br>1 | 2<br>3 | "
    )


def test_ragged_table_keeps_extra_cells() -> None:
    source = "| a | b |\n| --- | --- |\n| 1 | 2 | 3 |"

    assert render_trueconf_html(source) == (
        f"<b>a | b</b><br>{_THEMATIC_BREAK}<br>1 | 2 | 3"
    )


def test_wide_table_renders_as_labeled_blocks() -> None:
    source = (
        "| Q1 | Q2 | Q3 | Q4 | Итог | Δ к прошлому | Прогноз |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 120 | 135 | 128 | 150 | 533 | +12% | 160 |"
    )

    assert render_trueconf_html(source) == (
        "<b>Q1:</b> 120<br><b>Q2:</b> 135<br><b>Q3:</b> 128<br>"
        "<b>Q4:</b> 150<br><b>Итог:</b> 533<br>"
        "<b>Δ к прошлому:</b> +12%<br><b>Прогноз:</b> 160"
    )


def test_five_column_table_renders_as_labeled_blocks() -> None:
    source = (
        "| Язык | Год | Автор | Звёзды на GitHub | Статус |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| Python | 1991 | Guido van Rossum | ~600k | Активен |"
    )

    assert render_trueconf_html(source) == (
        "<b>Язык:</b> Python<br><b>Год:</b> 1991<br>"
        "<b>Автор:</b> Guido van Rossum<br>"
        "<b>Звёзды на GitHub:</b> ~600k<br><b>Статус:</b> Активен"
    )


def test_long_cell_renders_table_as_labeled_blocks() -> None:
    source = (
        "| Задача | Описание | Приоритет |\n"
        "| --- | --- | --- |\n"
        "| Рефакторинг API | Разбить монолит на микросервисы; мигрировать клиенты; "
        "обеспечить обратную совместимость через 2 релиза | Высокий |\n"
        "| База данных | Перенос с MySQL на PostgreSQL; проверить индексы и триггеры "
        "| Средний |"
    )

    assert render_trueconf_html(source) == (
        "<b>Задача:</b> Рефакторинг API<br>"
        "<b>Описание:</b> Разбить монолит на микросервисы; мигрировать клиенты; "
        "обеспечить обратную совместимость через 2 релиза<br>"
        "<b>Приоритет:</b> Высокий<br><br>"
        "<b>Задача:</b> База данных<br>"
        "<b>Описание:</b> Перенос с MySQL на PostgreSQL; проверить индексы и триггеры<br>"
        "<b>Приоритет:</b> Средний"
    )


def test_compact_threshold_keeps_24_character_cells_compact() -> None:
    source = f"| a | b |\n| --- | --- |\n| {'x' * 24} | y |"

    assert render_trueconf_html(source) == (
        f"<b>a | b</b><br>{_THEMATIC_BREAK}<br>{'x' * 24} | y"
    )


def test_compact_threshold_pushes_25_character_cells_to_blocks() -> None:
    source = f"| a | b |\n| --- | --- |\n| {'x' * 25} | y |"

    assert render_trueconf_html(source) == (f"<b>a:</b> {'x' * 25}<br><b>b:</b> y")


def test_block_table_empty_header_cell_uses_positional_label() -> None:
    source = f"| a |  | c |\n| --- | --- | --- |\n| 1 | 2 | {'o' * 30} |"

    assert render_trueconf_html(source) == (
        f"<b>a:</b> 1<br><b>2:</b> 2<br><b>c:</b> {'o' * 30}"
    )


def test_block_table_keeps_cells_beyond_labels() -> None:
    source = f"| a | b | c |\n| --- | --- | --- |\n| 1 | 2 | {'o' * 30} | 4 |"

    assert render_trueconf_html(source) == (
        f"<b>a:</b> 1<br><b>b:</b> 2<br><b>c:</b> {'o' * 30}<br>4"
    )


def test_block_table_pads_missing_cells_to_empty_values() -> None:
    source = f"| a | b | c |\n| --- | --- | --- |\n| x | {'o' * 30} | y |\n| 1 | 2 |"

    assert render_trueconf_html(source) == (
        f"<b>a:</b> x<br><b>b:</b> {'o' * 30}<br><b>c:</b> y<br><br>"
        "<b>a:</b> 1<br><b>b:</b> 2<br><b>c:</b>"
    )


def test_compact_table_commands_stay_bare() -> None:
    source = "| Команда | Действие |\n| --- | --- |\n| /start | запуск |"

    assert render_trueconf_html(source) == (
        f"<b>Команда | Действие</b><br>{_THEMATIC_BREAK}<br>/start | запуск"
    )


def test_compact_table_hyphenated_command_underscored() -> None:
    source = "| Команда | Действие |\n| --- | --- |\n| /claude-code | запуск |"

    assert render_trueconf_html(source) == (
        f"<b>Команда | Действие</b><br>{_THEMATIC_BREAK}<br>/claude_code | запуск"
    )


def test_block_table_commands_stay_bare() -> None:
    source = (
        f"| Задача | Описание | Приоритет |\n| --- | --- | --- |\n"
        f"| /start | {'д' * 30} | Высокий |"
    )

    assert render_trueconf_html(source) == (
        f"<b>Задача:</b> /start<br><b>Описание:</b> {'д' * 30}<br>"
        "<b>Приоритет:</b> Высокий"
    )


@pytest.mark.parametrize(
    "source",
    ["**/start**", "_/help@bot_", "~~/stop~~", "__/again__", "# /start"],
)
def test_clean_decorated_commands_are_bare(source: str) -> None:
    expected = (
        "/help@bot"
        if "help" in source
        else (
            "/again" if "again" in source else "/stop" if "stop" in source else "/start"
        )
    )
    assert render_trueconf_html(source) == expected


def test_nested_command_is_bare_but_linked_command_is_linked() -> None:
    assert render_trueconf_html("**_/start_**") == "/start"
    assert render_trueconf_html("[/start](https://example.com)") == (
        '<a href="https://example.com">/start</a>'
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**/claude-code**", "/claude_code"),
        ("***/claude-code***", "/claude_code"),
        ("`/github-code-review`", "/github_code_review"),
        ("__/document-to-action-items__", "/document_to_action_items"),
        ("~~/computer-use~~", "/computer_use"),
        ("**/claude-code@bot**", "/claude_code@bot"),
        ("**run /claude-code now**", "<b>run</b> /claude_code <b>now</b>"),
        ("`run /claude-code now`", "<b><i>run</i></b> /claude_code <b><i>now</i></b>"),
        ("/Foo-Bar", "/Foo_Bar"),
        ("/claude-code -- now", "/claude_code — now"),
        (
            "[/claude-code](https://example.com)",
            '<a href="https://example.com">/claude_code</a>',
        ),
    ],
)
def test_hyphenated_commands_render_underscored(source: str, expected: str) -> None:
    assert render_trueconf_html(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**run /start now**", "<b>run</b> /start <b>now</b>"),
        ("_use /help@bot now_", "<i>use</i> /help@bot <i>now</i>"),
        ("~~/stop now~~", "/stop <s>now</s>"),
        ("# run /start now", "<b>run</b> /start <b>now</b>"),
        ("/start", "/start"),
        ("run /start", "run /start"),
    ],
)
def test_embedded_commands_are_bare(source: str, expected: str) -> None:
    assert render_trueconf_html(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("`/start`", "/start"),
        ("Нажми `/start`", "Нажми /start"),
        ("`run /start now`", "<b><i>run</i></b> /start <b><i>now</i></b>"),
    ],
)
def test_backtick_commands_are_bare(source: str, expected: str) -> None:
    assert render_trueconf_html(source) == expected


def test_command_boundary_spaces_are_regular_and_outside_tags() -> None:
    source = (
        "⚠️ **Confirm /new**\n\n"
        "_Text fallback: reply `/approve`, `/always`, or `/cancel`._"
    )

    assert render_trueconf_html(source) == (
        "⚠️ <b>Confirm</b> /new<br><br>"
        "<i>Text fallback: reply</i> /approve<i>,</i> /always"
        "<i>, or</i> /cancel<i>.</i>"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**open https://example.com/start**", "<b>open https://example.com/start</b>"),
        ("see trueconf:user/stop now", "see trueconf:user/stop now"),
        ("**foo/bar**", "<b>foo/bar</b>"),
        ("**foo/bar-baz**", "<b>foo/bar-baz</b>"),
    ],
)
def test_command_like_url_paths_are_not_commands(source: str, expected: str) -> None:
    assert render_trueconf_html(source) == expected


@pytest.mark.parametrize(
    ("url", "rendered"),
    [
        (
            "https://example.com?a=1&b=2",
            '<a href="https://example.com?a=1&amp;b=2">x</a>',
        ),
        ("HTTP://example.com", '<a href="HTTP://example.com">x</a>'),
        ("mailto:user@example.com", '<a href="mailto:user@example.com">x</a>'),
        ("trueconf:user@server", '<a href="trueconf:user@server">x</a>'),
        ("javascript:alert(1)", "x (javascript:alert(1))"),
        ("//example.com/path", "x (//example.com/path)"),
        ("relative/path", "x (relative/path)"),
    ],
)
def test_link_allowlist(url: str, rendered: str) -> None:
    assert render_trueconf_html(f"[x]({url})") == rendered


def test_malformed_source_url_degrades() -> None:
    assert render_trueconf_html("[x](http://[)") == "x (http://[)"
    assert render_trueconf_html("[x](https://example.com/a%20b)") == (
        '<a href="https://example.com/a%20b">x</a>'
    )


def test_literal_html_in_backticks_is_not_command_split() -> None:
    assert render_trueconf_html("Use `<b>bold</b>` to bold") == (
        "Use <b><i>&lt;b&gt;bold&lt;/b&gt;</i></b> to bold"
    )
    assert render_trueconf_html('write `<a href="x">y</a>` now') == (
        "write <b><i>&lt;a href=&quot;x&quot;&gt;y&lt;/a&gt;</i></b> now"
    )


def test_degraded_anchor_state_is_per_call() -> None:
    render_trueconf_html('<a href="javascript:y">b')

    assert render_trueconf_html("</a> stray") == "</a> stray"


def test_raw_html_passes_through_and_text_is_escaped() -> None:
    assert render_trueconf_html("<script>x</script> <b>y</b> <quote>z</quote> &") == (
        "<script>x</script> <b>y</b> <quote>z</quote> &amp;"
    )


def test_raw_html_supported_underline_renders() -> None:
    source = "**Жирный** и *курсив*, а также <u>подчёркнутый</u> и ~~зачёркнутый~~."

    assert render_trueconf_html(source) == (
        "<b>Жирный</b> и <i>курсив</i>, а также "
        "<u>подчёркнутый</u> и <s>зачёркнутый</s>."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('<a href="https://x.com">l</a>', '<a href="https://x.com">l</a>'),
        (
            '<a href="javascript:alert(1)">l</a>',
            "&lt;a href=&quot;javascript:alert(1)&quot;&gt;l&lt;/a&gt;",
        ),
        (
            '<a href="javascript:x">a</a> ok <a href="https://y.com">b</a>',
            "&lt;a href=&quot;javascript:x&quot;&gt;a&lt;/a&gt; "
            'ok <a href="https://y.com">b</a>',
        ),
        ("<u class='x'>u</u>", "<u class='x'>u</u>"),
        ("<br>", "<br>"),
    ],
)
def test_raw_html_tag_policy(source: str, expected: str) -> None:
    assert render_trueconf_html(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", ""),
        ("\n", ""),
        ("a\r\nb", "a<br>b"),
        ("a\rb", "a<br>b"),
        ("**open", "**open"),
        ("`open", "`open"),
        ("[open](", "[open]("),
        ("```\nopen", "<i>open</i>"),
    ],
)
def test_boundaries_and_malformed_markdown(source: str, expected: str) -> None:
    assert render_trueconf_html(source) == expected


def test_rendering_can_increase_visible_length_past_sdk_limit() -> None:
    source = "\n".join(f"- {'x' * 1000}" for _ in range(4)) + "\n- " + "x" * 87

    rendered = render_trueconf_html(source)

    assert len(rendered.replace("<br>", "")) > 4096
