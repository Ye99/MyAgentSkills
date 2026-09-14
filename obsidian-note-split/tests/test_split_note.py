import json
import pathlib
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "split_note.py"

SOURCE = """\
Intro line about alpha, before any heading.

## Alpha

Alpha body. See [[#Beta details]] and [beta](#Beta%20details).

```sh
# not a heading, must keep one hash
echo "alpha"
```

### Gotchas

Alpha gotcha.

## Beta

Beta body with a literal \\n escape and a secret-looking token 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi.

### Beta details

Detail text.

### Gotchas

Beta gotcha.

## Alpha continued

More alpha, back to [[#Alpha]].


"""

OTHER = """\
Links in: [[Big Note#Beta details]] and [md](Big%20Note.md#Beta%20details) and [[Big Note]] and [[Big Note#Alpha|a]].
"""


def make_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Big Note.md").write_text(SOURCE, encoding="utf-8")
    (vault / "Other.md").write_text(OTHER, encoding="utf-8")
    return vault


def lines_of(text):
    return text.split("\n")


def base_plan():
    n = len(SOURCE.split("\n")) - 1
    lines = SOURCE.split("\n")
    beta = lines.index("## Beta") + 1
    cont = lines.index("## Alpha continued") + 1
    return {
        "source": "Big Note.md",
        "date": "2026-01-01",
        "notes": [
            {"name": "Big Note Alpha", "description": "Alpha things.", "tags": ["alpha"],
             "segments": [[1, beta - 1, 0], [cont, n, 0]]},
            {"name": "Big Note Beta", "description": "Beta things.", "tags": ["beta"],
             "segments": [[beta, cont - 1, 0]]},
        ],
    }


def run(vault, plan, tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(vault), "--plan", str(p), "--out", str(out)],
                       capture_output=True, text=True)
    return r, out


def test_valid_plan_passes_all_checks_and_moves_text_verbatim(tmp_path):
    vault = make_vault(tmp_path)
    r, out = run(vault, base_plan(), tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERIFICATION PASSED" in r.stdout
    alpha = (out / "notes" / "Big Note Alpha.md").read_text(encoding="utf-8")
    beta = (out / "notes" / "Big Note Beta.md").read_text(encoding="utf-8")
    # verbatim content, including escapes and secret-looking text (never redacted)
    assert "literal \\n escape" in beta and "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi" in beta
    # hash comment inside a code fence untouched
    assert "# not a heading, must keep one hash" in alpha
    # links to a heading that moved now name the destination note; same-note links stay short
    assert "[[Big Note Beta#Beta details]]" in alpha
    assert "](Big%20Note%20Beta.md#Beta%20details)" in alpha
    assert "[[#Alpha]]" in alpha
    # vault untouched
    assert (vault / "Big Note.md").read_text(encoding="utf-8") == SOURCE


def test_inbound_links_in_other_notes_are_rewritten_and_bare_links_kept(tmp_path):
    vault = make_vault(tmp_path)
    r, out = run(vault, base_plan(), tmp_path)
    assert r.returncode == 0, r.stdout
    other = (out / "rewrites" / "Other.md").read_text(encoding="utf-8")
    assert "[[Big Note Beta#Beta details]]" in other
    assert "(Big%20Note%20Beta.md#Beta%20details)" in other
    assert "[[Big Note]]" in other                      # the index keeps the bare link valid
    assert "[[Big Note Alpha#Alpha|a]]" in other
    report = json.loads((out / "report.json").read_text())
    assert report["rewrites"] == ["Other.md"] and "Other.md" in report["read_sha256"]


def test_index_uses_nested_links_for_duplicate_headings(tmp_path):
    vault = make_vault(tmp_path)
    plan = base_plan()
    plan["notes"] = [{"name": "Big Note All", "description": "Everything.", "tags": [],
                      "segments": [[1, len(SOURCE.split("\n")) - 1, 0]]}]
    plan["index"] = {"h3_when_h2_fewer_than": 10}
    r, out = run(vault, plan, tmp_path)
    assert r.returncode == 0, r.stdout
    index = (out / "notes" / "Big Note.md").read_text(encoding="utf-8")
    assert "[[Big Note All#Alpha#Gotchas|Gotchas]]" in index
    assert "[[Big Note All#Beta#Gotchas|Gotchas]]" in index


def test_heading_shift_changes_only_hash_count(tmp_path):
    vault = make_vault(tmp_path)
    plan = base_plan()
    plan["notes"][1]["segments"] = [[s[0], s[1], 0] for s in plan["notes"][1]["segments"]]
    lines = SOURCE.split("\n")
    d = lines.index("### Beta details") + 1
    beta_first, beta_last, _ = plan["notes"][1]["segments"][0]
    plan["notes"][1]["segments"] = [[beta_first, d - 1, 0], [d, beta_last, 1]]
    r, out = run(vault, plan, tmp_path)
    assert r.returncode == 0, r.stdout
    assert "\n## Beta details\n" in (out / "notes" / "Big Note Beta.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p["notes"][1]["segments"].__setitem__(0, [p["notes"][1]["segments"][0][0] - 1,
                                                          p["notes"][1]["segments"][0][1], 0]), "assigned to both"),
    (lambda p: p["notes"][0]["segments"].pop(), "unassigned lines"),
    (lambda p: p["notes"][1].__setitem__("name", "Bad/Name"), "invalid note name"),
    (lambda p: p["notes"][0]["segments"].__setitem__(0, [1, p["notes"][0]["segments"][0][1], 1]), "heading would become level"),
])
def test_bad_plans_fail_before_writing_notes(tmp_path, mutate, message):
    vault = make_vault(tmp_path)
    plan = base_plan()
    mutate(plan)
    r, out = run(vault, plan, tmp_path)
    assert r.returncode == 1
    assert message in r.stdout


def test_refuses_note_names_that_already_exist_in_the_vault(tmp_path):
    vault = make_vault(tmp_path)
    r, out = run(vault, base_plan(), tmp_path)
    assert r.returncode == 0
    (vault / "Big Note Alpha.md").write_text("x", encoding="utf-8")
    r2, _ = run(vault, base_plan(), tmp_path)
    assert r2.returncode == 1 and "already exists" in r2.stdout
