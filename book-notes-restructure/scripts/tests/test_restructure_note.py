import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
RESTRUCTURE = SCRIPTS / 'restructure_note.py'
VERIFY = SCRIPTS / 'verify_verbatim.py'

# Line numbers used by the plans below:
#  1 # Head of note        6 (blank)              11   indented   diagram
#  2 Untouched preamble.   7 ### A question ...   12 ```
#  3 (blank)               8 Answer line one.     13 (blank)
#  4 ### The Source        9 (blank)              14 ### Chapter material
#  5 by someone           10 ```text              15 Body from the source.
NOTE = """\
# Head of note
Untouched preamble.

### The Source
by someone

### A question I looked up
Answer line one.

```text
  indented   diagram
```

### Chapter material
Body from the source.
"""


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding='utf-8')
    return p


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)],
                          capture_output=True, text=True)


def base_plan():
    return {
        'start_line': 4,
        'callout': '> [!note]+ My lookup',
        'items': [
            {'level': 2, 'title': 'The Source', 'lines': [5, 6], 'kind': 'source'},
            {'level': 3, 'title': 'Chapter 1', 'kind': 'source'},
            {'level': 4, 'title': 'Chapter material', 'lines': [15, 15], 'kind': 'source'},
            {'level': 4, 'title': 'A question I looked up', 'lines': [8, 13], 'kind': 'lookup'},
        ],
    }


def test_moves_verbatim_and_verifies(tmp_path):
    note = write(tmp_path, 'Note.md', NOTE)
    before = write(tmp_path, 'before.md', NOTE)
    plan = write(tmp_path, 'plan.json', json.dumps(base_plan()))

    r = run(RESTRUCTURE, note, '--plan', plan)
    assert r.returncode == 0, r.stderr

    out = note.read_text(encoding='utf-8')
    assert out.startswith('# Head of note\nUntouched preamble.\n')
    assert '## The Source' in out
    assert '### Chapter 1' in out
    # source blocks stay unquoted, lookup blocks get wrapped
    assert '\nBody from the source.' in out
    assert '> [!note]+ My lookup' in out
    assert '> Answer line one.' in out
    # the whitespace-sensitive code fence survives inside the callout
    assert '>   indented   diagram' in out

    v = run(VERIFY, before, note, '--from-line', '4')
    assert 'VERIFICATION PASSED' in v.stdout, v.stdout + v.stderr
    assert v.returncode == 0


def test_reordering_is_allowed(tmp_path):
    """The lookup section is emitted after material that followed it originally."""
    note = write(tmp_path, 'Note.md', NOTE)
    plan = write(tmp_path, 'plan.json', json.dumps(base_plan()))
    run(RESTRUCTURE, note, '--plan', plan)
    out = note.read_text(encoding='utf-8')
    assert out.index('Body from the source.') < out.index('Answer line one.')


def test_dropped_line_is_refused(tmp_path):
    note = write(tmp_path, 'Note.md', NOTE)
    plan_data = base_plan()
    plan_data['items'] = [i for i in plan_data['items'] if i['title'] != 'Chapter material']
    plan = write(tmp_path, 'plan.json', json.dumps(plan_data))

    r = run(RESTRUCTURE, note, '--plan', plan)
    assert r.returncode != 0
    assert 'in no item' in r.stdout + r.stderr
    assert note.read_text(encoding='utf-8') == NOTE  # unchanged


def test_duplicated_line_is_refused(tmp_path):
    note = write(tmp_path, 'Note.md', NOTE)
    plan_data = base_plan()
    plan_data['items'].append(
        {'level': 4, 'title': 'Duplicate', 'lines': [15, 15], 'kind': 'source'})
    plan = write(tmp_path, 'plan.json', json.dumps(plan_data))

    r = run(RESTRUCTURE, note, '--plan', plan)
    assert r.returncode != 0
    assert 'used by item' in r.stdout + r.stderr


def test_absorbed_line_and_prepend(tmp_path):
    """A line folded into a heading must be declared, and its remainder kept."""
    text = "# Top\n\n### S\nTitle Here  and the rest of the sentence\nmore body\n"
    note = write(tmp_path, 'Note.md', text)
    before = write(tmp_path, 'before.md', text)

    plan_data = {
        'start_line': 3,
        'items': [{'level': 2, 'title': 'Title Here', 'lines': [5, 5],
                   'prepend': ['and the rest of the sentence'], 'kind': 'source'}],
    }
    plan = write(tmp_path, 'plan.json', json.dumps(plan_data))
    r = run(RESTRUCTURE, note, '--plan', plan)
    assert r.returncode != 0 and 'absorbed_lines' in r.stdout + r.stderr

    plan_data['absorbed_lines'] = [4]
    plan.write_text(json.dumps(plan_data), encoding='utf-8')
    assert run(RESTRUCTURE, note, '--plan', plan).returncode == 0

    out = note.read_text(encoding='utf-8')
    assert '## Title Here' in out
    assert 'and the rest of the sentence' in out
    assert 'more body' in out

    v = run(VERIFY, before, note, '--from-line', '3')
    assert 'VERIFICATION FAILED' in v.stdout  # the split line is a real, reported difference


def test_verify_catches_reword(tmp_path):
    note = write(tmp_path, 'Note.md', NOTE)
    before = write(tmp_path, 'before.md', NOTE)
    plan = write(tmp_path, 'plan.json', json.dumps(base_plan()))
    run(RESTRUCTURE, note, '--plan', plan)

    tampered = note.read_text(encoding='utf-8').replace(
        'Body from the source.', 'Body from the source, reworded.')
    note.write_text(tampered, encoding='utf-8')

    v = run(VERIFY, before, note, '--from-line', '4')
    assert v.returncode != 0
    assert 'VERIFICATION FAILED' in v.stdout


def test_verify_catches_dropped_code_line(tmp_path):
    note = write(tmp_path, 'Note.md', NOTE)
    before = write(tmp_path, 'before.md', NOTE)
    plan = write(tmp_path, 'plan.json', json.dumps(base_plan()))
    run(RESTRUCTURE, note, '--plan', plan)

    tampered = note.read_text(encoding='utf-8').replace('>   indented   diagram\n', '')
    note.write_text(tampered, encoding='utf-8')

    v = run(VERIFY, before, note, '--from-line', '4')
    assert v.returncode != 0
