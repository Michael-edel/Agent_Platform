from __future__ import annotations

from pathlib import Path


def _build_demo_invoice_pdf_bytes() -> bytes:
    # Minimal single-page PDF with built-in Helvetica (Type1).
    # Deterministic output: fixed text, fixed coordinates, fixed date string.
    lines = [
        "Demo Invoice",
        "Supplier: Demo Supplier",
        "Buyer: Demo Buyer",
        "Amount: 1000 KZT",
        "Date: 2026-01-01",
    ]

    # Build a simple content stream (PDF text operators).
    # NOTE: We intentionally avoid non-ASCII and special chars here.
    stream_parts: list[bytes] = []
    stream_parts.append(b"BT\n")
    stream_parts.append(b"/F1 24 Tf\n")
    stream_parts.append(b"72 720 Td\n")
    for idx, line in enumerate(lines):
        if idx > 0:
            stream_parts.append(b"0 -28 Td\n")
        # Parentheses are reserved in PDF string syntax; our lines avoid them.
        stream_parts.append(b"(" + line.encode("ascii") + b") Tj\n")
    stream_parts.append(b"ET\n")
    stream = b"".join(stream_parts)

    # PDF objects. We'll compute xref offsets after concatenation.
    objects: list[bytes] = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objects.append(
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        b"   /Resources << /Font << /F1 5 0 R >> >>\n"
        b"   /Contents 4 0 R >>\n"
        b"endobj\n"
    )
    objects.append(
        b"4 0 obj\n"
        + f"<< /Length {len(stream)} >>\n".encode("ascii")
        + b"stream\n"
        + stream
        + b"endstream\nendobj\n"
    )
    objects.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"  # binary marker line (standard practice)

    body_parts: list[bytes] = [header]
    offsets: list[int] = [0]  # xref entry 0 is the free object
    cur = len(header)

    for obj in objects:
        offsets.append(cur)
        body_parts.append(obj)
        cur += len(obj)

    xref_start = cur

    # xref table
    xref_lines: list[bytes] = []
    xref_lines.append(b"xref\n")
    xref_lines.append(f"0 {len(offsets)}\n".encode("ascii"))
    xref_lines.append(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        xref_lines.append(f"{off:010d} 00000 n \n".encode("ascii"))

    trailer = (
        b"trailer\n"
        + f"<< /Size {len(offsets)} /Root 1 0 R >>\n".encode("ascii")
        + b"startxref\n"
        + f"{xref_start}\n".encode("ascii")
        + b"%%EOF\n"
    )

    return b"".join(body_parts + xref_lines + [trailer])


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    demo_dir = repo_root / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)

    out_path = demo_dir / "demo-invoice.pdf"
    out_path.write_bytes(_build_demo_invoice_pdf_bytes())
    print(f"OK: перезаписан файл: {out_path}")


if __name__ == "__main__":
    main()

