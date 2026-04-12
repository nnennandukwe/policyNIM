"""Tests for the ingest foundation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from policynim.errors import InvalidPolicyDocumentError
from policynim.ingest import (
    MarkdownParser,
    chunk_policy_document,
    chunk_policy_documents,
    load_policy_documents,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICIES_DIR = REPO_ROOT / "policies"


def write_policy(path: Path, content: str) -> None:
    """Write one test policy document, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip(), encoding="utf-8")


def test_template_perfect_markdown_parses_into_normalized_document(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "perfect.md",
        """
        ---
        policy_id: BE-DEMO-001
        title: Perfect Policy
        doc_type: standard
        domain: backend
        tags:
          - demo
        grounded_in:
          - https://example.com/source
        ---
        # Perfect Policy

        ## Intent

        Keep the service safe.
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]

    assert document.source_path == "policies/backend/perfect.md"
    assert document.metadata.policy_id == "BE-DEMO-001"
    assert document.metadata.title == "Perfect Policy"
    assert document.metadata.doc_type == "standard"
    assert document.metadata.domain == "backend"
    assert document.metadata.tags == ["demo"]
    assert document.metadata.grounded_in == ["https://example.com/source"]


def test_markdown_without_frontmatter_uses_inferred_metadata(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "security" / "session-boundaries.md",
        """
        # Session Boundaries

        ## Intent

        Tokens must expire cleanly.
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]

    assert document.metadata.title == "Session Boundaries"
    assert document.metadata.policy_id == "SECURITY-SESSION-BOUNDARIES"
    assert document.metadata.domain == "security"
    assert document.metadata.doc_type == "guidance"
    assert document.metadata.tags == []
    assert document.metadata.grounded_in == []


def test_malformed_frontmatter_fails_fast(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "broken.md",
        """
        ---
        title Broken
        ---
        # Broken
        """,
    )

    with pytest.raises(InvalidPolicyDocumentError):
        load_policy_documents(tmp_path / "policies")


def test_duplicate_effective_policy_ids_fail_loudly(tmp_path: Path) -> None:
    duplicate = """
        ---
        policy_id: DUP-001
        ---
        # Duplicate Policy

        Body text.
    """
    write_policy(tmp_path / "policies" / "backend" / "first.md", duplicate)
    write_policy(tmp_path / "policies" / "backend" / "second.md", duplicate)

    with pytest.raises(InvalidPolicyDocumentError):
        load_policy_documents(tmp_path / "policies")


def test_chunk_ids_are_deterministic_with_repeated_headings(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "deterministic.md",
        """
        ---
        policy_id: BE-CHUNK-001
        title: Chunk Policy
        ---
        # Chunk Policy

        Intro text

        ## Repeated

        - first

        ## Repeated

        - second
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]
    first_run = chunk_policy_document(document)
    second_run = chunk_policy_document(document)

    assert [chunk.chunk_id for chunk in first_run] == [chunk.chunk_id for chunk in second_run]
    assert [chunk.lines for chunk in first_run] == ["5-8", "9-12", "13-15"]
    assert first_run[1].chunk_id == "BE-CHUNK-001:chunk-policy__repeated"
    assert first_run[2].chunk_id == "BE-CHUNK-001:chunk-policy__repeated-2"


def test_heading_paths_and_line_spans_follow_markdown_structure(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "structure.md",
        """
        # Root

        Overview text

        ## API Rules

        ```python
        # not-a-heading
        ```

        - keep contracts stable

        ### Edge Cases

        Handle retries carefully.
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]
    chunks = chunk_policy_document(document)

    assert [chunk.section for chunk in chunks] == [
        "Root",
        "Root > API Rules",
        "Root > API Rules > Edge Cases",
    ]
    assert chunks[1].lines == "5-12"
    assert "# not-a-heading" in chunks[1].text
    assert "not-a-heading" not in chunks[1].section


def test_preamble_content_is_preserved_before_first_heading(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "preamble.md",
        """
        Intro text before headings.

        More setup context.

        # Root

        ## Intent

        Keep the preamble.
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]
    chunks = chunk_policy_document(document)
    parser_chunks = chunk_policy_document(document, parser=MarkdownParser())

    assert chunks[0].section == "Root > Preamble"
    assert chunks[0].lines == "1-4"
    assert "Intro text before headings." in chunks[0].text
    assert chunks[1].section == "Root"
    assert [chunk.section for chunk in parser_chunks[:2]] == ["Root > Preamble", "Root"]


def test_inline_lists_allow_escaped_quotes(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "escaped-list.md",
        """
        ---
        title: Escaped Quotes
        tags: ["say \\\"hello\\\"", plain]
        grounded_in: ["https://example.com/a"]
        ---
        # Escaped Quotes

        ## Intent

        Keep quoted list items readable.
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]

    assert document.metadata.tags == ['say "hello"', "plain"]


