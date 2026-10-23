"""Fail a deployment if any generated page has an inconsistent or stale menu."""
from html import unescape
from pathlib import Path
import os
import re
import sys

root = Path("public")
pages = sorted(root.rglob("*.html"))
nav_sha = os.environ.get("GITHUB_SHA", "")
expected_paths = (
    "/posts/techniques-efficiency-productivity/",
    "/projects/aspergers-adhd-rescue-project/",
    "/posts/techniques-ai/",
)
expected_en = "Techniques: Health, ASD & ADHD Mental Health, Exercise"
expected_zh = "术：健康术，自闭症 & 多动症心理健康术，锻炼术"
whimsical = "/projects/my-whimsical-ideas/"
sheet_music = "/posts/my-sheet-music/"
aesthetics = "/posts/my-aesthetics/"

if not pages:
    sys.exit("Navigation check failed: no generated HTML pages found.")


def get_submenu(nav, menu_id):
    pattern = r'<ul\b[^>]*\bid=(?:"|\x27)?' + re.escape(menu_id) + r'(?:"|\x27)?[^>]*>.*?</ul>'
    match = re.search(pattern, nav, re.DOTALL)
    return match.group(0) if match else None


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
        failures.append(f"{page}: wrong/missing Efficiency > Health > AI order")
    if nav.count(expected_paths[1]) != 1:
        failures.append(f"{page}: Health entry missing or duplicated")
    if expected_en not in nav or expected_zh not in nav:
        failures.append(f"{page}: Health navigation label incorrect")

    projects = get_submenu(nav, "projects-submenu")
    blog = get_submenu(nav, "blog-submenu")
    if not projects or not blog:
        failures.append(f"{page}: Projects or Blog submenu missing")
        continue

    if whimsical in projects:
        failures.append(f"{page}: Whimsical Ideas remains under Projects")
    if blog.count(whimsical) != 1:
        failures.append(f"{page}: Whimsical Ideas missing/duplicated under Blog")
    if not (0 <= blog.find(sheet_music) < blog.find(whimsical) < blog.find(aesthetics)):
        failures.append(f"{page}: Whimsical Ideas is not immediately after Sheet Music")

    # Keep app projects in the same order and under the right headings on every page.
    consigliere = "/projects/consigliere-ai/"
    mindgruad = "/projects/mindgruad-app/"
    claimtome = "/projects/claimtome-ai/"
    if not (0 <= projects.find(consigliere) < projects.find(mindgruad) < projects.find(claimtome)):
        failures.append(f"{page}: wrong Consigliere > MindGruad > ClaimTome order")
    if projects.count(mindgruad) != 1 or projects.count(consigliere) != 1:
        failures.append(f"{page}: Consigliere or MindGruad item missing/duplicated")
    for label in ("Consigliere AI & Consigliere APP", "军师 AI & 军师 APP", "MindGruad APP", "自持慎言 APP"):
        if label not in projects:
            failures.append(f"{page}: app menu label missing: {label}")

    # A versioned URL forces a new cache key for every release.
    if nav_sha:
        for target in ("/", "/projects/art-project-xxx/", sheet_music, whimsical):
            versioned = f"{target}?nav={nav_sha}"
            if nav.count('href="' + versioned + '"') != 1:
                failures.append(f"{page}: missing or incorrect versioned link {versioned}")

for required in ("index.html", "projects/art-project-xxx/index.html"):
    if not (root / required).is_file():
        failures.append(f"Missing key generated page: {required}")

section_path = root / "projects/aspergers-adhd-rescue-project/index.html"
if not section_path.is_file():
    failures.append(f"{section_path}: section page missing")
else:
    section = unescape(section_path.read_text(encoding="utf-8"))
    heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", section, re.DOTALL)
    title = re.sub(r"<[^>]*>", "", heading.group(1)) if heading else ""
    if expected_en not in title or expected_zh not in title:
        failures.append(f"{section_path}: section headline does not match the navigation")

for section_name, labels in (
    ("consigliere-ai", ("Consigliere AI & Consigliere APP", "军师 AI & 军师 APP")),
    ("mindgruad-app", ("MindGruad APP", "自持慎言 APP")),
):
    section_file = root / "projects" / section_name / "index.html"
    if not section_file.exists():
        failures.append(f"{section_file}: app page missing")
        continue
    markup = unescape(section_file.read_text(encoding="utf-8"))
    h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", markup, re.DOTALL)
    heading = re.sub(r"<[^>]*>", "", h1.group(1)) if h1 else ""
    if any(label not in heading for label in labels):
        failures.append(f"{section_file}: title is inconsistent with menu")

if failures:
    print("\n".join(failures[:60]), file=sys.stderr)
    sys.exit(f"Navigation check failed on {len(failures)} assertions.")

print(
    f"Navigation check passed: {len(pages)} HTML pages; "
    "Whimsical Ideas under Blog after Sheet Music, Health order/headline correct, "
    "versioned links verified, including About and Art Project XXX."
)
