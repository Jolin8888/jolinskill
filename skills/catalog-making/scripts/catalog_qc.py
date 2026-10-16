#!/usr/bin/env python3
"""Portable PDF catalogue structural checks. This is not visual approval."""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def outside(box, bounds, tolerance=0.75):
    return (box[0] < bounds[0] - tolerance or box[1] < bounds[1] - tolerance
            or box[2] > bounds[2] + tolerance or box[3] > bounds[3] + tolerance)


def valid_uri(uri):
    if not isinstance(uri, str) or any(c.isspace() for c in uri):
        return False
    p = urlparse(uri)
    if p.scheme in ("http", "https"):
        return bool(p.netloc and p.hostname)
    if p.scheme == "mailto":
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", p.path))
    if p.scheme == "tel":
        return bool(re.fullmatch(r"[+0-9().-]+", p.path))
    return False


def run(args, pdf):
    required = {int(n.strip()) for n in args.required_changed_pages.split(",") if n.strip()}
    allowed = {int(n.strip()) for n in args.allowed_changed_pages.split(",") if n.strip()}
    if required and not args.baseline:
        raise ValueError("required-changed-pages requires --baseline")
    if not required.issubset(allowed):
        raise ValueError("Every required-changed-pages entry must also be in allowed-changed-pages")
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")
    result = {"status": "PASS", "pdf": str(Path(args.pdf).resolve()),
              "sha256": digest(args.pdf), "errors": [], "warnings": [],
              "checks": {}, "manual_review": {
                  "visual_layout": "REQUIRED", "clipping_paths": "REQUIRED",
                  "source_pixel_authenticity": "NOT_VERIFIED",
                  "note": "Image bounds and equal scale cannot prove absence of clipping, masking, pre-cropping, recoloring or regenerated content. Review rendered pages and source pixels separately."}}
    errors, warnings, checks = result["errors"], result["warnings"], result["checks"]
    with pdf.open(args.pdf) as doc:
        if doc.needs_pass:
            raise ValueError("Encrypted PDF requires a password; no checks performed")
        checks["page_count"] = len(doc)
        expected = manifest.get("expected_pages")
        if expected is not None:
            if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
                raise ValueError("expected_pages must be a positive integer")
            if expected != len(doc):
                errors.append({"check": "page_count", "expected": expected, "actual": len(doc)})
        texts = [p.get_text() for p in doc]
        all_text = "\n".join(texts)
        counts = {}
        declared = []
        for category in ("product_codes", "oem_codes"):
            codes = manifest.get(category, [])
            if not isinstance(codes, list) or any(not isinstance(c, str) or not c for c in codes):
                raise ValueError(category + " must be a list of nonempty strings")
            for code in codes:
                if code in declared:
                    errors.append({"check": "manifest_duplicate_code", "code": code})
                declared.append(code)
                # Hyphens are SKU characters: ABC-1 must not match ABC-1-ALT.
                pattern = r"(?<![\w-])" + re.escape(code) + r"(?![\w-])"
                count = len(re.findall(pattern, all_text))
                counts[code] = count
                if count != 1:
                    errors.append({"check": "code_occurrence", "code": code, "expected": 1, "actual": count})
        checks["code_counts"] = counts
        checks["codes_checked"] = len(declared)
        if not declared:
            warnings.append({"check": "inventory", "message": "No product or OEM codes declared; inventory completeness was not verified"})
        allowed_urls = manifest.get("allowed_urls")
        if allowed_urls is not None and (not isinstance(allowed_urls, list) or any(not isinstance(u, str) for u in allowed_urls)):
            raise ValueError("allowed_urls must be an array of exact URI strings")
        images_by_page, urls, font_records = {}, [], {}
        text_overflow = []
        for page_index, page in enumerate(doc, 1):
            media = tuple(page.mediabox * page.transformation_matrix)
            image_rows = []
            for image in page.get_image_info():
                bbox = image["bbox"]
                a, b, c, d, _, _ = image["transform"]
                iw, ih = image["width"], image["height"]
                sx, sy = math.hypot(a, b) / iw, math.hypot(c, d) / ih
                relative_error = abs(sx - sy) / max(sx, sy, 1e-12)
                shear = abs(a * c + b * d) / max(math.hypot(a, b) * math.hypot(c, d), 1e-12)
                row = {"bbox": list(bbox), "pixel_size": [iw, ih],
                       "scale_error": relative_error, "shear": shear}
                image_rows.append(row)
                if outside(bbox, media):
                    errors.append({"check": "image_outside_mediabox", "page": page_index, **row})
                if relative_error > 0.005 or shear > 0.005 or min(sx, sy) <= 0:
                    errors.append({"check": "image_distortion", "page": page_index, **row})
            images_by_page[page_index] = image_rows
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if span["text"].strip() and outside(span["bbox"], media):
                            text_overflow.append({"page": page_index, "text": span["text"][:120], "bbox": list(span["bbox"])})
            for link in page.get_links():
                uri = link.get("uri")
                if uri is not None:
                    urls.append({"page": page_index, "uri": uri})
                    if not valid_uri(uri):
                        errors.append({"check": "invalid_uri", "page": page_index, "uri": uri})
                    if allowed_urls is not None and uri not in allowed_urls:
                        errors.append({"check": "uri_not_allowed", "page": page_index, "uri": uri})
            for font in page.get_fonts(full=True):
                xref, extension, kind, name = font[:4]
                if xref in font_records:
                    continue
                try:
                    embedded = bool(doc.extract_font(xref)[3]) if xref else False
                except Exception:
                    embedded = False
                font_records[xref] = {"xref": xref, "name": name, "kind": kind,
                                      "extension": extension, "embedded_program": embedded}
        checks["images"] = images_by_page
        checks["image_count"] = sum(len(v) for v in images_by_page.values())
        checks["urls"] = urls
        checks["url_allowlist_enforced"] = allowed_urls is not None
        checks["fonts"] = list(font_records.values())
        checks["text_overflow"] = text_overflow
        if text_overflow:
            warnings.append({"check": "text_bounds", "count": len(text_overflow), "message": "Review overflows visually; font ascenders can cause false positives"})
        unembedded = [f["name"] for f in font_records.values() if not f["embedded_program"]]
        if unembedded:
            warnings.append({"check": "font_embedding", "fonts": unembedded,
                             "message": "Resource-level report; unused resources and Type 3 fonts may not have an extractable font program. Review actual used text."})
        checks["expected_images_matched"] = 0
        used = set()
        for item in manifest.get("expected_images", []):
            page_no, bbox, size = item["page"], item["bbox"], item["source_pixel_size"]
            if (isinstance(page_no, bool) or not isinstance(page_no, int) or len(bbox) != 4 or len(size) != 2
                    or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in [*bbox, *size])
                    or min(size) <= 0 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]):
                raise ValueError("expected_images requires 1-based page, positive source_pixel_size [w,h] and bbox [x0,y0,x1,y1]")
            ratio = ((bbox[2] - bbox[0]) / (bbox[3] - bbox[1])) / (size[0] / size[1])
            if abs(ratio - 1) > 0.005:
                errors.append({"check": "expected_image_aspect", "image": item})
            match = next((i for i, img in enumerate(images_by_page.get(page_no, []))
                          if (page_no, i) not in used and max(abs(a - b) for a, b in zip(img["bbox"], bbox)) <= 0.75), None)
            if match is None:
                errors.append({"check": "expected_image_missing", "image": item})
            else:
                used.add((page_no, match))
                checks["expected_images_matched"] += 1
        if args.baseline:
            if any(n < 1 or n > len(doc) for n in allowed):
                raise ValueError("allowed-changed-pages contains an out-of-range page")
            comparison = {"baseline": str(Path(args.baseline).resolve()), "sha256": digest(args.baseline),
                          "allowed_changed_pages": sorted(allowed), "required_changed_pages": sorted(required),
                          "changed_pages": [], "render_max_side": 600}
            with pdf.open(args.baseline) as baseline:
                if baseline.needs_pass:
                    raise ValueError("Encrypted baseline cannot be compared")
                if len(baseline) != len(doc):
                    errors.append({"check": "baseline_page_count", "baseline": len(baseline), "current": len(doc),
                                   "message": "Page mapping is required for inserted/deleted pages; same-index comparison was not performed"})
                    comparison["status"] = "NOT_COMPARED_PAGE_MAPPING_REQUIRED"
                else:
                    for index, page in enumerate(doc):
                        old = baseline[index]
                        scale = 600 / max(page.rect.width, page.rect.height)
                        scale_old = 600 / max(old.rect.width, old.rect.height)
                        new_pix = page.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csRGB, alpha=False)
                        old_pix = old.get_pixmap(matrix=pdf.Matrix(scale_old, scale_old), colorspace=pdf.csRGB, alpha=False)
                        changed = (tuple(page.rect) != tuple(old.rect) or new_pix.width != old_pix.width
                                   or new_pix.height != old_pix.height or new_pix.samples != old_pix.samples)
                        if changed:
                            comparison["changed_pages"].append(index + 1)
                            if index + 1 not in allowed:
                                errors.append({"check": "unexpected_changed_page", "page": index + 1})
                    comparison["status"] = "COMPARED"
                    for page_no in sorted(required - set(comparison["changed_pages"])):
                        errors.append({"check": "required_page_unchanged", "page": page_no})
            checks["baseline"] = comparison
    result["status"] = "FAIL" if errors else "PASS"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--allowed-changed-pages", default="")
    parser.add_argument("--required-changed-pages", default="")
    args = parser.parse_args()
    try:
        try:
            import pymupdf as pdf
        except ImportError:
            try:
                import fitz as pdf
                if not hasattr(pdf, "open") or not hasattr(pdf, "Matrix"):
                    raise ImportError("The installed fitz module is not PyMuPDF")
            except ImportError as exc:
                raise RuntimeError("PyMuPDF is required. Use the configured document Python runtime or install PyMuPDF in an approved environment.") from exc
        result = run(args, pdf)
    except Exception as exc:
        result = {"status": "FAIL", "errors": [{"check": "execution", "message": str(exc)}], "warnings": []}
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "errors": len(result["errors"]),
                      "warnings": len(result["warnings"]), "report": str(output)}, ensure_ascii=False))
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
