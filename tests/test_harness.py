from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
from copy import deepcopy
from pathlib import Path

from support import (
    LIVE_OUTPUT_TIMEOUT,
    LIVE_READY_TIMEOUT,
    SCRIPT,
    LoopFixture,
)


def test_opencode_attempts_are_private_and_use_fresh_sessions(loop_fixture: LoopFixture) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == [2, 3]
    assert len({call["attempt_id"] for call in state["opencode_calls"]}) == 2
    for call in state["opencode_calls"]:
        args = call["args"]
        assert args[0] == "run"
        assert "--standalone" in args
        assert args[args.index("--format") + 1] == "json"
        assert "--continue" not in args
        assert "--session" not in args
        assert "--fork" not in args
        assert call["result_initially_absent"]
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["3"]["state"] == "closed"
    assert (runs[0] / "attempt-1-ticket-2/agent.log").is_file()
    assert (runs[0] / "attempt-2-ticket-3/agent.log").is_file()


def test_opencode_renders_completed_text_and_generic_tool_blocks(loop_fixture: LoopFixture) -> None:
    assistant_text = "Assistant wording stays unchanged.\n\n**Assistant Markdown stays unchanged.**"
    events = [
        {
            "type": "text",
            "timestamp": 1,
            "sessionID": "ses_fixture",
            "part": {"type": "text", "text": assistant_text},
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_fixture",
            "part": {
                "type": "tool",
                "tool": "example_tool",
                "state": {
                    "status": "completed",
                    "input": {"target": "the fixture"},
                    "output": "result line",
                    "title": "Inspect fixture",
                },
            },
        },
    ]
    raw_output = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    assert result.returncode == 0, result.stderr.decode()
    expected_tool_block = (
        '✓ Inspect fixture (example_tool)\n  │ {"target":"the fixture"}\n  └ result line'
    )
    assert (
        "── Agent ──\n" + assistant_text + "\n\n" + expected_tool_block
    ).encode() in result.stdout
    assert result.stdout.count("── Agent ──".encode()) == 1
    assert b"Assistant\n" not in result.stdout
    assert b"tool: example_tool" not in result.stdout
    assert b"input:" not in result.stdout
    assert b"\x1b[" not in result.stdout
    assert b'"type": "tool_use"' not in result.stdout
    assert result.stdout.count(b"OpenCode: github-copilot") == 1
    attempt = runs[0] / "attempt-1-ticket-2"
    assert (attempt / "agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_adjacent_text_events_preserve_bodies_and_separation(
    loop_fixture: LoopFixture,
) -> None:
    bodies = ["**first**\n`literal`", "", "second\n", "third\n\n", "final 雪"]
    events = [{"type": "text", "part": {"text": body}} for body in bodies]
    raw_output = "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    assert result.returncode == 0, result.stderr.decode()
    expected = (
        "── Agent ──\n**first**\n`literal`\n\n"
        "── Agent ──\nsecond\n\n"
        "── Agent ──\nthird\n\n"
        "── Agent ──\nfinal 雪\n\n"
    )
    assert expected.encode() in result.stdout
    assert result.stdout.count("── Agent ──".encode()) == 4
    assert b"\x1b[" not in result.stdout
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode()


def test_opencode_unrecognized_and_malformed_output_stays_visible(
    loop_fixture: LoopFixture,
) -> None:
    raw_lines = [
        '{"type":"future_event","value":"future details"}',
        '{"type":"tool_use","part":{"tool":"odd_tool","state":{"status":[]}}}',
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "running_tool",
                    "state": {
                        "status": "running",
                        "title": "Running action",
                        "output": "not a completed result",
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "tool": "pending_tool",
                    "state": {"status": "pending", "title": "Pending action"},
                },
            }
        ),
        '{"type":',
        "OpenCode startup diagnostic",
        json.dumps({"type": "tool_use", "part": {"state": {"status": "completed"}}}),
        '{"type":"step_start"}',
        json.dumps(
            {
                "type": "step_start",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {
                    "id": "prt_step",
                    "sessionID": "ses",
                    "messageID": "msg_step",
                    "type": "step-start",
                },
            }
        ),
        json.dumps(
            {
                "type": "step_start",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {
                    "id": "prt_step",
                    "sessionID": "ses",
                    "messageID": "msg_step",
                    "type": "step-start",
                    "snapshot": "snapshot token",
                },
            }
        ),
        json.dumps(
            {
                "type": "step_start",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {
                    "id": "prt_step",
                    "sessionID": "ses",
                    "messageID": "msg_step",
                    "type": "step-start",
                    "snapshot": {"unexpected": "object"},
                },
            }
        ),
        json.dumps(
            {
                "type": "step_start",
                "timestamp": 1,
                "sessionID": "ses",
                "data": {
                    "part": {
                        "id": "prt_step",
                        "sessionID": "ses",
                        "messageID": "msg_step",
                        "type": "step-start",
                        "snapshot": "snapshot token",
                    }
                },
            }
        ),
        '{"type":"step_finish","part":{"type":"unexpected"}}',
        json.dumps(
            {
                "type": "step_finish",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {"type": "step-finish"},
            }
        ),
        json.dumps(
            {
                "type": "step_finish",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {
                    "id": "prt_step",
                    "sessionID": "ses",
                    "messageID": "msg_step",
                    "type": "step-finish",
                    "reason": None,
                },
            }
        ),
        json.dumps(
            {
                "type": "step_finish",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {
                    "id": "prt_step",
                    "sessionID": "ses",
                    "messageID": "msg_step",
                    "type": "step-finish",
                    "reason": "stop",
                },
            }
        ),
        json.dumps(
            {"type": "reasoning", "part": {"type": "reasoning", "text": "secret reasoning"}}
        ),
        json.dumps(
            {
                "type": "reasoning",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {"type": "reasoning", "text": "secret reasoning"},
            }
        ),
        json.dumps(
            {
                "type": "reasoning",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {
                    "id": "prt_reasoning",
                    "sessionID": "ses",
                    "messageID": "msg_reasoning",
                    "type": "reasoning",
                    "text": "secret reasoning",
                    "time": {},
                },
            }
        ),
        json.dumps(
            {
                "type": "reasoning",
                "timestamp": 1,
                "sessionID": "ses",
                "part": {
                    "id": "prt_reasoning",
                    "sessionID": "ses-other",
                    "messageID": "msg_reasoning",
                    "type": "reasoning",
                    "text": "secret reasoning",
                    "time": {"start": 1, "end": 2},
                },
            }
        ),
        '{"type":"reasoning"}',
        json.dumps({"type": "text", "part": {"type": "text", "text": "before\ud800after"}}),
    ]
    raw_output = "\n".join(raw_lines) + "\n"
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    assert result.returncode == 0, result.stderr.decode()
    assert b"OpenCode event: future_event" in result.stdout
    assert b"future details" in result.stdout
    assert b"OpenCode tool event" in result.stdout
    assert "✓ Running action".encode() not in result.stdout
    assert "✓ Pending action".encode() not in result.stdout
    assert b"OpenCode diagnostic" in result.stdout
    assert b"OpenCode startup diagnostic" in result.stdout
    assert b"Use tool (unknown tool)" in result.stdout
    assert b"No output" in result.stdout
    assert result.stdout.count(b"OpenCode step_start event") == 3
    assert result.stdout.count(b"OpenCode step_finish event") == 4
    assert result.stdout.count(b"OpenCode reasoning event") == 5
    assert b"secret reasoning" not in result.stdout
    assert b"before?after" in result.stdout
    assert b"Traceback" not in result.stdout + result.stderr
    attempt = runs[0] / "attempt-1-ticket-2"
    assert (attempt / "agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_generic_success_output_is_bounded_and_raw_record_is_complete(
    loop_fixture: LoopFixture,
) -> None:
    outputs = (
        ("Preview empty", ""),
        ("Preview three", "three-1\nthree-2\nthree-3"),
        ("Preview five", "five-1\nfive-2\nfive-3\nfive-4\nfive-5"),
        (
            "Preview seven",
            "seven-1\nseven-2\nseven-3\nseven-4\nseven-5\nseven-6\nseven-7",
        ),
    )
    events = [
        {
            "type": "tool_use",
            "timestamp": index,
            "sessionID": "ses_preview",
            "part": {
                "type": "tool",
                "tool": "unfamiliar_tool",
                "state": {
                    "status": "completed",
                    "title": title,
                    "input": {"target": title},
                    "output": output,
                },
            },
        }
        for index, (title, output) in enumerate(outputs, start=1)
    ]
    raw_output = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    assert result.returncode == 0, result.stderr.decode()
    visible = result.stdout.decode("utf-8")
    assert (
        '✓ Preview empty (unfamiliar_tool)\n  │ {"target":"Preview empty"}\n  └ No output'
        in visible
    )
    for index in range(1, 4):
        assert f"three-{index}" in visible
    for index in range(1, 6):
        assert f"five-{index}" in visible
        assert f"seven-{index}" in visible
    assert "seven-6" not in visible
    assert "seven-7" not in visible
    assert "... 2 additional line(s) omitted" in visible
    assert "✓ Preview seven (unfamiliar_tool)" in visible
    assert '  │ {"target":"Preview seven"}' in visible
    assert (
        "✓ Preview three (unfamiliar_tool)\n"
        '  │ {"target":"Preview three"}\n'
        "  │ three-1\n"
        "  │ three-2\n"
        "  └ three-3"
    ) in visible
    assert (
        "✓ Preview seven (unfamiliar_tool)\n"
        '  │ {"target":"Preview seven"}\n'
        "  ├ seven-1\n"
        "  │ seven-2\n"
        "  │ seven-3\n"
        "  │ seven-4\n"
        "  │ seven-5\n"
        "  └ ... 2 additional line(s) omitted"
    ) in visible
    assert "tool:" not in visible
    assert "input:" not in visible
    assert "output:" not in visible
    attempt = runs[0] / "attempt-1-ticket-2"
    log = attempt / "agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert b"seven-6" in log.read_bytes()


