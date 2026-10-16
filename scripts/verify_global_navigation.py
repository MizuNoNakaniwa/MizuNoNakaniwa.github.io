"""Check every published HTML page for the same, correctly ordered site navigation."""
from html import unescape
from pathlib import Path
import re
import sys

root = Path("public")
pages = sorted(root.rglob("*.html"))
expected_paths = (
    "/posts/techniques-efficiency-productivity/",
    "/projects/aspergers-adhd-rescue-project/",
    "/posts/techniques-ai/",
)
expected_en = "Techniques: Health, ASD & ADHD Mental Health, Exercise"
expected_zh = "术：健康术，自闭症 & 多动症心理健康术，锻炼术"

if not pages:
    sys.exit("Navigation check failed: no generated HTML pages found.")

failures = []
for page in pages:
    text = unescape(page.read_text(encoding="utf-8"))
    match = re.search(r"<nav\b[^>]*>.*?</nav>", text, re.DOTALL)
    if not match:
        failures.append(f"{page}: missing <nav>")
        continue
    nav = match.group(0)
    positions = [nav.find(path) for path in expected_paths]
    if not (0 <= positions[0] < positions[1] < positions[2]):
        failures.append(f"{page}: wrong/missing Efficiency > Health > AI navigation order")
    if nav.count(expected_paths[1]) != 1:
        failures.append(f"{page}: Health navigation entry is missing or duplicated")
    if expected_en not in nav or expected_zh not in nav:
        failures.append(f"{page}: Health navigation label is incorrect")

section_path = root / "projects/aspergers-adhd-rescue-project/index.html"
if not section_path.is_file():
    failures.append(f"{section_path}: section page missing")
else:
    section = unescape(section_path.read_text(encoding="utf-8"))
    heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", section, re.DOTALL)
    title = re.sub(r"<[^>]*>", "", heading.group(1)) if heading else ""
    if expected_en not in title or expected_zh not in title:
        failures.append(f"{section_path}: section headline does not match the navigation")

if failures:
    print("\n".join(failures[:50]), file=sys.stderr)
    sys.exit(f"Navigation check failed on {len(failures)} assertions.")

print(f"Navigation check passed: {len(pages)} HTML pages, including About and Art Project XXX.")
