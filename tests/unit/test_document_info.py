"""Unit tests for core/document_info.py - the Qt-free reader behind
File > Properties.

Every branch is checked against a real PDF built to exercise it
(encrypted with known permission flags, linearized, tagged, mixed page
sizes, fully-populated and completely-empty metadata, structurally
broken), not just "didn't raise" - this project's standing convention.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pikepdf
import pytest

from core.document_info import (
    _DOCINFO_KEYS,
    human_readable_size,
    parse_pdf_date,
    points_to_inches,
    points_to_mm,
    read_document_info,
)
from core.file_times import birth_time
from core.model.document import DocumentSession
from core.ops.metadata import _FIELD_TO_DOCINFO_KEY, SetMetadataOperation


def _make_pdf(path: Path, sizes: list[tuple[float, float]]) -> Path:
    pdf = pikepdf.Pdf.new()
    for size in sizes:
        pdf.add_blank_page(page_size=size)
    pdf.save(path)
    return path


def _letter(path: Path, pages: int = 1) -> Path:
    return _make_pdf(path, [(612, 792)] * pages)


# --- the reader/writer agreement -------------------------------------------


def test_docinfo_keys_match_the_operation_that_writes_them() -> None:
    """The six editable fields must be read back under exactly the
    names and keys `SetMetadataOperation` writes them with, or the
    Properties dialog and Tools > Metadata would quietly disagree.
    Pinned here instead of importing across the ops/core boundary (see
    core/document_info.py's comment on _DOCINFO_KEYS)."""
    shared = {name: key for name, key in _DOCINFO_KEYS.items() if name in _FIELD_TO_DOCINFO_KEY}
    assert shared == _FIELD_TO_DOCINFO_KEY
    # /Creator and /Producer are read-only extras, not writable fields.
    assert set(_DOCINFO_KEYS) - set(_FIELD_TO_DOCINFO_KEY) == {"creator", "producer"}


def test_metadata_written_by_set_metadata_reads_back_identically(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    doc = DocumentSession(working_path=_letter(session_dir / "working.pdf"), source_path=None)

    result = doc.apply(
        SetMetadataOperation(
            fields={
                "title": "Q1 Report",
                "author": "Radwan",
                "subject": "Numbers",
                "keywords": "q1, finance",
                "creation_date": "2025-06-03T12:00:00+02:00",
                "mod_date": "2025-06-04T13:00:00+00:00",
            }
        )
    )

    info = read_document_info(result.working_path)
    assert info.metadata is not None
    assert info.metadata.title == "Q1 Report"
    assert info.metadata.author == "Radwan"
    assert info.metadata.subject == "Numbers"
    assert info.metadata.keywords == "q1, finance"
    # Round-trips as the same ISO 8601 the dialog asked the user for.
    assert info.metadata.creation_date == "2025-06-03T12:00:00+02:00"
    assert info.metadata.mod_date == "2025-06-04T13:00:00+00:00"


# --- metadata ---------------------------------------------------------------


def test_absent_metadata_reads_as_none_not_empty_string(tmp_path: Path) -> None:
    info = read_document_info(_letter(tmp_path / "plain.pdf"))
    assert info.metadata is not None
    assert info.metadata.title is None
    assert info.metadata.producer is None


def test_present_but_blank_metadata_is_distinguishable_from_absent(tmp_path: Path) -> None:
    path = _letter(tmp_path / "blank_title.pdf")
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.docinfo["/Title"] = ""
        pdf.save(path)

    info = read_document_info(path)
    assert info.metadata is not None
    assert info.metadata.title == ""  # present, blank
    assert info.metadata.author is None  # absent


def test_creator_and_producer_are_reported(tmp_path: Path) -> None:
    path = _letter(tmp_path / "produced.pdf")
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.docinfo["/Creator"] = "Some Editor"
        pdf.docinfo["/Producer"] = "Some Library 2.0"
        pdf.save(path)

    info = read_document_info(path)
    assert info.metadata is not None
    assert info.metadata.creator == "Some Editor"
    assert info.metadata.producer == "Some Library 2.0"


def test_unparseable_date_is_shown_raw_rather_than_dropped(tmp_path: Path) -> None:
    path = _letter(tmp_path / "baddate.pdf")
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.docinfo["/CreationDate"] = "sometime last Tuesday"
        pdf.save(path)

    info = read_document_info(path)
    assert info.metadata is not None
    assert info.metadata.creation_date == "sometime last Tuesday"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("D:20250603120000+02'00'", "2025-06-03T12:00:00+02:00"),
        ("D:20250603120000-05'30'", "2025-06-03T12:00:00-05:30"),
        ("D:20250603120000Z", "2025-06-03T12:00:00+00:00"),
        ("D:20250603120000", "2025-06-03T12:00:00"),  # no offset: not invented
        ("D:2025", "2025-01-01T00:00:00"),
        ("20250603120000", "2025-06-03T12:00:00"),  # missing D: prefix
    ],
)
def test_pdf_date_parsing(raw: str, expected: str) -> None:
    parsed = parse_pdf_date(raw)
    assert parsed is not None
    assert parsed.isoformat() == expected


