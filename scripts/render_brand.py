"""Render project branding from the Quinty art and brand.json; optionally scale the art and export PNGs.

Quinty the starfish (five arms: a *quint*) has three poses, each a 1254 px transparent PNG in
``docs/assets/quinty/``:

- ``inspector``: examines a contract under a magnifier. The avatar, icon, mark, README hero, social card.
- ``chef``: stamps approval. The pull-request banner when every contract passed.
- ``explorer``: traces the path where the code left the model. The banner when a contract failed.

``--png`` (needs Pillow and resvg-py) scales the poses into ``docs/assets/quinty/*-SIZE.png`` and exports
the raster files. Rendering itself only embeds those committed bytes, so ``--check`` runs on plain Python.

    uv run --no-project --with resvg-py==0.2.6 --with pillow==12.0.0 python scripts/render_brand.py --png
    python scripts/render_brand.py --check
"""

import argparse
import base64
import json
import re
import struct
from hashlib import sha256
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"
QUINTY = ASSETS / "quinty"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
PURPLE, PURPLE_LIGHT, LAVENDER, MINT, CORAL = "#6E56CF", "#C4B5FD", "#F1ECFE", "#86EFAC", "#F87171"
POSES = ("inspector", "chef", "explorer")
# Every scaled copy the SVGs embed or the README shows: (pose, height in pixels).
SIZES = [(pose, 168) for pose in POSES] + [(pose, 320) for pose in POSES] + [("inspector", 440), ("inspector", 560)]
# Quinty's face and hat, without the clipboard: what still reads at favicon size.
FACE = (40, 90, 900, 950)
RAW = "https://raw.githubusercontent.com/gigaverse-app/{repository}/main/docs/assets/{name}"


def digest(path):
    # dry-lint: distinct-from src/pytest_quint_connect/contract.py::Contract.fingerprints
    # Brand freshness hashes text with normalized line endings, so a CRLF checkout is not stale; the plugin's
    # receipt fingerprints exact bytes. This script also runs with --no-project, without the package installed.
    content = path.read_bytes() if path.suffix == ".png" else path.read_text(encoding="utf-8").encode("utf-8")
    return sha256(content).hexdigest()


def art(pose: str, height: int) -> tuple[str, int]:
    """A scaled pose as a PNG data URI, and its width, from the committed copy (no image library needed)."""
    data = (QUINTY / f"{pose}-{height}.png").read_bytes()
    width, real = struct.unpack(">II", data[16:24])
    assert real == height, f"{pose}-{height}.png is {real} px tall; rerun with --png"
    return "data:image/png;base64," + base64.b64encode(data).decode(), width


def motif(failed: bool = False, scale: float = 1.0) -> str:
    """A replayed trace: model states in a row, each matched by the code below it; a failure breaks the last."""
    parts = [f'<path d="M12 14h68" stroke="{PURPLE}" stroke-width="2" opacity="0.7"/>']
    for step in range(5):
        x = 12 + step * 17
        broken = failed and step == 4
        parts.append(f'<circle cx="{x}" cy="14" r="5" fill="{PURPLE_LIGHT}"/>')
        parts.append(f'<path d="M{x} 21v8" stroke="{PURPLE}" stroke-width="1.6" stroke-dasharray="2 2"/>')
        parts.append(f'<rect x="{x - 5}" y="32" width="10" height="10" rx="2" fill="{CORAL if broken else MINT}"/>')
        if broken:
            parts.append(f'<path d="M{x - 2.5} 34.5l5 5m0 -5l-5 5" stroke="#FFFFFF" stroke-width="1.6"/>')
    return f'<g transform="scale({scale})">{"".join(parts)}</g>'


def palette(dark: bool) -> tuple[str, str, str, str]:
    """Ground, edge, ink and muted text, matching GitHub's own light and dark themes."""
    return ("#0D1117", "#30363D", "#F0F6FC", "#9198A1") if dark else ("#FFFFFF", "#D1D9E0", "#1F2328", "#59636E")


def wordmark(dark: bool) -> str:
    return f'pytest<tspan fill="{PURPLE_LIGHT if dark else PURPLE}">-quint-connect</tspan>'


STICKY = {
    "": ("inspector", "contract report", "Quint histories, replayed on this pull request", False),
    "-passed": ("chef", "every history matched the model", "Quint histories, replayed on this pull request", False),
    "-failed": ("explorer", "the code left the model's path", "reproduce it with the commands below", True),
}


