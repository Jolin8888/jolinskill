#!/usr/bin/env python3
"""Verify protected product pixels in an unscaled transparent cutout master."""
import argparse
import json
import sys
from pathlib import Path


def run(args):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required; use the configured image Python runtime or an approved environment") from exc
    report = {"status": "PASS", "errors": [], "warnings": [], "checks": {},
              "manual_review": {"mask_reliability": "NOT_VERIFIED", "silhouette_and_edges": "REQUIRED",
                                "note": "Only pixels selected by the supplied mask are tested. A passing result cannot establish that the mask protects every product pixel. Review edges and coverage visually."}}
    errors, checks = report["errors"], report["checks"]
    with Image.open(args.source) as source, Image.open(args.cutout) as cutout, Image.open(args.protect_mask) as mask:
        checks["source_size"] = list(source.size)
        checks["cutout_size"] = list(cutout.size)
        checks["mask_size"] = list(mask.size)
        if source.size != cutout.size or source.size != mask.size:
            errors.append({"check": "master_dimensions", "message": "Source, cutout and mask must have identical unscaled dimensions"})
        if "A" not in cutout.getbands() and "transparency" not in cutout.info:
            errors.append({"check": "cutout_alpha", "message": "Cutout must contain an alpha channel or explicit transparency"})
        # Explicit alpha defines protected pixels; grayscale masks use intensity.
        if "A" in mask.getbands() or "transparency" in mask.info:
            protect = mask.convert("RGBA").getchannel("A")
            checks["mask_selection"] = "alpha > 0"
        elif mask.mode in ("1", "L"):
            protect = mask.convert("L")
            checks["mask_selection"] = "grayscale > 0"
        else:
            errors.append({"check": "mask_format", "message": "Use an alpha mask or a grayscale mask, not an ambiguous RGB image"})
            protect = None
        if not errors:
            selected = protect.point(lambda p: 255 if p > 0 else 0)
            count = selected.histogram()[255]
            checks["protected_pixel_count"] = count
            if count == 0:
                errors.append({"check": "empty_protection", "message": "Protect mask must select a nonempty product interior"})
            else:
                src_rgb, dst_rgba = source.convert("RGB"), cutout.convert("RGBA")
                rgb_changes = alpha_changes = 0
                for src, dst, active in zip(src_rgb.getdata(), dst_rgba.getdata(), selected.getdata()):
                    if active:
                        rgb_changes += src != dst[:3]
                        alpha_changes += dst[3] != 255
                checks["protected_rgb_changed"] = rgb_changes
                checks["protected_alpha_not_opaque"] = alpha_changes
                if rgb_changes:
                    errors.append({"check": "protected_rgb_changed", "pixels": rgb_changes})
                if alpha_changes:
                    errors.append({"check": "protected_alpha_not_opaque", "pixels": alpha_changes})
    report["status"] = "FAIL" if errors else "PASS"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--cutout", required=True)
    parser.add_argument("--protect-mask", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as exc:
        result = {"status": "FAIL", "errors": [{"check": "execution", "message": str(exc)}], "warnings": []}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "errors": len(result["errors"]), "report": str(out)}, ensure_ascii=False))
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