@pytest.mark.parametrize("raw", ["", "garbage", "D:20251345000000", "D:99"])
def test_unparseable_pdf_dates_return_none(raw: str) -> None:
    assert parse_pdf_date(raw) is None


# --- file on disk ------------------------------------------------------------


def test_file_info_describes_the_source_not_the_working_copy(tmp_path: Path) -> None:
    source = _letter(tmp_path / "original.pdf", 1)
    working = _letter(tmp_path / "working.pdf", 3)

    info = read_document_info(working, source_path=source, has_unsaved_changes=True)

    assert info.file.path == source
    assert info.file.exists
    assert info.file.size_bytes == source.stat().st_size
    assert info.file.has_unsaved_changes is True
    assert info.file.modified is not None
    # ...while the parsed sections describe the working copy.
    assert info.geometry is not None
    assert info.geometry.page_count == 3


def test_file_info_for_a_document_never_saved_to_disk(tmp_path: Path) -> None:
    info = read_document_info(_letter(tmp_path / "working.pdf"), source_path=None)
    assert info.file.path is None
    assert info.file.exists is False
    assert info.file.size_bytes is None


def test_file_info_for_a_source_that_has_since_been_deleted(tmp_path: Path) -> None:
    working = _letter(tmp_path / "working.pdf")
    info = read_document_info(working, source_path=tmp_path / "gone.pdf")
    assert info.file.exists is False
    assert info.file.size_bytes is None
    # The document itself is still perfectly readable.
    assert info.geometry is not None


def test_creation_time_is_reported_on_a_filesystem_that_records_one(tmp_path: Path) -> None:
    """Including on Linux, where `os.stat()` has no `st_birthtime` and
    `core/file_times.py` calls `statx()` to get it - see
    `tests/unit/test_file_times.py` for that mechanism itself.

    Skipped only where the *filesystem under tmp_path* genuinely
    records no birth time, which is a real possibility (some tmpfs and
    NFS mounts) - not where the platform merely needs a different
    syscall to read one.
    """
    source = _letter(tmp_path / "src.pdf")
    if birth_time(source) is None:
        pytest.skip("this filesystem records no creation time")

    info = read_document_info(source, source_path=source)
    assert isinstance(info.file.created, datetime)
    # A file created moments ago must read as created moments ago -
    # `st_ctime` (inode change time) would also pass that, so the
    # test below is what rules the wrong-field mistake out.
    assert abs((datetime.now().astimezone() - info.file.created).total_seconds()) < 60


