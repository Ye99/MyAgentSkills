from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

IMAGE_SKILLS = {
    "extract-embedded-images": {
        "description_terms": ("base64", "without OCR", "externalize base64"),
    },
    "externalize-image-and-extract-text": {
        "description_terms": ("OCR", "extract text from image", "image conversion"),
    },
    "rewrite-obsidian-image-notes": {
        "description_terms": ("replaced by editable Markdown",),
    },
}

ROUTING_ROWS = (
    ("base64", "only wants assets extracted", "extract-embedded-images"),
    ("replaced by editable Markdown", "rewrite-obsidian-image-notes"),
    ("keep/externalize", "also add its text", "externalize-image-and-extract-text"),
)


def skill_doc(name: str) -> str:
    return (REPO / name / "SKILL.md").read_text(encoding="utf-8")


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter"
    body = match.group("body")
    values: dict[str, str] = {}
    current_key: str | None = None
    for line in body.splitlines():
        if line.startswith("  ") and current_key:
            values[current_key] += " " + line.strip()
            continue
        key, sep, value = line.partition(":")
        assert sep, f"frontmatter line lacks ':' delimiter: {line!r}"
        current_key = key
        values[key] = value.strip().removeprefix(">-").strip()
        values[key] = values[key].strip()
    return {key: value.strip() for key, value in values.items()}


def assert_all_terms(text: str, terms: tuple[str, ...]) -> None:
    missing = [term for term in terms if term not in text]
    assert not missing, f"missing routing terms: {missing}"


def test_image_skill_descriptions_are_decisive() -> None:
    for name, expectations in IMAGE_SKILLS.items():
        meta = frontmatter(skill_doc(name))

        assert meta["name"] == name
        assert meta["description"].startswith("Use when")
        assert_all_terms(meta["description"], expectations["description_terms"])


def test_all_image_skills_share_the_same_routing_table() -> None:
    for name in IMAGE_SKILLS:
        text = skill_doc(name)

        assert "## Choose the Right Skill" in text
        for row_terms in ROUTING_ROWS:
            assert_all_terms(text, row_terms)


def test_readme_lists_image_skills_in_decision_order() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    extract_pos = readme.index("**extract-embedded-images**")
    externalize_pos = readme.index("**externalize-image-and-extract-text**")
    rewrite_pos = readme.index("**rewrite-obsidian-image-notes**")

    assert extract_pos < externalize_pos < rewrite_pos