def test_opencode_generic_extension_result_uses_bounded_input_fallback(
    loop_fixture: LoopFixture,
) -> None:
    event = {
        "type": "tool_use",
        "timestamp": 1,
        "sessionID": "ses_extension",
        "part": {
            "type": "tool",
            "tool": "extension.browser",
            "state": {
                "status": "completed",
                "title": "Inspect browser response",
                "input": {"url": "https://example.invalid", "details": "x" * 500},
                "output": "Browser result stays generic.",
            },
        },
    }

    raw_output, visible, runs = _run_opencode_events(loop_fixture, [event])

    assert "✓ Inspect browser response (extension.browser)" in visible
    block = next(block for block in visible.split("\n\n") if "Inspect browser response" in block)
    input_line = next(line for line in block.splitlines() if line.startswith("  │ "))
    input_summary = input_line.removeprefix("  │ ")
    assert len(input_summary) <= 240
    assert input_summary.endswith("...")
    assert "https://example.invalid" in input_summary
    assert "Browser result stays generic." in block
    assert "tool:" not in block
    assert "input:" not in block
    assert "output:" not in block
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_successful_skill_loads_suppress_instructions_and_keep_raw_event(
    loop_fixture: LoopFixture,
) -> None:
    instruction_payload = (
        '<skill_content name="Test-driven development">\n'
        "PRIVATE_SKILL_INSTRUCTIONS\n"
        "PRIVATE_SKILL_REFERENCE\n"
        "</skill_content>"
    )
    event = {
        "type": "tool_use",
        "timestamp": 1,
        "sessionID": "ses_skill",
        "part": {
            "type": "tool",
            "tool": "skill",
            "state": {
                "status": "completed",
                "title": "skill",
                "input": {"id": "tdd"},
                "output": instruction_payload,
                "metadata": {
                    "metadata": {
                        "name": "Test-driven development",
                        "directory": "/skills/tdd",
                    },
                    "content": [{"type": "text", "text": instruction_payload}],
                    "rawInput": '{"id":"tdd"}',
                },
            },
        },
    }

    raw_output, visible, runs = _run_opencode_events(loop_fixture, [event])

    assert "✓ Load Test-driven development instructions (skill)\n  └ Loaded tdd" in visible
    assert "PRIVATE_SKILL_INSTRUCTIONS" not in visible
    assert "PRIVATE_SKILL_REFERENCE" not in visible
    assert "<skill_content" not in visible
    assert '{"id":"tdd"}' not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")
    assert b"PRIVATE_SKILL_INSTRUCTIONS" in log.read_bytes()
    assert b"PRIVATE_SKILL_REFERENCE" in log.read_bytes()


def test_opencode_successful_questions_show_supplied_answers_and_keep_question_relationships(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_question",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "completed",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose a database",
                                "header": "Database",
                                "options": [
                                    {
                                        "label": "PostgreSQL",
                                        "description": "Relational database",
                                    },
                                    {"label": "SQLite", "description": "Embedded database"},
                                ],
                            }
                        ]
                    },
                    "output": "PRIVATE_QUESTION_MODEL_TEXT",
                    "metadata": {"metadata": {"answers": [["PostgreSQL"]]}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_question",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "completed",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Which databases do you prefer?",
                                "header": "Databases",
                                "options": [
                                    {"label": "PostgreSQL", "description": "Relational"},
                                    {"label": "SQLite", "description": "Embedded"},
                                ],
                                "multiple": True,
                            },
                            {
                                "question": "What must the migration preserve?",
                                "header": "Constraints\nand risks",
                                "options": [],
                            },
                            {
                                "question": "Choose a deployment region",
                                "header": "Region",
                                "options": [
                                    {"label": "EU West", "description": "Europe"},
                                    {"label": "US East", "description": "North America"},
                                ],
                            },
                        ]
                    },
                    "output": "PRIVATE_MULTI_QUESTION_MODEL_TEXT",
                    "metadata": {
                        "metadata": {
                            "answers": [
                                ["PostgreSQL", "SQLite"],
                                ["Keep the current table names; preserve naïve labels."],
                                ["EU West"],
                            ]
                        }
                    },
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_question",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "completed",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Share optional notes",
                                "header": "Notes",
                                "options": [],
                            }
                        ]
                    },
                    "output": "PRIVATE_NO_ANSWER_MODEL_TEXT",
                    "metadata": {"metadata": {"answers": [[]]}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 4,
            "sessionID": "ses_question",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "completed",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Submit a blank note",
                                "header": "Notes",
                                "options": [],
                            }
                        ]
                    },
                    "output": "PRIVATE_EMPTY_STRING_QUESTION_OUTPUT",
                    "metadata": {"metadata": {"answers": [[""]]}},
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✓ Choose a database (question)\n  └ Selected PostgreSQL" in visible
    assert (
        "✓ Answer 3 questions (question)\n"
        "  ├ Databases: Which databases do you prefer?\n"
        "  │ Selected PostgreSQL, SQLite\n"
        "  ├ Constraints and risks: What must the migration preserve?\n"
        "  │ Keep the current table names; preserve naïve labels.\n"
        "  │ Region: Choose a deployment region\n"
        "  └ Selected EU West"
    ) in visible
    assert "✓ Share optional notes (question)\n  └ No answer supplied" in visible
    assert '✓ Submit a blank note (question)\n  └ ""' in visible
    assert "PRIVATE_QUESTION_MODEL_TEXT" not in visible
    assert "PRIVATE_MULTI_QUESTION_MODEL_TEXT" not in visible
    assert "PRIVATE_NO_ANSWER_MODEL_TEXT" not in visible
    assert "PRIVATE_EMPTY_STRING_QUESTION_OUTPUT" not in visible
    assert "Relational database" not in visible
    assert "Embedded database" not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")
    assert b"PRIVATE_QUESTION_MODEL_TEXT" in log.read_bytes()
    assert b"PRIVATE_MULTI_QUESTION_MODEL_TEXT" in log.read_bytes()
    assert b"PRIVATE_NO_ANSWER_MODEL_TEXT" in log.read_bytes()
    assert b"PRIVATE_EMPTY_STRING_QUESTION_OUTPUT" in log.read_bytes()
    assert "preserve naïve labels".encode() in log.read_bytes()


def test_opencode_partial_skill_and_question_schemas_keep_generic_results(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_partial_summaries",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {
                    "status": "completed",
                    "title": "skill",
                    "input": {"id": "incomplete-skill"},
                    "output": "PARTIAL_SKILL_RESULT",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_partial_summaries",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "completed",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose a database",
                                "header": "Database",
                                "options": [
                                    {"label": "PostgreSQL", "description": "Relational"},
                                ],
                            }
                        ]
                    },
                    "output": "PARTIAL_QUESTION_RESULT",
                    "metadata": {"metadata": {"answers": []}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_partial_summaries",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "completed",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose a database",
                                "header": "Database",
                                "options": [],
                                "extra": "unsupported prompt field",
                            }
                        ]
                    },
                    "output": "UNSUPPORTED_QUESTION_RESULT",
                    "metadata": {"metadata": {"answers": [["PostgreSQL"]]}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 4,
            "sessionID": "ses_partial_summaries",
            "part": {
                "type": "tool",
                "tool": "question_extension",
                "state": {
                    "status": "completed",
                    "title": "Inspect extension question result",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose a database",
                                "header": "Database",
                                "options": [],
                            }
                        ]
                    },
                    "output": "EXTENSION_QUESTION_RESULT",
                    "metadata": {"metadata": {"answers": [["PostgreSQL"]]}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 5,
            "sessionID": "ses_partial_summaries",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "completed",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose a backend",
                                "header": "A question header longer than thirty characters",
                                "options": [],
                            }
                        ]
                    },
                    "output": "LONG_HEADER_QUESTION_RESULT",
                    "metadata": {"metadata": {"answers": [["PostgreSQL"]]}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 6,
            "sessionID": "ses_partial_summaries",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "completed",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose one database",
                                "header": "Database",
                                "options": [
                                    {"label": "PostgreSQL", "description": "Relational"},
                                    {"label": "SQLite", "description": "Embedded"},
                                ],
                            }
                        ]
                    },
                    "output": "UNSUPPORTED_SINGLE_CHOICE_ANSWERS",
                    "metadata": {"metadata": {"answers": [["PostgreSQL", "SQLite"]]}},
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✓ Use tool (skill)" in visible
    assert "Loaded incomplete-skill" not in visible
    assert "PARTIAL_SKILL_RESULT" in visible
    assert "✓ Use tool (question)" in visible
    assert "Selected PostgreSQL" not in visible
    assert "PARTIAL_QUESTION_RESULT" in visible
    assert "✓ Use tool (question)" in visible
    assert "UNSUPPORTED_QUESTION_RESULT" in visible
    assert "✓ Inspect extension question result (question_extension)" in visible
    assert "EXTENSION_QUESTION_RESULT" in visible
    assert "LONG_HEADER_QUESTION_RESULT" in visible
    assert "✓ Use tool (question)" in visible
    unsupported_answers = next(
        block for block in visible.split("\n\n") if "UNSUPPORTED_SINGLE_CHOICE_ANSWERS" in block
    )
    assert "Selected PostgreSQL, SQLite" not in unsupported_answers
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")