def test_creation_time_is_none_rather_than_a_wrong_value_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`st_ctime` is the inode *change* time, not a creation time. Where
    no real creation time can be read, this must come back as
    "unknown" rather than quietly showing something else - so the
    unavailable case is forced here rather than waiting for a
    filesystem that happens to exhibit it."""
    monkeypatch.setattr("core.document_info.birth_time", lambda *args, **kwargs: None)
    source = _letter(tmp_path / "src.pdf")

    info = read_document_info(source, source_path=source)

    assert info.file.created is None
    # The rest of the section is unaffected - one unavailable field
    # must not blank out the others.
    assert info.file.modified is not None
    assert info.file.size_bytes is not None


# --- page geometry -------------------------------------------------------------


def test_uniform_page_geometry(tmp_path: Path) -> None:
    info = read_document_info(_letter(tmp_path / "letter.pdf", 5))
    assert info.geometry is not None
    assert info.geometry.page_count == 5
    assert len(info.geometry.size_groups) == 1
    group = info.geometry.size_groups[0]
    assert (group.width_pt, group.height_pt) == (612.0, 792.0)
    assert group.page_count == 5
    assert group.orientation == "portrait"
    assert round(group.width_mm, 1) == 215.9
    assert round(group.height_in, 2) == 11.0
    assert info.geometry.unreadable_pages == 0


def test_mixed_page_sizes_are_reported_as_mixed_with_the_dominant_size_first(
    tmp_path: Path,
) -> None:
    path = _make_pdf(tmp_path / "mixed.pdf", [(612, 792), (842, 595), (612, 792), (200, 200)])
    info = read_document_info(path)
    assert info.geometry is not None
    assert [(g.width_pt, g.height_pt, g.page_count) for g in info.geometry.size_groups] == [
        (612.0, 792.0, 2),
        (842.0, 595.0, 1),
        (200.0, 200.0, 1),
    ]
    assert [g.orientation for g in info.geometry.size_groups] == [
        "portrait",
        "landscape",
        "square",
    ]


def test_sub_point_size_noise_does_not_split_one_real_page_size(tmp_path: Path) -> None:
    # Real producers emit A4 as both 595.276x841.89 and 595.28x841.89.
    path = _make_pdf(tmp_path / "a4.pdf", [(595.276, 841.89), (595.28, 841.89)])
    info = read_document_info(path)
    assert info.geometry is not None
    assert len(info.geometry.size_groups) == 1
    assert info.geometry.size_groups[0].page_count == 2


def test_rotation_is_reported_per_distinct_angle(tmp_path: Path) -> None:
    path = _letter(tmp_path / "rot.pdf", 3)
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[1].Rotate = 90
        pdf.pages[2].Rotate = 90
        pdf.save(path)

    info = read_document_info(path)
    assert info.geometry is not None
    assert [(g.degrees, g.page_count) for g in info.geometry.rotation_groups] == [(90, 2), (0, 1)]


def test_out_of_range_rotation_is_normalised(tmp_path: Path) -> None:
    path = _letter(tmp_path / "rot450.pdf")
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Rotate = 450
        pdf.save(path)

    info = read_document_info(path)
    assert info.geometry is not None
    assert info.geometry.rotation_groups[0].degrees == 90


@pytest.mark.parametrize(
    "media_box",
    [b"", b"/MediaBox[0 0 (wide) 792]", b"/MediaBox[0 0 612]", b"/MediaBox/Letter"],
)
def test_a_missing_or_malformed_media_box_still_yields_a_size(
    tmp_path: Path, media_box: bytes
) -> None:
    """qpdf repairs a missing or garbage /MediaBox to a default letter
    page while opening the file, so `Page.mediabox` never actually
    raises for anything pikepdf can open at all - verified by hand
    across all four shapes below (a page with the key deleted and
    re-saved gets one written back, too). The guard in `_page_size`
    stays as belt-and-braces, but this is what a real reader sees, and
    pinning it here means a future pikepdf that stops repairing shows
    up as a failure rather than as a silently wrong page size.
    """
    path = tmp_path / "badbox.pdf"
    path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R" + media_box + b">>endobj\n"
        b"trailer<</Root 1 0 R/Size 4>>\n"
        b"%%EOF\n"
    )

    info = read_document_info(path)
    assert info.geometry is not None
    assert info.geometry.page_count == 1
    assert info.geometry.unreadable_pages == 0
    assert len(info.geometry.size_groups) == 1


def test_a_non_numeric_rotation_is_counted_not_fatal(tmp_path: Path) -> None:
    path = _letter(tmp_path / "badrot.pdf")
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj["/Rotate"] = pikepdf.String("sideways")
        pdf.save(path)

    info = read_document_info(path)
    assert info.geometry is not None
    assert info.geometry.page_count == 1
    assert info.geometry.unreadable_pages == 1
    assert info.geometry.rotation_groups == ()


# --- PDF technical --------------------------------------------------------------


def test_plain_document_technical_flags(tmp_path: Path) -> None:
    info = read_document_info(_letter(tmp_path / "plain.pdf"))
    assert info.technical.pdf_version is not None
    assert info.technical.linearized is False
    assert info.technical.tagged is False
    assert info.technical.encrypted is False
    assert info.technical.permissions is None


def test_linearized_document_is_reported_as_such(tmp_path: Path) -> None:
    source = _letter(tmp_path / "src.pdf", 2)
    linearized = tmp_path / "linear.pdf"
    with pikepdf.Pdf.open(source) as pdf:
        pdf.save(linearized, linearize=True)

    assert read_document_info(linearized).technical.linearized is True
    assert read_document_info(source).technical.linearized is False


def test_tagged_document_is_reported_as_such(tmp_path: Path) -> None:
    path = _letter(tmp_path / "tagged.pdf")
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        pdf.Root.StructTreeRoot = pdf.make_indirect(
            pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot)
        )
        pdf.save(path)

    assert read_document_info(path).technical.tagged is True


def test_marked_without_a_structure_tree_is_not_reported_as_tagged(tmp_path: Path) -> None:
    path = _letter(tmp_path / "claims_tagged.pdf")
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        pdf.save(path)

    assert read_document_info(path).technical.tagged is False


def test_catalog_version_overrides_the_header_version(tmp_path: Path) -> None:
    """A file incrementally updated to a later version keeps its old
    `%PDF-x.y` header; pikepdf's `pdf_version` reports only that
    header, so the catalog's /Version has to win."""
    path = _letter(tmp_path / "updated.pdf")
    with pikepdf.Pdf.open(path, allow_overwriting_input=True) as pdf:
        header_version = str(pdf.pdf_version)
        pdf.Root.Version = pikepdf.Name("/1.7")
        pdf.save(path)

    assert header_version != "1.7"  # the fixture only means something if these differ
    assert read_document_info(path).technical.pdf_version == "1.7"


