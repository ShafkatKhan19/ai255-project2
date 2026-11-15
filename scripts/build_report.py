"""Fill docs/REPORT.md with the captured evidence and write docs/REPORT.filled.md.

  python scripts/build_report.py

Reads:
  evidence/url.txt              the live address
  evidence/<name>.txt           outputs saved by scripts/evidence.ps1
  evidence/notes-<name>.md      my own notes on each experiment
  docs/ai_disclosure.md         the AI-use statement
Anything missing is marked NOT CAPTURED YET so it cannot be overlooked.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
MISSING = "NOT CAPTURED YET"


def read(path):
    if not path.exists():
        return None
    # PowerShell writes a byte-order mark at the start of UTF-8 files.
    lines = path.read_text(encoding="utf-8-sig").strip().splitlines()
    # "kubectl get pods -o wide" ends with two columns that are always
    # empty here. Cut them off so the table fits on the page.
    for header in lines:
        cut = header.find("NOMINATED NODE")
        if cut > 0:
            lines = [line[:cut].rstrip() for line in lines]
            break
    return "\n".join(line.rstrip() for line in lines)


def main():
    text = (ROOT / "docs" / "REPORT.md").read_text(encoding="utf-8")
    missing = []

    def fill(kind, name, path):
        content = read(path)
        if content is None:
            missing.append(f"{kind}:{name}")
            return MISSING
        return content

    text = text.replace("{{URL}}", fill("url", "url", EVIDENCE / "url.txt"))
    text = re.sub(r"\{\{EVIDENCE:([\w-]+)\}\}",
                  lambda m: fill("evidence", m.group(1), EVIDENCE / f"{m.group(1)}.txt"), text)
    text = re.sub(r"\{\{NOTES:([\w-]+)\}\}",
                  lambda m: fill("notes", m.group(1), EVIDENCE / f"notes-{m.group(1)}.md"), text)
    text = text.replace("{{AI_DISCLOSURE}}", fill("disclosure", "ai", ROOT / "docs" / "ai_disclosure.md"))

    out = ROOT / "docs" / "REPORT.filled.md"
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    if missing:
        print(f"{len(missing)} item(s) still missing:")
        for item in missing:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