def test_blank_sections_keep_stable_line_spans_until_next_heading(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "blank-section.md",
        """
        # Root

        ## Empty Rules

        ### Filled Rules

        Keep the last section anchored to the end of the document.
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]
    chunks = chunk_policy_document(document)

    assert [chunk.section for chunk in chunks] == [
        "Root",
        "Root > Empty Rules",
        "Root > Empty Rules > Filled Rules",
    ]
    assert [chunk.lines for chunk in chunks] == ["1-2", "3-4", "5-7"]


def test_duplicate_nested_heading_paths_get_numeric_chunk_suffixes(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "nested-duplicates.md",
        """
        # Root

        ## API

        ### Rules

        Keep request ids.

        ## API

        ### Rules

        Preserve idempotency.
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]
    chunks = chunk_policy_document(document)

    assert [chunk.chunk_id for chunk in chunks] == [
        "BACKEND-NESTED-DUPLICATES:root",
        "BACKEND-NESTED-DUPLICATES:root__api",
        "BACKEND-NESTED-DUPLICATES:root__api__rules",
        "BACKEND-NESTED-DUPLICATES:root__api-2",
        "BACKEND-NESTED-DUPLICATES:root__api__rules-2",
    ]


def test_runtime_rules_parse_with_line_spans_and_matcher_values(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "runtime-rules.md",
        """
        ---
        policy_id: BACKEND-RUNTIME-001
        title: Runtime Rules
        domain: backend
        runtime_rules:
          - action: shell_command
            effect: confirm
            reason: Review dangerous deploy commands.
            command_regexes:
              - "^deploy:"
          - action: file_write
            effect: block
            reason: Protect production secrets files.
            path_globs: ["secrets/prod.env"]
        ---
        # Runtime Rules

        ## Intent

        Keep runtime actions bounded.
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]

    assert len(document.runtime_rules) == 2
    assert document.runtime_rules[0].action == "shell_command"
    assert document.runtime_rules[0].command_regexes == ["^deploy:"]
    assert document.runtime_rules[0].start_line == 6
    assert document.runtime_rules[0].end_line == 10
    assert document.runtime_rules[1].path_globs == ["secrets/prod.env"]
    assert document.runtime_rules[1].start_line == 11
    assert document.runtime_rules[1].end_line == 14


def test_runtime_rules_inline_matcher_lists_preserve_quoted_scalars(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "quoted-inline-matchers.md",
        """
        ---
        runtime_rules:
          - action: file_write
            effect: block
            reason: Preserve quoted matcher scalars.
            path_globs: ["true", "1"]
        ---
        # Quoted Inline Matchers
        """,
    )

    document = load_policy_documents(tmp_path / "policies")[0]

    assert document.runtime_rules[0].path_globs == ["true", "1"]


def test_runtime_rules_inline_matcher_list_errors_keep_file_and_line_context(
    tmp_path: Path,
) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "broken-inline-matchers.md",
        """
        ---
        runtime_rules:
          - action: file_write
            effect: block
            reason: Broken inline matcher.
            path_globs: ["unterminated]
        ---
        # Broken Inline Matchers
        """,
    )

    with pytest.raises(
        InvalidPolicyDocumentError,
        match=(
            r"Policy document policies/backend/broken-inline-matchers\.md has "
            r"invalid runtime_rules at line 6"
        ),
    ):
        load_policy_documents(tmp_path / "policies")


def test_runtime_rules_key_requires_at_least_one_rule_entry(tmp_path: Path) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "empty-runtime-rules.md",
        """
        ---
        runtime_rules:
        ---
        # Empty Runtime Rules
        """,
    )

    with pytest.raises(
        InvalidPolicyDocumentError,
        match="runtime_rules must include at least one rule entry",
    ):
        load_policy_documents(tmp_path / "policies")


@pytest.mark.parametrize(
    ("matcher_field", "matcher_value"),
    [
        ("path_globs", "{prod: true}"),
        ("command_regexes", "[1, 2]"),
    ],
)
def test_runtime_rules_reject_non_string_matcher_payloads(
    tmp_path: Path,
    matcher_field: str,
    matcher_value: str,
) -> None:
    write_policy(
        tmp_path / "policies" / "backend" / "invalid-runtime-rule.md",
        f"""
        ---
        runtime_rules:
          - action: shell_command
            effect: confirm
            reason: Invalid matcher payload.
            {matcher_field}: {matcher_value}
        ---
        # Invalid Runtime Rule
        """,
    )

    with pytest.raises(
        InvalidPolicyDocumentError,
        match="matcher",
    ):
        load_policy_documents(tmp_path / "policies")


def test_shipped_policy_docs_yield_non_empty_chunks() -> None:
    documents = load_policy_documents(POLICIES_DIR)
    chunks = chunk_policy_documents(documents)

    assert len(documents) >= 8
    assert len({document.metadata.policy_id for document in documents}) == len(documents)
    assert all(chunk.text.strip() for chunk in chunks)
    assert {chunk.path for chunk in chunks} == {document.source_path for document in documents}