def test_encrypted_document_reports_its_real_permission_flags(tmp_path: Path) -> None:
    source = _letter(tmp_path / "src.pdf", 2)
    encrypted = tmp_path / "encrypted.pdf"
    with pikepdf.Pdf.open(source) as pdf:
        pdf.save(
            encrypted,
            encryption=pikepdf.Encryption(
                owner="owner",
                user="",  # openable without a password, permissions still enforced
                allow=pikepdf.Permissions(
                    extract=False,
                    modify_annotation=False,
                    modify_other=False,
                    print_lowres=True,
                    print_highres=False,
                ),
            ),
        )

    info = read_document_info(encrypted)
    assert info.technical.encrypted is True
    permissions = info.technical.permissions
    assert permissions is not None
    assert permissions.printing is True
    assert permissions.high_quality_printing is False
    assert permissions.copying is False
    assert permissions.modifying is False
    assert permissions.annotating is False
    # The rest of the report is still fully available.
    assert info.geometry is not None
    assert info.geometry.page_count == 2


def test_a_password_protected_document_degrades_instead_of_raising(tmp_path: Path) -> None:
    """Reachable for real: ProtectOperation requires a non-empty user
    password, so the working copy of a just-protected document cannot
    be reopened. pikepdf's PasswordError is not a PdfError subclass,
    which is exactly how this used to escape a handler."""
    source = _letter(tmp_path / "src.pdf")
    protected = tmp_path / "protected.pdf"
    with pikepdf.Pdf.open(source) as pdf:
        pdf.save(protected, encryption=pikepdf.Encryption(owner="o", user="secret"))

    info = read_document_info(protected, source_path=protected)

    assert info.password_protected is True
    assert info.read_error is not None
    assert info.technical.encrypted is True
    assert info.technical.permissions is None
    assert info.metadata is None
    assert info.geometry is None
    # The on-disk facts need no parsing, so they survive.
    assert info.file.size_bytes == protected.stat().st_size


def test_a_structurally_broken_document_degrades_instead_of_raising(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is definitely not a PDF")

    info = read_document_info(broken, source_path=broken)

    assert info.read_error is not None
    assert info.password_protected is False
    assert info.metadata is None
    assert info.geometry is None
    assert info.file.size_bytes == len(b"this is definitely not a PDF")


def test_no_open_document_reports_an_error_rather_than_raising() -> None:
    info = read_document_info(None)
    assert info.read_error is not None
    assert info.metadata is None
    assert info.file.path is None


# --- formatting helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [(0, "0 B"), (512, "512 B"), (1024, "1.0 KB"), (1536, "1.5 KB"), (5 * 1024**2, "5.0 MB")],
)
def test_human_readable_size(size_bytes: int, expected: str) -> None:
    assert human_readable_size(size_bytes) == expected


def test_unit_conversions() -> None:
    assert round(points_to_mm(612), 1) == 215.9
    assert points_to_inches(792) == 11.0