def sticky_banner(variant: str, dark: bool) -> str:
    """A pull-request comment's full-width banner. Adopters hot-link these from a released revision."""
    pose, first, second, failed = STICKY[variant]
    ground, edge, ink, muted = palette(dark)
    # Tall enough that Quinty reads at a narrow comment's width, where the banner scales down to fit.
    uri, width = art(pose, 320)
    shown = 88 * width / 320
    glow = 0.22 if dark else 0.14
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="96" viewBox="0 0 640 96" role="img" aria-label="pytest-quint-connect: {first}">
<title>pytest-quint-connect: {first}</title>
<defs><linearGradient id="sticky-glow" x1="0" y1="0" x2="1" y2="0">
<stop stop-color="{PURPLE}" stop-opacity="{glow}"/><stop offset="0.55" stop-color="{PURPLE}" stop-opacity="0"/>
</linearGradient></defs>
<rect x="0.5" y="0.5" width="639" height="95" rx="12" fill="{ground}" stroke="{edge}"/>
<rect x="0.5" y="0.5" width="639" height="95" rx="12" fill="url(#sticky-glow)"/>
<image href="{uri}" x="8" y="4" width="{shown:.1f}" height="88"/>
<text x="{20 + shown:.0f}" y="44" font-family="{FONT}" font-size="25" font-weight="800" letter-spacing="-0.5" fill="{ink}">{wordmark(dark)}</text>
<text x="{20 + shown:.0f}" y="68" font-family="{FONT}" font-size="14" font-weight="700" fill="{CORAL if failed else ink}">{escape(first)}</text>
<text x="{20 + shown:.0f}" y="86" font-family="{FONT}" font-size="12" fill="{muted}">{escape(second)}</text>
<g transform="translate(528 20) scale(1.05)">{motif(failed)}</g>
</svg>
"""


# The PR comment's scoreboard: large colored digits, composed by report.py from these fixed glyphs (the way
# pytest-obligation composes its report-glyphs), so a comment needs no image service and no per-run files.
# Adopters' comments hot-link them from a released revision: never rename or move them.
SCORE_COLORS = {
    "good": ("#1A7F37", "#3FB950"),
    "bad": ("#CF222E", "#F85149"),
    "warning": ("#9A6700", "#D29922"),
    "neutral": ("#1F2328", "#F0F6FC"),
}


def glyph(char: str, color: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="36" viewBox="0 0 18 36" '
        f'font-family="{FONT}"><text x="9" y="28" text-anchor="middle" font-size="28" font-weight="700" '
        f'fill="{color}">{char}</text></svg>\n'
    )


def score_glyphs() -> dict[str, str]:
    files = {}
    for name, (light, dark) in SCORE_COLORS.items():
        for theme, color in (("light", light), ("dark", dark)):
            for char, slug in [*((str(d), str(d)) for d in range(10)), ("/", "slash")]:
                files[f"docs/assets/score/{name}-{slug}-{theme}.svg"] = glyph(char, color)
    return files


def hero(dark: bool, brand: dict[str, str]) -> str:
    """The README's full-width banner: Quinty inspecting a contract, the name, and a replayed trace."""
    ground, edge, ink, muted = palette(dark)
    bottom, glow = ("#1A1030", 0.30) if dark else ("#F3ECFD", 0.55)
    halo = PURPLE if dark else PURPLE_LIGHT
    uri, width = art("inspector", 560)
    shown = 280 * width / 560
    first, second = (escape(line) for line in brand["banner_lines"])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="320" viewBox="0 0 1280 320" role="img" aria-label="pytest-quint-connect: {escape(brand["tagline"])}">
