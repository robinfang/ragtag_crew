from __future__ import annotations

from ragtag_crew.telegram.html import (
    _convert_markdown_tables,
    _render_table_as_code_block,
    render_telegram_html,
)


class TestConvertMarkdownTables:
    def test_simple_table_converted(self):
        md = "| Name | Value |\n| --- | --- |\n| foo | bar |"
        result = _convert_markdown_tables(md)
        assert "```" in result
        assert "foo" in result
        assert "bar" in result

    def test_table_with_header_and_separator(self):
        md = "| Col A | Col B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
        result = _convert_markdown_tables(md)
        assert "Col A" in result
        assert "Col B" in result
        assert "1" in result
        assert "3" in result

    def test_no_table_unchanged(self):
        text = "just some text\nwith no table"
        assert _convert_markdown_tables(text) == text

    def test_table_surrounded_by_text(self):
        md = "before\n| A | B |\n| -- | -- |\n| x | y |\nafter"
        result = _convert_markdown_tables(md)
        assert result.startswith("before\n")
        assert result.endswith("\nafter")
        assert "```" in result

    def test_multiple_tables(self):
        md = "| A | B |\n| -- | -- |\n| 1 | 2 |\n\nsome text\n\n| C | D |\n| -- | -- |\n| 3 | 4 |"
        result = _convert_markdown_tables(md)
        assert "```" in result
        blocks = result.split("```")
        assert len(blocks) == 5  # before + block + between + block + after

    def test_empty_table_not_converted(self):
        md = "| | |\n| --- | --- |"
        result = _convert_markdown_tables(md)
        assert "```" not in result

    def test_table_with_alignment_syntax(self):
        md = "| Left | Center | Right |\n| :--- | :---: | ---: |\n| a | b | c |"
        result = _convert_markdown_tables(md)
        assert "Left" in result
        assert "Center" in result
        assert "Right" in result
        assert "a" in result

    def test_table_at_end_of_text(self):
        md = "intro\n| X | Y |\n| -- | -- |\n| 1 | 2 |"
        result = _convert_markdown_tables(md)
        assert result.startswith("intro\n")
        assert "```" in result


class TestRenderTableAsCodeBlock:
    def test_basic_render(self):
        lines = ["| A | B |", "| -- | -- |", "| 1 | 2 |"]
        result = _render_table_as_code_block(lines)
        assert result.startswith("```")
        assert result.endswith("```")
        assert "A" in result
        assert "1" in result

    def test_column_alignment(self):
        lines = [
            "| Name | Description |",
            "| -- | -- |",
            "| short | long description here |",
            "| very long name | short |",
        ]
        result = _render_table_as_code_block(lines)
        lines_in_block = result.split("\n")
        name_lengths = [
            len(line.split("|")[1])
            for line in lines_in_block
            if "|" in line and not line.startswith("-")
        ]
        assert len(set(name_lengths)) == 1

    def test_three_columns(self):
        lines = [
            "| Col1 | Col2 | Col3 |",
            "| -- | -- | -- |",
            "| a | b | c |",
        ]
        result = _render_table_as_code_block(lines)
        assert "Col1" in result
        assert "Col2" in result
        assert "Col3" in result


class TestRenderTelegramHtmlWithTables:
    def test_table_renders_as_pre_code(self):
        md = "| Key | Val |\n| -- | -- |\n| a | b |"
        result = render_telegram_html(md)
        assert "<pre><code>" in result
        assert "Key" in result

    def test_table_and_code_block_coexist(self):
        text = "```\nsome code\n```\n| X | Y |\n| -- | -- |\n| 1 | 2 |"
        result = render_telegram_html(text)
        assert result.count("<pre><code>") == 2

    def test_table_with_bold_still_escapes(self):
        text = "| **Header** | Value |\n| -- | -- |\n| **bold** | normal |"
        result = _convert_markdown_tables(text)
        assert "```" in result

    def test_fenced_code_block_table_is_not_rewritten(self):
        text = "```md\n| A | B |\n| -- | -- |\n| 1 | 2 |\n```"
        result = render_telegram_html(text)
        assert result.count("<pre><code>") == 1
        assert "A | B" in result
        assert "-+-" not in result
