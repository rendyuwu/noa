from __future__ import annotations

import html
import os
from argparse import ArgumentParser
from pathlib import Path


def main() -> int:
    parser = ArgumentParser(
        description="Generate an HTML screenshot gallery for a NOA smoke run.",
    )
    parser.add_argument(
        "artifacts_dir",
        help="Artifacts directory (contains shots/ and logs).",
    )
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    shots_dir = artifacts_dir / "shots"
    video_dir = artifacts_dir / "video"
    out_file = artifacts_dir / "index.html"

    if not artifacts_dir.is_dir():
        raise SystemExit(f"Artifacts dir not found: {artifacts_dir}")
    if not shots_dir.is_dir():
        raise SystemExit(f"Shots dir not found: {shots_dir}")

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    images = [
        p for p in shots_dir.iterdir() if p.is_file() and p.suffix.lower() in exts
    ]
    images.sort(key=lambda p: p.name)

    video_exts = {".webm", ".mp4"}
    videos: list[Path] = []
    if video_dir.is_dir():
        videos = [
            p
            for p in video_dir.iterdir()
            if p.is_file() and p.suffix.lower() in video_exts
        ]
        videos.sort(key=lambda p: p.name)

    def rel(path: Path) -> str:
        return html.escape(
            os.path.relpath(path, start=out_file.parent).replace(os.sep, "/")
        )

    def maybe_link(path: Path, label: str) -> str:
        if not path.exists():
            return ""
        return f'<a href="{rel(path)}">{html.escape(label)}</a>'

    links = [
        maybe_link(artifacts_dir / "console-errors.txt", "console-errors.txt"),
        maybe_link(artifacts_dir / "network-requests.txt", "network-requests.txt"),
        maybe_link(artifacts_dir / "api.log", "api.log"),
        maybe_link(artifacts_dir / "web.log", "web.log"),
        maybe_link(artifacts_dir / "failure.png", "failure.png"),
    ]
    links = [x for x in links if x]

    items_html: list[str] = []
    for img in images:
        name = html.escape(img.name)
        href = rel(img)
        items_html.append(
            "\n".join(
                [
                    '<article class="card">',
                    f'  <header class="card__header"><code>{name}</code></header>',
                    f'  <a class="card__imgLink" href="{href}">',
                    f'    <img class="card__img" src="{href}" alt="{name}" loading="lazy" />',
                    "  </a>",
                    "</article>",
                ]
            )
        )

    title = "NOA Smoke Screenshots"
    subtitle = html.escape(str(artifacts_dir))
    nav = "" if not links else " | ".join(links)
    count = f"{len(images)} screenshot(s)"

    videos_html = ""
    if videos:
        first = videos[0]
        first_href = rel(first)
        list_items = "\n".join(
            [
                f'<li><a href="{rel(v)}"><code>{html.escape(v.name)}</code></a></li>'
                for v in videos
            ]
        )
        videos_html = "\n".join(
            [
                '<section class="videos">',
                "  <h2>Screen recording</h2>",
                '  <p class="meta">Recorded post-login (if available).</p>',
                f'  <video class="video__player" controls src="{first_href}"></video>',
                '  <ul class="videos__list">',
                f"{list_items}",
                "  </ul>",
                "</section>",
            ]
        )

    css = """
:root { color-scheme: light; }
body {
  margin: 0;
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
  background: #0b1020;
  color: #e8ecff;
}
a { color: #9ad0ff; }
header.page {
  padding: 18px 18px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.06),
    rgba(255, 255, 255, 0)
  );
}
h1 { margin: 0 0 6px; font-size: 18px; letter-spacing: 0.2px; }
.meta { margin: 0; opacity: 0.85; font-size: 12px; }
.nav { margin-top: 8px; font-size: 12px; opacity: 0.9; }
 main { padding: 16px 16px 28px; }
 .videos {
   margin-bottom: 16px;
   padding: 14px;
   border: 1px solid rgba(255, 255, 255, 0.10);
   border-radius: 10px;
   background: rgba(255, 255, 255, 0.04);
 }
 .videos h2 { margin: 0 0 8px; font-size: 14px; letter-spacing: 0.2px; }
 .video__player {
   width: 100%;
   max-height: 420px;
   background: rgba(0, 0, 0, 0.25);
   border-radius: 8px;
 }
 .videos__list {
   margin: 10px 0 0;
   padding-left: 18px;
   font-size: 12px;
 }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
}
.card {
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 10px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.04);
}
.card__header {
  padding: 10px 10px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(0, 0, 0, 0.12);
}
.card__header code { font-size: 12px; }
.card__imgLink { display: block; }
.card__img {
  display: block;
  width: 100%;
  height: 200px;
  object-fit: contain;
  background: rgba(0, 0, 0, 0.25);
}
.empty {
  padding: 18px;
  border: 1px dashed rgba(255, 255, 255, 0.25);
  border-radius: 10px;
  opacity: 0.9;
}
""".strip()

    doc = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8" />',
            '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"  <title>{html.escape(title)}</title>",
            "  <style>",
            css,
            "  </style>",
            "</head>",
            "<body>",
            '  <header class="page">',
            f"    <h1>{html.escape(title)}</h1>",
            f'    <p class="meta"><code>{subtitle}</code> &middot; {html.escape(count)}</p>',
            f'    <div class="nav">{nav}</div>',
            "  </header>",
            "  <main>",
            videos_html,
            '    <section class="grid">',
            "      "
            + (
                "\n      ".join(items_html)
                if items_html
                else '<div class="empty">No screenshots found in shots/</div>'
            ),
            "    </section>",
            "  </main>",
            "</body>",
            "</html>",
        ]
    )

    out_file.write_text(doc, encoding="utf-8")
    print(str(out_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