<title>pytest-quint-connect: {escape(brand["tagline"])}</title>
<defs>
<linearGradient id="hero-ground" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{ground}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>
<radialGradient id="hero-halo"><stop offset="0" stop-color="{halo}" stop-opacity="{glow}"/><stop offset="1" stop-color="{halo}" stop-opacity="0"/></radialGradient>
</defs>
<rect x="1" y="1" width="1278" height="318" rx="22" fill="url(#hero-ground)" stroke="{edge}" stroke-width="2"/>
<circle cx="180" cy="166" r="168" fill="url(#hero-halo)"/>
<image href="{uri}" x="{180 - shown / 2:.1f}" y="22" width="{shown:.1f}" height="280"/>
<text x="350" y="146" font-family="{FONT}" font-size="66" font-weight="800" letter-spacing="-1.5" fill="{ink}">{wordmark(dark)}</text>
<text x="354" y="200" font-family="{FONT}" font-size="28" font-weight="600" fill="{ink}">{first}</text>
<text x="354" y="240" font-family="{FONT}" font-size="28" font-weight="600" fill="{PURPLE_LIGHT if dark else PURPLE}">{second}</text>
<text x="354" y="280" font-family="{FONT}" font-size="18" fill="{muted}">{escape(brand["tagline"])}</text>
<g transform="translate(1040 108)">{motif(scale=2.0)}</g>
</svg>
"""


def mark() -> str:
    """The 64 px mark: Quinty on a lavender tile, for the documentation header."""
    uri, width = art("face", 168)
    shown = 58 * width / 168
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64" role="img" aria-label="pytest-quint-connect">
<title>pytest-quint-connect</title>
<rect width="64" height="64" rx="15" fill="{LAVENDER}"/>
<image href="{uri}" x="{(64 - shown) / 2:.1f}" y="3" width="{shown:.1f}" height="58"/>
</svg>
"""


def banner(brand: dict[str, str]) -> str:
    """The documentation homepage's compact dark banner."""
    uri, width = art("inspector", 168)
    shown = 56 * width / 168
    first, second = (escape(line) for line in brand["banner_lines"])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="780" height="64" viewBox="0 0 780 64" role="img" aria-label="pytest-quint-connect: {escape(brand["tagline"])}">
<title>pytest-quint-connect: {escape(brand["tagline"])}</title>
<rect x="0.5" y="0.5" width="779" height="63" rx="10" fill="#0D1117" stroke="#30363D"/>
<image href="{uri}" x="8" y="4" width="{shown:.1f}" height="56"/>
<text x="{18 + shown:.0f}" y="41" font-family="{FONT}" font-size="26" font-weight="800" letter-spacing="-0.5" fill="#F0F6FC">{wordmark(True)}</text>
<text x="{318 + shown:.0f}" y="27" font-family="{FONT}" font-size="12" font-weight="600" fill="#C9D1D9">{first}</text>
<text x="{318 + shown:.0f}" y="45" font-family="{FONT}" font-size="12" fill="#9CA8B7">{second}</text>
<g transform="translate(676 1)">{motif()}</g>
</svg>
"""


def social(brand: dict[str, str]) -> str:
    """The 1280 x 640 social card."""
    uri, width = art("inspector", 440)
    first, second = (escape(line) for line in brand["banner_lines"])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640" role="img" aria-label="pytest-quint-connect: {escape(brand["tagline"])}">
<title>pytest-quint-connect: {escape(brand["tagline"])}</title>
<defs><radialGradient id="social-halo"><stop offset="0" stop-color="{PURPLE}" stop-opacity="0.35"/><stop offset="1" stop-color="{PURPLE}" stop-opacity="0"/></radialGradient></defs>
<rect width="1280" height="640" fill="#0D1117"/>
<circle cx="300" cy="330" r="270" fill="url(#social-halo)"/>
<image href="{uri}" x="{300 - width / 2:.1f}" y="110" width="{width}" height="440"/>
<text x="600" y="250" font-family="{FONT}" font-size="58" font-weight="800" letter-spacing="-1.5" fill="#F0F6FC">{wordmark(True)}</text>
<text x="604" y="320" font-family="{FONT}" font-size="30" font-weight="600" fill="#F0F6FC">{first}</text>
<text x="604" y="366" font-family="{FONT}" font-size="30" font-weight="600" fill="{MINT}">{second}</text>
<g transform="translate(604 420)">{motif(scale=2.6)}</g>
<text x="604" y="590" font-family="{FONT}" font-size="22" fill="#8B949E">github.com/gigaverse-app/{brand["repository"]}</text>
</svg>
"""


