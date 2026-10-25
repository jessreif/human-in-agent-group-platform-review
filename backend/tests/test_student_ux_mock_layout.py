"""Keep the mock countdown centered on the task header, not the admin header."""

import re
from pathlib import Path


def test_mock_task_header_has_equal_side_columns_and_center_timer() -> None:
    template = (Path(__file__).resolve().parents[2] / "demos" / "template" / "investigators_demo.html").read_text()
    task = re.search(r"\.task-header \{([^}]+)\}", template)
    admin = re.search(r"\.admin-head \{([^}]+)\}", template)
    assert task is not None and admin is not None
    assert "display: grid;" in task[1]
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);" in task[1]
    assert "display: flex;" in admin[1]
    markup = template.split('<header class="task-header">', 1)[1]
    assert markup.index('class="task-heading"') < markup.index('id="timer"')
    assert markup.index('id="timer"') < markup.index('class="task-controls"')