def test_opencode_incomplete_questions_do_not_get_success_summaries(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_incomplete_question",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "running",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose a pending database",
                                "header": "Database",
                                "options": [
                                    {"label": "PostgreSQL", "description": "Relational"},
                                ],
                            }
                        ]
                    },
                    "output": "PENDING_QUESTION_OUTPUT",
                    "metadata": {"metadata": {"answers": [["PostgreSQL"]]}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_incomplete_question",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "cancelled",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose a cancelled region",
                                "header": "Region",
                                "options": [{"label": "EU", "description": "Europe"}],
                            }
                        ]
                    },
                    "output": "CANCELLED_QUESTION_OUTPUT",
                    "metadata": {"metadata": {"answers": [["EU"]]}},
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✓ Choose a pending database (question)" not in visible
    assert "✓ Choose a cancelled region (question)" not in visible
    assert "Selected PostgreSQL" not in visible
    assert "Selected EU" not in visible
    assert "OpenCode tool event" in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")


def test_opencode_failed_skill_and_question_calls_keep_full_evidence(
    loop_fixture: LoopFixture,
) -> None:
    skill_error = "\n".join(f"SKILL_ERROR_LINE_{line}" for line in range(1, 7))
    skill_output = "\n".join(f"SKILL_OUTPUT_LINE_{line}" for line in range(1, 7))
    question_error = "\n".join(f"QUESTION_ERROR_LINE_{line}" for line in range(1, 7))
    question_output = "\n".join(f"QUESTION_OUTPUT_LINE_{line}" for line in range(1, 7))
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_summary_failures",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {
                    "status": "error",
                    "title": "skill",
                    "input": {"id": "tdd"},
                    "error": skill_error,
                    "output": skill_output,
                    "metadata": {
                        "metadata": {
                            "name": "Test-driven development",
                            "directory": "/skills/tdd",
                        }
                    },
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_summary_failures",
            "part": {
                "type": "tool",
                "tool": "question",
                "state": {
                    "status": "error",
                    "title": "question",
                    "input": {
                        "questions": [
                            {
                                "question": "Choose a database",
                                "header": "Database",
                                "options": [
                                    {"label": "PostgreSQL", "description": "Relational"},
                                ],
                            }
                        ]
                    },
                    "error": question_error,
                    "output": question_output,
                    "metadata": {"metadata": {"answers": [["PostgreSQL"]]}},
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✗ Load Test-driven development instructions (skill)" in visible
    assert "Loaded tdd" not in visible
    assert "✗ Choose a database (question)" in visible
    for line in range(1, 7):
        assert f"SKILL_ERROR_LINE_{line}" in visible
        assert f"SKILL_OUTPUT_LINE_{line}" in visible
        assert f"QUESTION_ERROR_LINE_{line}" in visible
        assert f"QUESTION_OUTPUT_LINE_{line}" in visible
    assert "additional line(s) omitted" not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")


def test_opencode_tool_output_preserves_supplied_ansi_in_preview_and_raw_log(
    loop_fixture: LoopFixture,
) -> None:
    colored_output = "\x1b[31mred first line\nred second line\x1b[0m"
    event = {
        "type": "tool_use",
        "timestamp": 1,
        "sessionID": "ses_ansi",
        "part": {
            "type": "tool",
            "tool": "ansi_tool",
            "state": {
                "status": "completed",
                "title": "Show colored output",
                "output": colored_output,
            },
        },
    }

    raw_output, visible, runs = _run_opencode_events(
        loop_fixture, [event], extra_env={"NO_COLOR": ""}
    )

    assert "✓ Show colored output (ansi_tool)" in visible
    assert "  │ \x1b[31mred first line\n  └ red second line\x1b[0m" in visible
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")
    assert b"\\u001b[31mred first line" in raw_output.encode("utf-8")


def test_opencode_terminal_multiline_tool_output_preserves_supplied_ansi_styles(
    loop_fixture: LoopFixture,
) -> None:
    output = loop_fixture._assert_live_harness_output(
        terminal=True,
        live_output_fragment=b"\x1b[1;2;31mred first line\nred second line\x1b[0m",
    ).replace(b"\r\n", b"\n")

    assert b"\x1b[1;2;31mred first line\n  \xe2\x94\x94 red second line\x1b[0m" in output


def test_opencode_file_modification_results_hide_inputs_and_strip_patch_header(
    loop_fixture: LoopFixture,
) -> None:
    patch_input = (
        "*** Begin Patch\n*** Update File: src/settings.py\n+SECRET_PATCH_INPUT\n*** End Patch"
    )
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_file_changes",
            "part": {
                "id": "call_patch",
                "type": "tool",
                "tool": "patch",
                "state": {
                    "status": "completed",
                    "title": "patch",
                    "input": {"patchText": patch_input},
                    "output": "Success. Updated the following files:\n"
                    "M src/settings.py\nA tests/test_settings.py\n"
                    "M src/config.py\nD src/old_config.py\n"
                    "A docs/settings.md\nM sixth-result.py",
                    "metadata": {
                        "metadata": {
                            "files": [
                                {
                                    "file": "src/settings.py",
                                    "patch": "SECRET_PATCH_DIFF",
                                }
                            ]
                        }
                    },
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_file_changes",
            "part": {
                "id": "call_returned_diff",
                "type": "tool",
                "tool": "patch",
                "state": {
                    "status": "completed",
                    "title": "Show returned change",
                    "input": {"patchText": "SECRET_PATCH_FOR_RETURNED_DIFF"},
                    "output": "--- a/src/returned.py\n+++ b/src/returned.py\n"
                    "@@ -1 +1 @@\n-old\n+new",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_file_changes",
            "part": {
                "id": "call_edit",
                "type": "tool",
                "tool": "edit",
                "state": {
                    "status": "completed",
                    "title": "edit",
                    "input": {
                        "path": "src/app.py",
                        "oldString": "SECRET_OLD_CONTENT",
                        "newString": "SECRET_NEW_CONTENT",
                    },
                    "output": "Edited src/app.py (1 replacement)",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 4,
            "sessionID": "ses_file_changes",
            "part": {
                "id": "call_write",
                "type": "tool",
                "tool": "write",
                "state": {
                    "status": "completed",
                    "title": "write",
                    "input": {"path": "src/new.py", "content": "SECRET_FILE_CONTENT"},
                    "output": "Wrote file successfully: src/new.py",
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✓ Apply patch (patch)" in visible
    patch_block = next(block for block in visible.split("\n\n") if "(patch)" in block)
    for result_line in (
        "M src/settings.py",
        "A tests/test_settings.py",
        "M src/config.py",
        "D src/old_config.py",
        "A docs/settings.md",
    ):
        assert result_line in patch_block
    assert "Success. Updated the following files:" not in patch_block
    assert "sixth-result.py" not in patch_block
    assert "... 1 additional line(s) omitted" in patch_block
    diff_block = next(block for block in visible.split("\n\n") if "Show returned change" in block)
    for diff_line in (
        "--- a/src/returned.py",
        "+++ b/src/returned.py",
        "@@ -1 +1 @@",
        "-old",
        "+new",
    ):
        assert diff_line in diff_block
    assert "✓ Edit src/app.py (edit)" in visible
    assert "Edited src/app.py (1 replacement)" in visible
    assert "✓ Write src/new.py (write)" in visible
    assert "Wrote file successfully: src/new.py" in visible
    for private_input in (
        "SECRET_PATCH_INPUT",
        "SECRET_PATCH_DIFF",
        "SECRET_PATCH_FOR_RETURNED_DIFF",
        "SECRET_OLD_CONTENT",
        "SECRET_NEW_CONTENT",
        "SECRET_FILE_CONTENT",
    ):
        assert private_input not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")
    for private_input in (
        "SECRET_PATCH_INPUT",
        "SECRET_PATCH_DIFF",
        "SECRET_OLD_CONTENT",
        "SECRET_NEW_CONTENT",
        "SECRET_FILE_CONTENT",
    ):
        assert private_input.encode("utf-8") in log.read_bytes()


def test_opencode_file_and_webfetch_previews_preserve_supplied_ansi(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_colored_previews",
            "part": {
                "type": "tool",
                "tool": "patch",
                "state": {
                    "status": "completed",
                    "title": "Update colored file",
                    "input": {"patchText": "*** Begin Patch\n+line\n*** End Patch"},
                    "output": "Success. Updated the following files:\n"
                    "\x1b[35m+src/colored.py\x1b[0m",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_colored_previews",
            "part": {
                "type": "tool",
                "tool": "webfetch",
                "state": {
                    "status": "completed",
                    "title": "Fetch colored docs",
                    "input": {"url": "https://example.test/colored"},
                    "output": "\x1b[36m# Colored web title\x1b[0m",
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(
        loop_fixture, events, extra_env={"NO_COLOR": ""}
    )

    assert "✓ Update colored file (patch)" in visible
    assert "  └ \x1b[35m+src/colored.py\x1b[0m" in visible
    assert "✓ Fetch colored docs (webfetch)" in visible
    assert "  │ https://example.test/colored" in visible
    assert "  └ \x1b[36m# Colored web title\x1b[0m" in visible
    assert "\x1b[32m" not in visible
    assert "\x1b[1m" not in visible
    agent_log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert agent_log.read_bytes() == raw_output.encode("utf-8")
    assert b"\\u001b[35m" in agent_log.read_bytes()
    assert b"\\u001b[36m" in agent_log.read_bytes()


def test_opencode_webfetch_shows_url_and_bounded_content_preview(loop_fixture: LoopFixture) -> None:
    url = "https://docs.python.org/3/library/asyncio.html"
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_webfetch",
            "part": {
                "id": "call_webfetch",
                "type": "tool",
                "tool": "webfetch",
                "state": {
                    "status": "completed",
                    "title": "webfetch",
                    "input": {"url": url, "format": "markdown", "timeout": 20},
                    "output": "# asyncio — Asynchronous I/O\n"
                    "Retrieved content line two\n"
                    "Retrieved content line three\n"
                    "Retrieved content line four\n"
                    "Retrieved content line five\n"
                    "sixth content line",
                    "metadata": {"metadata": {"contentType": "text/markdown"}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_webfetch",
            "part": {
                "id": "call_empty_webfetch",
                "type": "tool",
                "tool": "webfetch",
                "state": {
                    "status": "completed",
                    "title": "webfetch",
                    "input": {"url": "https://example.test/empty", "format": "text"},
                    "output": "",
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    block = next(block for block in visible.split("\n\n") if "(webfetch)" in block)
    assert "✓ Fetch web content (webfetch)" in block
    assert f"  │ {url}" in block
    for line in (
        "# asyncio — Asynchronous I/O",
        "Retrieved content line two",
        "Retrieved content line three",
        "Retrieved content line four",
        "Retrieved content line five",
    ):
        assert line in block
    assert "sixth content line" not in block
    assert "... 1 additional line(s) omitted" in block
    assert "✓ Fetch web content (webfetch)" in visible
    assert "  │ https://example.test/empty\n  └ No output" in visible
    assert '"url":' not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")
    assert b"sixth content line" in log.read_bytes()


def test_opencode_file_and_webfetch_fallbacks_keep_results_without_guessing(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_family_fallback",
            "part": {
                "type": "tool",
                "tool": "edit",
                "state": {
                    "status": "completed",
                    "title": "edit",
                    "input": {
                        "path": "src/partial.py",
                        "oldString": 42,
                        "newString": "SECRET_UNSUPPORTED_REPLACEMENT",
                    },
                    "output": "Meaningful first edit result\nSecond edit result",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_family_fallback",
            "part": {
                "type": "tool",
                "tool": "webfetch",
                "state": {
                    "status": "completed",
                    "title": "webfetch",
                    "input": {"url": 42, "format": "markdown"},
                    "output": {"message": "Unsupported structured response remains visible"},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_family_fallback",
            "part": {
                "type": "tool",
                "tool": "patch_extension",
                "state": {
                    "status": "completed",
                    "title": "Inspect extension output",
                    "input": {"mode": "preview"},
                    "output": "Success. Updated the following files:\n"
                    "Keep this unrelated first line",
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert '✓ Edit src/partial.py (edit)\n  │ {"path":"src/partial.py"}' in visible
    assert "Meaningful first edit result" in visible
    assert "SECRET_UNSUPPORTED_REPLACEMENT" not in visible
    assert '  │ {"url":42,"format":"markdown"}' in visible
    assert '{"message":"Unsupported structured response remains visible"}' in visible
    assert "  │ 42" not in visible
    extension_block = next(
        block for block in visible.split("\n\n") if "Inspect extension output" in block
    )
    assert "Success. Updated the following files:" in extension_block
    assert "Keep this unrelated first line" in extension_block
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_file_and_webfetch_failures_keep_full_error_details(
    loop_fixture: LoopFixture,
) -> None:
    failed_tools = (
        ("patch", {"patchText": "SECRET_FAILURE_PATCH_INPUT"}),
        (
            "edit",
            {"path": "src/edit.py", "oldString": "old", "newString": "new"},
        ),
        ("write", {"path": "src/write.py", "content": "new file contents"}),
        ("webfetch", {"url": "https://example.test/failure", "format": "markdown"}),
    )
    events = []
    for timestamp, (tool_name, input_value) in enumerate(failed_tools, start=1):
        events.append(
            {
                "type": "tool_use",
                "timestamp": timestamp,
                "sessionID": "ses_failed_file_tools",
                "part": {
                    "partID": f"prt_{tool_name}",
                    "sessionID": "ses_failed_file_tools",
                    "messageID": "msg_failed_file_tools",
                    "id": f"call_{tool_name}",
                    "type": "tool",
                    "tool": tool_name,
                    "state": {
                        "status": "error",
                        "input": input_value,
                        "error": "\n".join(f"{tool_name}_error_{line}" for line in range(1, 7)),
                        "metadata": {
                            "providerCall": {"executed": True},
                            "providerResult": {"executed": True},
                            "rawInput": json.dumps(input_value),
                        },
                        "time": {"start": timestamp, "end": timestamp + 1},
                    },
                },
            }
        )

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    action_titles = {
        "patch": "Apply patch",
        "edit": "Edit src/edit.py",
        "write": "Write src/write.py",
        "webfetch": "Fetch web content",
    }
    for tool_name, _ in failed_tools:
        block = next(block for block in visible.split("\n\n") if f"({tool_name})" in block)
        assert f"✗ {action_titles[tool_name]} ({tool_name})" in block
        assert f"✓ {action_titles[tool_name]}" not in block
        for line in range(1, 7):
            assert f"{tool_name}_error_{line}" in block
        assert "additional line(s) omitted" not in block
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_failed_patch_keeps_any_available_output_unfiltered(
    loop_fixture: LoopFixture,
) -> None:
    # OpenCode 2.1.4 failures carry error details; retain output too if an
    # adapter includes it in a failed tool state.
    output = "Success. Updated the following files:\n" + "\n".join(
        f"partial-result-{line}" for line in range(1, 7)
    )
    event = {
        "type": "tool_use",
        "timestamp": 1,
        "sessionID": "ses_failed_patch_output",
        "part": {
            "type": "tool",
            "tool": "patch",
            "state": {
                "status": "error",
                "input": {"patchText": "SECRET_FAILED_PATCH_INPUT"},
                "error": "Patch failed after a partial application.",
                "output": output,
            },
        },
    }

    raw_output, visible, runs = _run_opencode_events(loop_fixture, [event])

    block = next(block for block in visible.split("\n\n") if "(patch)" in block)
    assert "✗ Apply patch (patch)" in block
    assert "Patch failed after a partial application." in block
    assert "Success. Updated the following files:" in block
    for line in range(1, 7):
        assert f"partial-result-{line}" in block
    assert "additional line(s) omitted" not in block
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_successful_reads_show_targets_and_reliable_counts_only(
    loop_fixture: LoopFixture,
) -> None:
    event = {
        "type": "tool_use",
        "timestamp": 1,
        "sessionID": "ses_read",
        "part": {
            "type": "tool",
            "tool": "read",
            "state": {
                "status": "completed",
                "title": "read",
                "input": {"path": "src/private.py", "offset": 4, "limit": 3},
                "output": "Read file src/private.py, lines 4-6\nSECRET_FILE_CONTENT",
                "metadata": {
                    "metadata": {
                        "display": {
                            "type": "file",
                            "totalLines": 120,
                            "truncated": False,
                        }
                    }
                },
            },
        },
    }

    zero_directory_event = {
        "type": "tool_use",
        "timestamp": 2,
        "sessionID": "ses_read",
        "part": {
            "type": "tool",
            "tool": "read",
            "state": {
                "status": "completed",
                "title": "read",
                "input": {"path": "empty-directory"},
                "output": "Read directory empty-directory, 0 entries",
                "metadata": {
                    "metadata": {
                        "display": {
                            "type": "directory",
                            "totalEntries": 0,
                            "truncated": False,
                        }
                    }
                },
            },
        },
    }

    media_event = {
        "type": "tool_use",
        "timestamp": 3,
        "sessionID": "ses_read",
        "part": {
            "type": "tool",
            "tool": "read",
            "state": {
                "status": "completed",
                "title": "Inspect image metadata",
                "input": {"path": "assets/diagram.png"},
                "output": "Image read successfully\nSECRET_IMAGE_CONTENT",
                "metadata": {
                    "metadata": {
                        "display": {
                            "type": "media",
                            "mime": "image/png",
                            "totalLines": 99,
                        },
                        "content": [{"type": "file", "uri": "data:image/png;base64,SECRET"}],
                        "totalLines": 99,
                    }
                },
            },
        },
    }

    direct_metadata_event = {
        "type": "tool_use",
        "timestamp": 4,
        "sessionID": "ses_read",
        "part": {
            "type": "tool",
            "tool": "read",
            "state": {
                "status": "completed",
                "title": "read",
                "input": {"path": "src/summary.py"},
                "output": "Read file src/summary.py, lines 1-12\nSECRET_DIRECT_COUNT_CONTENT",
                "metadata": {"metadata": {"lineCount": 12, "truncated": False}},
            },
        },
    }

    no_count_event = {
        "type": "tool_use",
        "timestamp": 5,
        "sessionID": "ses_read",
        "part": {
            "type": "tool",
            "tool": "read",
            "state": {
                "status": "completed",
                "title": "read",
                "input": {"path": "src/no-metadata.py"},
                "output": "Read file src/no-metadata.py, lines 1-2",
                "metadata": {"metadata": {"truncated": False}},
            },
        },
    }

    raw_output, visible, runs = _run_opencode_events(
        loop_fixture,
        [event, zero_directory_event, media_event, direct_metadata_event, no_count_event],
    )

    assert "✓ Read src/private.py (read)\n  └ 120 lines" in visible
    assert "✓ Read empty-directory (read)\n  └ 0 entries" in visible
    assert "✓ Inspect image metadata (read)\n  └ assets/diagram.png" in visible
    assert "✓ Read src/summary.py (read)\n  └ 12 lines" in visible
    assert "99 lines" not in visible
    no_count_block = next(block for block in visible.split("\n\n") if "src/no-metadata.py" in block)
    assert no_count_block == "✓ Read src/no-metadata.py (read)"
    for label in ("target:", "lines:", "entries:", "count:", "not provided"):
        assert label not in visible
    for private_content in (
        "SECRET_FILE_CONTENT",
        "SECRET_DIRECT_COUNT_CONTENT",
        "SECRET_IMAGE_CONTENT",
        "data:image/png;base64,SECRET",
    ):
        assert private_content not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")
    assert b"SECRET_FILE_CONTENT" in log.read_bytes()
    assert b"SECRET_DIRECT_COUNT_CONTENT" in log.read_bytes()
    assert b"SECRET_IMAGE_CONTENT" in log.read_bytes()


def test_opencode_reads_without_reliable_counts_still_hide_file_contents(
    loop_fixture: LoopFixture,
) -> None:
    event = {
        "type": "tool_use",
        "timestamp": 1,
        "sessionID": "ses_read_without_metadata",
        "part": {
            "type": "tool",
            "tool": "read",
            "state": {
                "status": "completed",
                "title": "read",
                "input": {"path": "src/no-count.py"},
                "output": "SECRET_CONTENT_WITHOUT_METADATA",
            },
        },
    }

    raw_output, visible, runs = _run_opencode_events(loop_fixture, [event])

    assert "src/no-count.py" in visible
    assert "SECRET_CONTENT_WITHOUT_METADATA" not in visible
    assert "lines:" not in visible
    assert b"SECRET_CONTENT_WITHOUT_METADATA" in raw_output.encode("utf-8")
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_does_not_present_a_truncated_read_count_as_a_total(
    loop_fixture: LoopFixture,
) -> None:
    event = {
        "type": "tool_use",
        "timestamp": 1,
        "sessionID": "ses_truncated_read",
        "part": {
            "type": "tool",
            "tool": "read",
            "state": {
                "status": "completed",
                "title": "read",
                "input": {"path": "src/large.py"},
                "output": "SECRET_TRUNCATED_READ_CONTENT",
                "metadata": {
                    "metadata": {
                        "display": {
                            "type": "file",
                            "totalLines": 999,
                            "truncated": True,
                        }
                    }
                },
            },
        },
    }

    raw_output, visible, runs = _run_opencode_events(loop_fixture, [event])

    assert "src/large.py" in visible
    assert "999 lines" not in visible
    assert "SECRET_TRUNCATED_READ_CONTENT" not in visible
    assert b"SECRET_TRUNCATED_READ_CONTENT" in raw_output.encode("utf-8")
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_unsupported_read_shapes_keep_the_generic_result_fallback(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_read_fallback",
            "part": {
                "type": "tool",
                "tool": "read",
                "state": {
                    "status": "completed",
                    "title": "Inspect unsupported read",
                    "input": {"path": "src/private.py", "mode": "preview"},
                    "output": "Unsupported read shape keeps this result visible.",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_read_fallback",
            "part": {
                "type": "tool",
                "tool": "read_extension",
                "state": {
                    "status": "completed",
                    "title": "Inspect custom reader",
                    "input": {"path": "src/custom.py"},
                    "output": "Custom read output stays visible.",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_read_fallback",
            "part": {
                "type": "tool",
                "tool": "read",
                "state": {
                    "status": "completed",
                    "title": "Inspect legacy read shape",
                    "input": {"filePath": "src/legacy.py"},
                    "output": "Unsupported read alias keeps this result visible.",
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✓ Inspect unsupported read (read)" in visible
    assert '{"path":"src/private.py","mode":"preview"}' in visible
    assert "Unsupported read shape keeps this result visible." in visible
    assert "✓ Inspect custom reader (read_extension)" in visible
    assert "Custom read output stays visible." in visible
    assert "✓ Inspect legacy read shape (read)" in visible
    assert '{"filePath":"src/legacy.py"}' in visible
    assert "Unsupported read alias keeps this result visible." in visible
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_successful_searches_show_natural_filters_and_reliable_counts(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_search",
            "part": {
                "type": "tool",
                "tool": "grep",
                "state": {
                    "status": "completed",
                    "title": "grep",
                    "input": {
                        "pattern": "NEEDLE_QUERY",
                        "path": "lib/",
                        "include": "*.py",
                        "literal": True,
                        "caseSensitive": False,
                        "limit": 10,
                    },
                    "output": "Found 2 matches\nSECRET_MATCH_ONE\nSECRET_MATCH_TWO",
                    "metadata": {"metadata": {"matches": 2, "truncated": False}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_search",
            "part": {
                "type": "tool",
                "tool": "glob",
                "state": {
                    "status": "completed",
                    "title": "glob",
                    "input": {
                        "pattern": "empty/**",
                        "path": "workspace/empty",
                        "hidden": True,
                    },
                    "output": "No files found",
                    "metadata": {"metadata": {"count": 0, "truncated": False}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_search",
            "part": {
                "type": "tool",
                "tool": "glob",
                "state": {
                    "status": "completed",
                    "title": "glob",
                    "input": {"pattern": "src/**/*.py", "path": "workspace"},
                    "output": "Found 17 matches\nSECRET_GLOB_MATCH_ONE\nSECRET_GLOB_MATCH_TWO",
                    "metadata": {"metadata": {"truncated": False}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 4,
            "sessionID": "ses_search",
            "part": {
                "type": "tool",
                "tool": "grep",
                "state": {
                    "status": "completed",
                    "title": "grep",
                    "input": {"pattern": "capped-query"},
                    "output": "Found 5 matches\nSECRET_CAPPED_MATCH",
                    "metadata": {"metadata": {"matches": 5, "truncated": True}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 5,
            "sessionID": "ses_search",
            "part": {
                "type": "tool",
                "tool": "websearch",
                "state": {
                    "status": "completed",
                    "title": "websearch",
                    "input": {"query": "OpenCode JSON event schema"},
                    "output": "## [Result title](https://example.test)\nSECRET_WEB_RESULT",
                    "metadata": {"metadata": {"provider": "fixture"}},
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert (
        "✓ Search text (grep)\n"
        "  ├ for NEEDLE_QUERY\n"
        "  ├ in lib/\n"
        "  ├ files matching *.py\n"
        "  ├ literal text\n"
        "  ├ case-insensitive\n"
        "  ├ up to 10 matches\n"
        "  └ 2 matches"
    ) in visible
    assert (
        "✓ Find files (glob)\n"
        "  ├ empty/**\n"
        "  ├ in workspace/empty\n"
        "  ├ including hidden files\n"
        "  └ 0 files"
    ) in visible
    assert "workspace" in visible
    assert "src/**/*.py" in visible
    assert "17 files" not in visible
    assert "Found 2 matches" not in visible
    assert "Found 17 matches" not in visible
    assert "count:" not in visible
    assert "not provided" not in visible
    assert "more than 5 matches" in visible
    assert "✓ Search the web (websearch)\n  └ for OpenCode JSON event schema" in visible
    assert "https://example.test" not in visible
    assert "OpenCode JSON event schema:" not in visible
    for match in (
        "SECRET_MATCH_ONE",
        "SECRET_MATCH_TWO",
        "SECRET_GLOB_MATCH_ONE",
        "SECRET_GLOB_MATCH_TWO",
        "SECRET_CAPPED_MATCH",
        "SECRET_WEB_RESULT",
    ):
        assert match not in visible
        assert match.encode("utf-8") in raw_output.encode("utf-8")
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")


def test_opencode_unsupported_search_shapes_keep_the_generic_result_fallback(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_search_fallback",
            "part": {
                "type": "tool",
                "tool": "grep",
                "state": {
                    "status": "completed",
                    "title": "Inspect unsupported grep",
                    "input": {"query": "not the verified pattern field"},
                    "output": "Found 1 match\nUNSUPPORTED_GREP_RESULT",
                    "metadata": {"metadata": {"matches": 1}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_search_fallback",
            "part": {
                "type": "tool",
                "tool": "grep_extension",
                "state": {
                    "status": "completed",
                    "title": "Inspect custom grep",
                    "input": {"pattern": "custom schema", "path": "src"},
                    "output": "CUSTOM_GREP_RESULT",
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✓ Inspect unsupported grep (grep)" in visible
    assert '{"query":"not the verified pattern field"}' in visible
    assert "Found 1 match" in visible
    assert "UNSUPPORTED_GREP_RESULT" in visible
    assert "✓ Inspect custom grep (grep_extension)" in visible
    assert "CUSTOM_GREP_RESULT" in visible
    assert "count: not provided" not in visible
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_running_subagent_shows_session_without_output_preview(
    loop_fixture: LoopFixture,
) -> None:
    fixture = Path(__file__).parent / "fixtures/running-subagent.json"
    event = json.loads(fixture.read_text())

    raw_output, visible, runs = _run_opencode_events(loop_fixture, [event])

    assert (
        "✓ Inspect the fixture (subagent: explore)\n"
        "  └ Running in background · session: ses_fixture_child"
    ) in visible
    assert "Background task launched" not in visible
    assert "Do not duplicate the work" not in visible
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")
    assert "Do not duplicate the work" in raw_output


def test_opencode_running_summary_uses_returned_state_not_input_or_output(
    loop_fixture: LoopFixture,
) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/running-subagent.json").read_text())
    events = []
    for index, status in enumerate(("completed", "success", "succeeded")):
        event = deepcopy(fixture)
        state = event["part"]["state"]
        state["status"] = status
        state["input"]["description"] = f"Background case {index}"
        if index == 0:
            del state["input"]["background"]
        elif index == 1:
            state["input"]["background"] = False
        else:
            state["input"]["extra"] = "unfamiliar input field"
        state["output"] = f"UNRELATED_OUTPUT_{index}"
        state["metadata"]["metadata"]["sessionId"] = f"ses_fixture_{index}"
        events.append(event)

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    for index in range(3):
        assert (
            f"✓ Background case {index} (subagent: explore)\n"
            f"  └ Running in background · session: ses_fixture_{index}"
        ) in visible
        assert f"UNRELATED_OUTPUT_{index}" not in visible
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_running_summary_preserves_unrecognized_results_and_failures(
    loop_fixture: LoopFixture,
) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/running-subagent.json").read_text())
    cases = (
        ("other_tool", "tool", "task"),
        ("non_success", "call_status", "running"),
        ("error_status", "call_status", "error"),
        ("explicit_error", "error", "EXPLICIT_FAILURE"),
        ("completed", "status", "completed"),
        ("missing_status", "status", None),
        ("missing_session", "sessionId", None),
        ("empty_session", "sessionId", ""),
        ("blank_session", "sessionId", "  "),
        ("multiline_session", "sessionId", "ses_bad\nline"),
        ("invalid_session", "sessionId", 42),
        ("missing_truncation", "truncated", None),
        ("truncated", "truncated", True),
        ("invalid_truncation", "truncated", "false"),
        ("numeric_truncation", "truncated", 0),
        ("missing_nested", "nested", None),
    )
    events = []
    for label, field, value in cases:
        event = deepcopy(fixture)
        part = event["part"]
        state = part["state"]
        state["input"]["description"] = f"Case {label}"
        state["output"] = f"VISIBLE_OUTPUT_{label}"
        nested = state["metadata"]["metadata"]
        if field == "tool":
            part["tool"] = value
        elif field == "call_status":
            state["status"] = value
        elif field == "error":
            state["error"] = value
        elif field == "nested":
            # Top-level fields must not substitute for the nested record.
            state["metadata"] = nested
        elif value is None:
            del nested[field]
        else:
            nested[field] = value
        events.append(event)

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    for label, _, _ in cases:
        assert f"VISIBLE_OUTPUT_{label}" in visible, label
    assert "Running in background · session:" not in visible
    assert "✗ Case explicit_error (subagent: explore)" in visible
    assert "EXPLICIT_FAILURE" in visible
    assert "✗ Case error_status (subagent: explore)" in visible
    assert "OpenCode tool event" in visible
    assert (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_identifies_parent_level_delegation_and_bounds_returned_results(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_delegation",
            "part": {
                "type": "tool",
                "tool": "task",
                "state": {
                    "status": "completed",
                    "title": "Inspect parser behavior",
                    "input": {
                        "description": "Inspect parser behavior",
                        "subagent_type": "explore",
                        "task_id": "ses_child_123",
                        "prompt": "A private task prompt that must not be displayed",
                    },
                    "output": "task_id: ses_child_123\n<task_result>\n"
                    "finding-1\nfinding-2\nfinding-3\nfinding-4\n"
                    "finding-5\nfinding-6\nfinding-7\n</task_result>",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_delegation",
            "part": {
                "type": "tool",
                "tool": "subagent",
                "state": {
                    "status": "completed",
                    "title": "subagent",
                    "input": {
                        "description": "Review the failure path",
                        "agent": "reviewer",
                        "sessionID": "ses_review_child",
                        "prompt": "A private subagent prompt that must not be displayed",
                    },
                    "output": '<subagent sessionID="ses_review_child" state="completed">\n'
                    "A second parent-level delegated result.\n</subagent>",
                    "metadata": {
                        "metadata": {
                            "status": "completed",
                            "sessionId": "ses_review_child",
                        }
                    },
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_delegation",
            "part": {
                "type": "tool",
                "tool": "job_dispatcher",
                "state": {
                    "status": "completed",
                    "title": "Run delegated work",
                    "input": {
                        "description": "Inspect the queue worker",
                        "prompt": "A private explicit-delegation prompt",
                    },
                    "output": "Queue worker finding.",
                    "metadata": {
                        "metadata": {
                            "delegated": True,
                            "agent_name": "queue-agent",
                            "sessionID": "ses_queue_child",
                        }
                    },
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 4,
            "sessionID": "ses_delegation",
            "part": {
                "type": "tool",
                "tool": "invoke_worker",
                "state": {
                    "status": "completed",
                    "title": "Review the failure path",
                    "input": {"description": "Review the failure path"},
                    "output": "Ambiguous worker output stays visible.",
                    "metadata": {
                        "metadata": {
                            "agent_type": "reviewer",
                            "sessionId": "ses_ambiguous_child",
                        }
                    },
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 5,
            "sessionID": "ses_delegation",
            "part": {
                "type": "tool",
                "tool": "subagent",
                "state": {
                    "status": "completed",
                    "title": "subagent",
                    "input": {
                        "description": "Check missing agent identity",
                        "prompt": "A private prompt without agent identity",
                    },
                    "output": '<subagent sessionID="ses_no_agent" state="completed">\n'
                    "Result without agent identity.\n</subagent>",
                    "metadata": {"metadata": {"sessionID": "ses_no_agent"}},
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 6,
            "sessionID": "ses_delegation",
            "part": {
                "type": "tool",
                "tool": "subagent",
                "state": {
                    "status": "completed",
                    "title": "Inspect malformed subagent result",
                    "input": {
                        "description": "Inspect malformed subagent result",
                        "agent": "explore",
                        "prompt": "A private unsupported-schema prompt",
                        "extra": "not in the verified input schema",
                    },
                    "output": "Unsupported subagent result stays visible.",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 7,
            "sessionID": "ses_delegation",
            "part": {
                "type": "tool",
                "tool": "workflow_runner",
                "state": {
                    "status": "completed",
                    "title": "Run a task",
                    "input": {"description": "Run a task", "task_id": "not-enough-evidence"},
                    "output": "Ordinary generic output.",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 8,
            "sessionID": "ses_delegation",
            "part": {
                "type": "tool",
                "tool": "task",
                "state": {
                    "status": "completed",
                    "title": "task",
                    "input": {
                        "description": "Preserve task result text",
                        "subagent_type": "explore",
                        "task_id": "ses_task_456",
                        "prompt": "A private task result-body prompt",
                    },
                    "output": "<task_result>\n"
                    "task_id: This is a useful finding.\n"
                    "A second finding.\n</task_result>",
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✓ Inspect parser behavior (task: explore)" in visible
    assert "finding-1" in visible
    assert "finding-5" in visible
    assert "finding-6" not in visible
    assert "finding-7" not in visible
    assert "... 2 additional line(s) omitted" in visible
    assert (
        "✓ Review the failure path (subagent: reviewer)\n"
        "  └ A second parent-level delegated result."
    ) in visible
    assert (
        "✓ Inspect the queue worker (job_dispatcher: queue-agent)\n  └ Queue worker finding."
        in visible
    )
    assert "✓ Review the failure path (invoke_worker)" in visible
    assert "Ambiguous worker output stays visible." in visible
    assert "Delegated: Review the failure path" not in visible
    assert (
        "✓ Check missing agent identity (subagent)\n  └ Result without agent identity." in visible
    )
    assert "✓ Inspect malformed subagent result (subagent)" in visible
    assert "Unsupported subagent result stays visible." in visible
    assert "A second parent-level delegated result." in visible
    assert "✓ Run a task (workflow_runner)" in visible
    assert "tool: workflow_runner" not in visible
    assert "Delegated: Run a task" not in visible
    assert (
        "✓ Preserve task result text (task: explore)\n"
        "  │ task_id: This is a useful finding.\n"
        "  └ A second finding."
    ) in visible
    for private_value in (
        "A private task prompt",
        "A private subagent prompt",
        "A private explicit-delegation prompt",
        "A private prompt without agent identity",
        "A private task result-body prompt",
        "ses_child_123",
        "ses_review_child",
        "ses_queue_child",
        "ses_no_agent",
        "ses_task_456",
        "<task_result>",
        "<subagent ",
        "agent: explore",
        "task id:",
        "session:",
    ):
        assert private_value not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")
    assert b"finding-7" in log.read_bytes()
    assert b"A private task prompt" in log.read_bytes()
    assert b"ses_review_child" in log.read_bytes()


def test_opencode_exploration_and_delegation_failures_keep_full_error_details(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_failed_exploration",
            "part": {
                "type": "tool",
                "tool": "read",
                "state": {
                    "status": "error",
                    "title": "Read failed",
                    "input": {"path": "src/unavailable.py"},
                    "error": "READ_ERROR_DETAIL",
                    "output": "\n".join(f"READ_OUTPUT_LINE_{line}" for line in range(1, 7)),
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_failed_exploration",
            "part": {
                "type": "tool",
                "tool": "grep",
                "state": {
                    "status": "error",
                    "title": "Search failed",
                    "input": {"pattern": "private-pattern", "path": "src"},
                    "error": "SEARCH_ERROR_DETAIL",
                    "output": "\n".join(f"SEARCH_OUTPUT_LINE_{line}" for line in range(1, 7)),
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_failed_exploration",
            "part": {
                "type": "tool",
                "tool": "task",
                "state": {
                    "status": "error",
                    "title": "Delegated task failed",
                    "input": {
                        "description": "Investigate delegated failure",
                        "subagent_type": "explore",
                        "task_id": "ses_failed_child",
                        "prompt": "A private failed delegation prompt",
                    },
                    "error": "DELEGATION_ERROR_LINE_1\nDELEGATION_ERROR_LINE_2\n"
                    "DELEGATION_ERROR_LINE_3\nDELEGATION_ERROR_LINE_4\n"
                    "DELEGATION_ERROR_LINE_5\nDELEGATION_ERROR_LINE_6",
                    "output": "\n".join(f"DELEGATION_OUTPUT_LINE_{line}" for line in range(1, 7)),
                },
            },
        },
    ]

    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)

    assert "✗ Read failed (read)" in visible
    assert "READ_ERROR_DETAIL" in visible
    assert "✗ Search failed (grep)" in visible
    assert "SEARCH_ERROR_DETAIL" in visible
    assert "✗ Investigate delegated failure (task: explore)" in visible
    for line in range(1, 7):
        assert f"DELEGATION_ERROR_LINE_{line}" in visible
        assert f"READ_OUTPUT_LINE_{line}" in visible
        assert f"SEARCH_OUTPUT_LINE_{line}" in visible
        assert f"DELEGATION_OUTPUT_LINE_{line}" in visible
    assert "ses_failed_child" not in visible
    assert "A private failed delegation prompt" not in visible
    assert "additional line(s) omitted" not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")


def _run_opencode_events(
    loop_fixture: LoopFixture,
    events: list[dict[str, object]],
    *,
    extra_env: dict[str, str] | None = None,
) -> tuple[str, str, list[Path]]:
    raw_output = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()
    result, runs = loop_fixture.run_loop("--max-iterations", "1", extra_env=extra_env)
    assert result.returncode == 0, result.stderr.decode()
    return raw_output, result.stdout.decode("utf-8"), runs


def test_opencode_shell_success_shows_command_and_compact_output(loop_fixture: LoopFixture) -> None:
    outputs = (
        ("Empty result", "printf ''", ""),
        ("Short result", "printf short", "short-1\nshort-2\nshort-3"),
        (
            "Five-line result",
            "printf five",
            "five-1\nfive-2\nfive-3\nfive-4\nfive-5",
        ),
        (
            "Long result",
            "printf long",
            "long-1\nlong-2\nlong-3\nlong-4\nlong-5\nlong-6\nlong-7",
        ),
        (
            "Multiline command",
            "printf first\nprintf second",
            "multiline-1\n\nmultiline-3",
        ),
    )
    events = [
        {
            "type": "tool_use",
            "timestamp": index,
            "sessionID": "ses_shell",
            "part": {
                "type": "tool",
                "tool": "shell",
                "state": {
                    "status": "completed",
                    "title": title,
                    "input": {"command": command},
                    "output": output,
                },
            },
        }
        for index, (title, command, output) in enumerate(outputs, start=1)
    ]
    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)
    for title, command, _ in outputs:
        assert f"✓ {title} (shell)" in visible
        rendered_command = command.replace("\n", "\n  │ ")
        assert f"  │ {rendered_command}" in visible
    assert [
        block[block.index("✓ ") :] for block in visible.split("\n\n") if " (shell)" in block
    ] == (
        [
            "✓ Empty result (shell)\n  │ printf ''\n  └ No output",
            "✓ Short result (shell)\n  │ printf short\n  │ short-1\n  │ short-2\n  └ short-3",
            "✓ Five-line result (shell)\n"
            "  │ printf five\n"
            "  │ five-1\n"
            "  │ five-2\n"
            "  │ five-3\n"
            "  │ five-4\n"
            "  └ five-5",
            "✓ Long result (shell)\n"
            "  │ printf long\n"
            "  ├ long-1\n"
            "  │ long-2\n"
            "  │ long-3\n"
            "  │ long-4\n"
            "  │ long-5\n"
            "  └ ... 2 additional line(s) omitted",
            "✓ Multiline command (shell)\n"
            "  │ printf first\n"
            "  │ printf second\n"
            "  │ multiline-1\n"
            "  │ \n"
            "  └ multiline-3",
        ]
    )
    assert "  └ No output" in visible
    for line in ("short-1", "short-2", "short-3", "five-1", "five-5"):
        assert line in visible
    for line in ("long-1", "long-5"):
        assert line in visible
    assert "long-6" not in visible
    assert "long-7" not in visible
    assert visible.count("additional line(s) omitted") == 1
    assert "... 2 additional line(s) omitted" in visible
    assert "tool:" not in visible
    assert "command:" not in visible
    assert "output:" not in visible
    assert "input:" not in visible
    assert '"command":' not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")
    assert b"long-6" in log.read_bytes()


def test_opencode_shell_failures_show_full_details_and_generic_fallbacks(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_shell_failure",
            "part": {
                "type": "tool",
                "tool": "shell",
                "state": {
                    "status": "error",
                    "title": "Failed shell command",
                    "input": {"command": "git status --short"},
                    "error": "failure-1\nfailure-2\nfailure-3\nfailure-4\nfailure-5\nfailure-6",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_shell_failure",
            "part": {
                "type": "tool",
                "tool": "shell",
                "state": {
                    "status": "error",
                    "input": {"workdir": "/workspace"},
                    "error": "No command field is available.",
                },
            },
        },
        {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_shell_failure",
            "part": {
                "type": "tool",
                "tool": "shell",
                "state": {"status": "error", "error": "No input details are available."},
            },
        },
    ]
    raw_output, visible, runs = _run_opencode_events(loop_fixture, events)
    assert "✗ Failed shell command (shell)" in visible
    assert "  │ git status --short" in visible
    for line in range(1, 7):
        assert f"failure-{line}" in visible
    assert '  │ {"workdir":"/workspace"}' in visible
    assert "No command field is available." in visible
    assert "✗ Run command (shell)" in visible
    assert "No input details are available." in visible
    assert "tool:" not in visible
    assert "command:" not in visible
    assert "input:" not in visible
    assert "output:" not in visible
    assert "additional line(s) omitted" not in visible
    log = runs[0] / "attempt-1-ticket-2/agent.log"
    assert log.read_bytes() == raw_output.encode("utf-8")


def test_opencode_failed_tools_and_run_errors_show_available_details(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_failure",
            "part": {
                "type": "tool",
                "tool": "failing_tool",
                "state": {
                    "status": "error",
                    "input": {"action": "inspect"},
                    "error": "first error line\nsecond error line\nthird error line\n"
                    "fourth error line\nfifth error line\nsixth error line",
                    "title": "Failed inspection",
                    "output": "output-1\noutput-2\noutput-3\noutput-4\noutput-5\noutput-6",
                },
            },
        },
        {
            "type": "error",
            "timestamp": 2,
            "sessionID": "ses_failure",
            "error": {"type": "provider.error", "message": "Run-level failure detail."},
        },
    ]
    raw_output = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    assert result.returncode == 0, result.stderr.decode()
    visible = result.stdout.decode("utf-8")
    assert "✗ Failed inspection (failing_tool)" in visible
    assert '  │ {"action":"inspect"}' in visible
    assert (
        "✗ Failed inspection (failing_tool)\n"
        '  │ {"action":"inspect"}\n'
        "  │ Error: first error line\n"
        "  │ second error line\n"
        "  │ third error line\n"
        "  │ fourth error line\n"
        "  │ fifth error line\n"
        "  │ sixth error line\n"
        "  │ output-1\n"
        "  │ output-2\n"
        "  │ output-3\n"
        "  │ output-4\n"
        "  │ output-5\n"
        "  └ output-6"
    ) in visible
    assert "Error: first error line" in visible
    assert "sixth error line" in visible
    assert "output-1" in visible
    assert "output-6" in visible
    assert "additional line(s) omitted" not in visible
    assert "tool:" not in visible
    assert "input:" not in visible
    assert "error:" not in visible
    assert "output:" not in visible
    assert "✗ OpenCode error" in visible
    assert "Run-level failure detail." in visible
    attempt = runs[0] / "attempt-1-ticket-2"
    assert (attempt / "agent.log").read_bytes() == raw_output.encode("utf-8")


def test_opencode_stream_handles_split_unicode_multiple_records_and_final_line(
    loop_fixture: LoopFixture,
) -> None:
    first_event = {
        "type": "text",
        "timestamp": 1,
        "sessionID": "ses_split",
        "part": {"type": "text", "text": "First complete block"},
    }
    large_event = {
        "type": "text",
        "timestamp": 2,
        "sessionID": "ses_split",
        "part": {"type": "text", "text": "雪"},
    }
    step_event = {
        "type": "step_start",
        "timestamp": 0,
        "sessionID": "ses_split",
        "part": {
            "id": "prt_step",
            "sessionID": "ses_split",
            "messageID": "msg_split",
            "type": "step-start",
            "snapshot": "step snapshot",
        },
    }
    reasoning_event = {
        "type": "reasoning",
        "timestamp": 0,
        "sessionID": "ses_split",
        "part": {
            "id": "prt_reasoning",
            "sessionID": "ses_split",
            "messageID": "msg_split",
            "type": "reasoning",
            "text": "hidden reasoning",
            "time": {"start": 0, "end": 0},
        },
    }
    finish_event = {
        "type": "step_finish",
        "timestamp": 3,
        "sessionID": "ses_split",
        "part": {
            "id": "prt_finish",
            "sessionID": "ses_split",
            "messageID": "msg_split",
            "type": "step-finish",
            "reason": "stop",
            "snapshot": "step snapshot",
            "cost": 0,
            "tokens": {},
        },
    }

    def encode_stream(text: str) -> bytes:
        large_event["part"]["text"] = text
        records = [step_event, reasoning_event, finish_event, first_event, large_event]
        lines = [json.dumps(event, ensure_ascii=False, separators=(",", ":")) for event in records]
        final_tool = {
            "type": "tool_use",
            "timestamp": 3,
            "sessionID": "ses_split",
            "part": {
                "type": "tool",
                "tool": "final_tool",
                "state": {
                    "status": "completed",
                    "title": "Final unterminated event",
                    "output": "unterminated result rendered",
                },
            },
        }
        return ("\n".join([*lines, json.dumps(final_tool, separators=(",", ":"))])).encode("utf-8")

    long_text = "雪"
    raw_bytes = encode_stream(long_text)
    unicode_offset = raw_bytes.index("雪".encode())
    padding = (8191 - unicode_offset % 8192) % 8192
    long_text = "x" * padding + long_text
    raw_bytes = encode_stream(long_text)
    unicode_offset = raw_bytes.index("雪".encode())
    assert unicode_offset % 8192 == 8191
    split_at = unicode_offset + 1
    assert split_at == 8192
    raw_output = raw_bytes.decode("utf-8")
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()

    ready_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ready_listener.bind(("127.0.0.1", 0))
    ready_listener.listen()
    ready_listener.settimeout(LIVE_READY_TIMEOUT)
    env = dict(loop_fixture.env)
    env["IMPLEMENT_LOOP_TEST_OPENCODE_SPLIT_BYTES"] = str(split_at)
    env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
    process: subprocess.Popen[bytes] | None = None
    visible = bytearray()
    try:
        process = subprocess.Popen(
            [str(SCRIPT), "1", "--max-iterations", "1"],
            cwd=loop_fixture.repo,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        with ready_listener.accept()[0] as ready_connection:
            ready_connection.settimeout(LIVE_READY_TIMEOUT)
            assert ready_connection.recv(1) == b"R"
        loop_fixture._read_until_output(
            process.stdout.fileno(),
            visible,
            b"First complete block",
            timeout=LIVE_OUTPUT_TIMEOUT,
        )
        assert process.poll() is None, "harness exited before receiving the second chunk"
        assert "── Agent ──\nFirst complete block\n\n".encode() in visible
        assert visible.count("── Agent ──".encode()) == 1
        assert "雪".encode() not in visible
        attempts = list(
            (loop_fixture.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
        )
        assert len(attempts) == 1
        assert (attempts[0] / "agent.log").read_bytes() == raw_bytes[:split_at]

        assert process.stdin is not None
        process.stdin.write(b"x")
        process.stdin.flush()
        process.stdin.close()
        process.stdin = None
        remaining, _ = process.communicate(timeout=LIVE_READY_TIMEOUT)
        visible.extend(remaining)
        assert process.returncode == 0, bytes(visible).decode("utf-8", "replace")
        assert "雪".encode() in visible
        assert ("── Agent ──\n" + long_text + "\n\n").encode() in visible
        assert visible.count("── Agent ──".encode()) == 2
        assert b"hidden reasoning" not in visible
        assert b"OpenCode step_start event" not in visible
        assert b"OpenCode step_finish event" not in visible
        assert b"Final unterminated event" in visible
        assert b"unterminated result rendered" in visible
        assert (attempts[0] / "agent.log").read_bytes() == raw_bytes
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.write(b"x")
                    process.stdin.flush()
                except BrokenPipeError, OSError, ValueError:
                    pass
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=LIVE_READY_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
        ready_listener.close()


def test_opencode_final_unterminated_write_failure_does_not_fail_raw_logging(
    loop_fixture: LoopFixture,
) -> None:
    events = [
        {
            "type": "text",
            "timestamp": 1,
            "sessionID": "ses_pipe",
            "part": {"type": "text", "text": "Completed block before pipe closes"},
        },
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_pipe",
            "part": {
                "type": "tool",
                "tool": "final_tool",
                "state": {
                    "status": "completed",
                    "title": "Final unterminated result",
                    "output": "This block cannot reach the closed pipe.",
                },
            },
        },
    ]
    raw_output = (
        json.dumps(events[0], ensure_ascii=False, separators=(",", ":"))
        + "\n"
        + json.dumps(events[1], ensure_ascii=False, separators=(",", ":"))
    )
    raw_bytes = raw_output.encode("utf-8")
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()

    ready_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ready_listener.bind(("127.0.0.1", 0))
    ready_listener.listen()
    ready_listener.settimeout(LIVE_READY_TIMEOUT)
    env = dict(loop_fixture.env)
    env["IMPLEMENT_LOOP_TEST_OPENCODE_SPLIT_BYTES"] = str(len(raw_bytes))
    env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
    env["IMPLEMENT_LOOP_TEST_RESULT_MODE"] = "missing"
    process: subprocess.Popen[bytes] | None = None
    visible = bytearray()
    try:
        process = subprocess.Popen(
            [str(SCRIPT), "1", "--max-iterations", "1"],
            cwd=loop_fixture.repo,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        with ready_listener.accept()[0] as ready_connection:
            ready_connection.settimeout(LIVE_READY_TIMEOUT)
            assert ready_connection.recv(1) == b"R"
        loop_fixture._read_until_output(
            process.stdout.fileno(),
            visible,
            b"Completed block before pipe closes",
            timeout=LIVE_OUTPUT_TIMEOUT,
        )
        assert process.poll() is None, "harness exited before the fake released its stream"
        attempts = list(
            (loop_fixture.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
        )
        assert len(attempts) == 1
        agent_log = attempts[0] / "agent.log"
        assert agent_log.read_bytes() == raw_bytes

        process.stdout.close()
        process.stdout = None
        assert process.stdin is not None
        process.stdin.write(b"x")
        process.stdin.flush()
        process.stdin.close()
        process.stdin = None
        _, stderr = process.communicate(timeout=LIVE_READY_TIMEOUT)
        assert process.returncode != 0
        assert b"did not submit a required per-attempt completion report" in (stderr or b"")
        assert b"Unable to persist the complete OpenCode output log" not in (stderr or b"")
        process_record = json.loads((attempts[0] / "process.json").read_text())
        assert process_record["log_persisted"]
        assert process_record["stream_error"] is not None
        assert agent_log.read_bytes() == raw_bytes
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.write(b"x")
                    process.stdin.flush()
                except BrokenPipeError, OSError, ValueError:
                    pass
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=LIVE_READY_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        ready_listener.close()


def test_opencode_redirected_output_is_live_before_harness_exit(loop_fixture: LoopFixture) -> None:
    loop_fixture._assert_live_harness_output(terminal=False)


def test_opencode_terminal_output_is_live_before_harness_exit(loop_fixture: LoopFixture) -> None:
    loop_fixture._assert_live_harness_output(terminal=True)


def test_opencode_terminal_failed_tool_uses_red_cross(loop_fixture: LoopFixture) -> None:
    loop_fixture._assert_live_harness_output(terminal=True, include_failure=True)


def test_opencode_terminal_output_honors_no_color_presence(loop_fixture: LoopFixture) -> None:
    loop_fixture._assert_live_harness_output(terminal=True, no_color=True)


def test_invocation_from_checkout_subdirectory_uses_checkout_root(
    loop_fixture: LoopFixture,
) -> None:
    nested = loop_fixture.repo / "nested"
    nested.mkdir()
    (nested / "tracked.txt").write_text("tracked fixture\n")
    loop_fixture.git("add", "nested")
    loop_fixture.git("commit", "--quiet", "-m", "Add nested fixture")

    result, _ = loop_fixture.run_loop("--max-iterations", "1", cwd=nested)

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert state["tickets"]["2"]["state"] == "closed"
    assert loop_fixture.git("branch", "--show-current", cwd=nested) == "main"
    assert (loop_fixture.repo / "implementation-2.txt").exists()