def render() -> dict[str, str]:
    brand = json.loads((ASSETS / "brand.json").read_text(encoding="utf-8"))
    files = {
        "docs/assets/logo.svg": mark(),
        "docs/assets/banner.svg": banner(brand),
        "docs/assets/social-preview.svg": social(brand),
        "docs/assets/readme-hero-light.svg": hero(False, brand),
        "docs/assets/readme-hero-dark.svg": hero(True, brand),
    }
    # Adopters' pull-request comments hot-link these from a released revision: never rename or move them.
    for variant in STICKY:
        for theme, dark in (("light", False), ("dark", True)):
            files[f"docs/assets/sticky-banner{variant}-{theme}.svg"] = sticky_banner(variant, dark)
    files.update(score_glyphs())
    # The counter's sign embeds Quinty too; the Worker has no file system, so the art ships as a module.
    uri, _ = art("inspector", 320)
    files["counter/src/quinty.js"] = (
        "// Generated by scripts/render_brand.py from docs/assets/quinty/inspector-320.png; do not edit.\n"
        f'export const QUINTY = "{uri}";\n'
    )
    return files


def scale_art() -> None:
    """Scale each pose to every size the SVGs and README use, and draw the avatar and the favicon."""
    from PIL import Image  # optional export dependency, installed by the documented uv --with command

    for pose, height in SIZES:
        source = Image.open(QUINTY / f"{pose}.png").convert("RGBA")
        width = round(source.width * height / source.height)
        source.resize((width, height), Image.LANCZOS).save(QUINTY / f"{pose}-{height}.png", optimize=True)
    inspector = Image.open(QUINTY / "inspector.png").convert("RGBA")
    face = inspector.crop(FACE)
    face.resize((168, 168), Image.LANCZOS).save(QUINTY / "face-168.png", optimize=True)
    for name, size, source in (("avatar.png", 512, inspector), ("icon.png", 256, face)):
        scaled = source.resize((round(size * 0.88),) * 2, Image.LANCZOS)
        tile = Image.new("RGBA", (size, size), LAVENDER)
        tile.alpha_composite(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2))
        tile.convert("RGB").save(ASSETS / name, optimize=True)


RASTERS = (
    ("banner.svg", "banner.png", 1560, 128),
    ("social-preview.svg", "social-preview.png", 1280, 640),
    ("readme-hero-light.svg", "readme-hero-light.png", 2560, 640),
    ("readme-hero-dark.svg", "readme-hero-dark.png", 2560, 640),
)


def hashed() -> list[str]:
    """Every file whose content the committed rasters depend on, or that is a raster."""
    names = [f"quinty/{pose}.png" for pose in POSES] + [f"quinty/{pose}-{height}.png" for pose, height in SIZES]
    names.append("quinty/face-168.png")
    return names + ["brand.json", "avatar.png", "icon.png"] + [n for raster in RASTERS for n in raster[:2]]


def readme_urls(repository: str) -> dict[str, str]:
    """The README hero's absolute PNG URLs, each with its content hash so image caches refresh (and PyPI renders it)."""
    return {
        RAW.format(repository=repository, name=name): f"?v={digest(ASSETS / name)[:12]}"
        for name in ("readme-hero-light.png", "readme-hero-dark.png")
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--png", action="store_true", help="Scale the art and export PNGs (Pillow, resvg-py 0.2.6)")
    parser.add_argument("--check", action="store_true", help="Check generated files without writing")
    args = parser.parse_args()
    if args.png:
        scale_art()
    stale = []
    for name, content in render().items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        parser.exit(1, "Regenerate branding: " + ", ".join(stale) + "\n")
    if args.png:
        import resvg_py

        for source, target, width, height in RASTERS:
            (ASSETS / target).write_bytes(
                bytes(resvg_py.svg_to_bytes(svg_path=str(ASSETS / source), width=width, height=height))
            )
        hashes = {name: digest(ASSETS / name) for name in hashed()}
        (ASSETS / "raster-sources.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8", newline="\n")
    if args.check:
        hashes = json.loads((ASSETS / "raster-sources.json").read_text(encoding="utf-8"))
        for name in hashed():
            if hashes.get(name) != digest(ASSETS / name):
                parser.exit(1, "Regenerate PNG exports with --png: " + name + "\n")
    brand = json.loads((ASSETS / "brand.json").read_text(encoding="utf-8"))
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    for url, version in readme_urls(brand["repository"]).items():
        if args.png:
            text = re.sub(re.escape(url) + r"(?:\?v=[0-9a-f]+)?", url + version, text)
        elif args.check and url + version not in text:
            parser.exit(1, "Refresh the README hero with --png: " + url + "\n")
    if args.png:
        readme.write_text(text, encoding="utf-8", newline="\n")
    print("Brand assets verified" if args.check else "Brand assets rendered")


if __name__ == "__main__":
    main()
