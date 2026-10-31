"""Lexer tests: positions are real, and a bad byte is one record, not a crash."""

from __future__ import annotations

from harness_hx.diagnostics import Reporter
from harness_hx.lexer import Lexer


def tokenize(source: str):
    reporter = Reporter("t.hx")
    tokens = Lexer(source, reporter).run()
    return tokens, reporter.diagnostics()


def kinds(source: str) -> list[str]:
    tokens, _ = tokenize(source)
    return [token.kind for token in tokens]


def test_positions_are_one_based_and_track_newlines():
    tokens, _ = tokenize("let x = 1\n  let y = 2\n")
    x_token = next(token for token in tokens if token.value == "x")
    y_token = next(token for token in tokens if token.value == "y")
    assert (x_token.line, x_token.col) == (1, 5)
    assert (y_token.line, y_token.col) == (2, 7)


def test_keywords_are_not_identifiers():
    assert kinds("let x") == ["let", "ident", "eof"]
    assert kinds("module") == ["module", "eof"]


def test_duration_is_a_token_but_money_stays_a_number_and_a_currency():
    # 10m is a duration; 2.00 USD is a float plus a currency name the parser
    # joins inside a budget block, because money has no meaning elsewhere.
    assert kinds("10m") == ["duration", "eof"]
    assert kinds("2.00 USD") == ["float", "ident", "eof"]
    assert kinds("90s 5h 12ms") == ["duration", "duration", "duration", "eof"]


def test_comments_are_skipped_but_strings_keep_their_text():
    tokens, diagnostics = tokenize('// note\n/* block */ "a//b"')
    assert diagnostics == []
    assert [token.kind for token in tokens] == ["string", "eof"]
    assert tokens[0].value == "a//b"


def test_an_unknown_character_is_reported_and_skipped():
    tokens, diagnostics = tokenize("1 @ 2")
    assert [token.kind for token in tokens] == ["int", "int", "eof"]
    assert [diagnostic.code for diagnostic in diagnostics] == ["HX1001"]
    assert (diagnostics[0].line, diagnostics[0].col) == (1, 3)


def test_an_unterminated_string_stops_at_the_newline_and_says_where():
    _, diagnostics = tokenize('"oops\nlet x = 1')
    assert [diagnostic.code for diagnostic in diagnostics] == ["HX1002"]
    assert (diagnostics[0].line, diagnostics[0].col) == (1, 1)


def test_an_unterminated_block_comment_is_reported_once():
    _, diagnostics = tokenize("/* never closed\ntext")
    assert [diagnostic.code for diagnostic in diagnostics] == ["HX1003"]


def test_an_invalid_escape_is_located_at_the_escape_not_the_string():
    tokens, diagnostics = tokenize('"a\\q"')
    assert [diagnostic.code for diagnostic in diagnostics] == ["HX1005"]
    assert diagnostics[0].col == 3
    assert tokens[0].kind == "string"
