from __future__ import annotations

from pathlib import Path
from passwatcher import render as render_module
from passwatcher.csv_import import CsvFormat, CsvIssue, ImportPreview
from passwatcher.render import Renderer
from passwatcher.service import ImportSummary


def preview() -> ImportPreview:
    return ImportPreview(
        format=CsvFormat.PASSWATCHER,
        total=3,
        inserted=1,
        updated=1,
        skipped=1,
        ignored_columns=("timeCreated",),
    )


def test_plain_import_preview_contains_only_format_and_counts(capsys) -> None:
    """Catches plain previews becoming unstable or revealing row contents."""
    Renderer(plain=True).import_preview(preview())

    assert capsys.readouterr().out.splitlines() == [
        "Import preview",
        "Format: passwatcher",
        "Total: 3",
        "Insert: 1",
        "Update: 1",
        "Skip: 1",
        "Ignored columns: timeCreated",
    ]


def test_plain_import_errors_show_only_row_field_and_safe_message(capsys) -> None:
    """Catches invalid CSV cell values being copied into terminal output."""
    Renderer(plain=True).import_errors(
        [
            CsvIssue(7, "password", "Required field is empty"),
            CsvIssue(None, "header", "Missing username column"),
        ]
    )

    assert capsys.readouterr().out.splitlines() == [
        "Import validation failed",
        "Row 7 | password | Required field is empty",
        "Header | header | Missing username column",
    ]


def test_plain_export_warning_describes_plaintext_and_spreadsheet_risk(
    capsys, tmp_path: Path
) -> None:
    """Catches export confirmation omitting its two primary security risks."""
    Renderer(plain=True).export_warning(
        tmp_path / "passwords.csv", CsvFormat.PASSWATCHER
    )

    output = capsys.readouterr().out
    assert "plaintext" in output.lower()
    assert "spreadsheet" in output.lower()
    assert "passwords.csv" in output


def test_plain_completion_summaries_are_non_secret_and_exact(
    capsys, tmp_path: Path
) -> None:
    """Catches success output printing imported or exported credential values."""
    renderer = Renderer(plain=True)
    renderer.import_complete(ImportSummary(total=4, inserted=2, updated=1, skipped=1))
    renderer.export_complete(4, tmp_path / "passwords.csv", CsvFormat.BROWSER)

    assert capsys.readouterr().out.splitlines() == [
        "Import complete: 2 inserted, 1 updated, 1 skipped.",
        f"Export complete: 4 records in browser format at {tmp_path / 'passwords.csv'}.",
    ]


def test_styled_csv_output_reuses_theme_without_secrets(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    """Catches new workflows bypassing the existing colored renderer."""
    monkeypatch.setattr(render_module, "_color_output_available", lambda: True)
    renderer = Renderer()

    renderer.import_preview(preview())
    renderer.export_warning(tmp_path / "hidden-secret.csv", CsvFormat.BROWSER)
    renderer.import_complete(ImportSummary(total=3, inserted=1, updated=1, skipped=1))

    output = capsys.readouterr().out
    assert "\x1b[" in output
    assert "Import preview" in output
    assert "timeCreated" in output
    assert "plaintext" in output.lower()
    assert "Import complete" in output
    assert "credential-password-sentinel" not in output
