from __future__ import annotations

import json
import os
import resource
import signal
import socket
import stat
import subprocess
from pathlib import Path

from support import (
    LIVE_OUTPUT_FRAGMENT,
    LIVE_OUTPUT_TIMEOUT,
    LIVE_READY_TIMEOUT,
    SCRIPT,
    LoopTestCase,
)


class HarnessTests(LoopTestCase):
    def test_opencode_attempts_are_private_and_use_fresh_sessions(self) -> None:
        self.add_ticket(3)
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2", harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["opencode_calls"]], [2, 3])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(
            len({call["attempt_id"] for call in state["opencode_calls"]}),
            2,
        )
        for call in state["opencode_calls"]:
            args = call["args"]
            self.assertEqual(args[0], "run")
            self.assertIn("--standalone", args)
            self.assertEqual(args[args.index("--format") + 1], "json")
            self.assertNotIn("--continue", args)
            self.assertNotIn("--session", args)
            self.assertNotIn("--fork", args)
            self.assertTrue(call["result_initially_absent"])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "closed")
        self.assertTrue((runs[0] / "attempt-1-ticket-2/agent.log").is_file())
        self.assertTrue((runs[0] / "attempt-2-ticket-3/agent.log").is_file())

    def test_opencode_renders_completed_text_and_generic_tool_blocks(self) -> None:
        assistant_text = (
            "Assistant wording stays unchanged.\n\n**Assistant Markdown stays unchanged.**"
        )
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
        self.state["opencode_raw_output"] = raw_output
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "1", harness=None)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        expected_tool_block = (
            '✓ Inspect fixture (example_tool)\n  │ {"target":"the fixture"}\n  └ result line'
        )
        self.assertIn((assistant_text + "\n\n" + expected_tool_block).encode(), result.stdout)
        self.assertNotIn(b"Assistant\n", result.stdout)
        self.assertNotIn(b"tool: example_tool", result.stdout)
        self.assertNotIn(b"input:", result.stdout)
        self.assertNotIn(b"\x1b[", result.stdout)
        self.assertNotIn(b'"type": "tool_use"', result.stdout)
        self.assertEqual(result.stdout.count(b"OpenCode: github-copilot"), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertEqual((attempt / "agent.log").read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_unrecognized_and_malformed_output_stays_visible(self) -> None:
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
        self.state["opencode_raw_output"] = raw_output
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "1", harness=None)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"OpenCode event: future_event", result.stdout)
        self.assertIn(b"future details", result.stdout)
        self.assertIn(b"OpenCode tool event", result.stdout)
        self.assertNotIn("✓ Running action".encode(), result.stdout)
        self.assertNotIn("✓ Pending action".encode(), result.stdout)
        self.assertIn(b"OpenCode diagnostic", result.stdout)
        self.assertIn(b"OpenCode startup diagnostic", result.stdout)
        self.assertIn(b"Use tool (unknown tool)", result.stdout)
        self.assertIn(b"No output", result.stdout)
        self.assertEqual(result.stdout.count(b"OpenCode step_start event"), 3)
        self.assertEqual(result.stdout.count(b"OpenCode step_finish event"), 4)
        self.assertEqual(result.stdout.count(b"OpenCode reasoning event"), 5)
        self.assertNotIn(b"secret reasoning", result.stdout)
        self.assertIn(b"before?after", result.stdout)
        self.assertNotIn(b"Traceback", result.stdout + result.stderr)
        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertEqual((attempt / "agent.log").read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_generic_success_output_is_bounded_and_raw_record_is_complete(self) -> None:
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
        self.state["opencode_raw_output"] = raw_output
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "1", harness=None)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        visible = result.stdout.decode("utf-8")
        self.assertIn(
            '✓ Preview empty (unfamiliar_tool)\n  │ {"target":"Preview empty"}\n  └ No output',
            visible,
        )
        for index in range(1, 4):
            self.assertIn(f"three-{index}", visible)
        for index in range(1, 6):
            self.assertIn(f"five-{index}", visible)
            self.assertIn(f"seven-{index}", visible)
        self.assertNotIn("seven-6", visible)
        self.assertNotIn("seven-7", visible)
        self.assertIn("... 2 additional line(s) omitted", visible)
        self.assertIn("✓ Preview seven (unfamiliar_tool)", visible)
        self.assertIn('  │ {"target":"Preview seven"}', visible)
        self.assertNotIn("tool:", visible)
        self.assertNotIn("input:", visible)
        self.assertNotIn("output:", visible)
        attempt = runs[0] / "attempt-1-ticket-2"
        log = attempt / "agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertIn(b"seven-6", log.read_bytes())

    def test_opencode_generic_extension_result_uses_bounded_input_fallback(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events([event])

        self.assertIn("✓ Inspect browser response (extension.browser)", visible)
        block = next(
            block for block in visible.split("\n\n") if "Inspect browser response" in block
        )
        input_line = next(line for line in block.splitlines() if line.startswith("  │ "))
        input_summary = input_line.removeprefix("  │ ")
        self.assertLessEqual(len(input_summary), 240)
        self.assertTrue(input_summary.endswith("..."))
        self.assertIn("https://example.invalid", input_summary)
        self.assertIn("Browser result stays generic.", block)
        self.assertNotIn("tool:", block)
        self.assertNotIn("input:", block)
        self.assertNotIn("output:", block)
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(), raw_output.encode("utf-8")
        )

    def test_opencode_successful_skill_loads_suppress_instructions_and_keep_raw_event(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events([event])

        self.assertIn(
            "✓ Load Test-driven development instructions (skill)\n  └ Loaded tdd",
            visible,
        )
        self.assertNotIn("PRIVATE_SKILL_INSTRUCTIONS", visible)
        self.assertNotIn("PRIVATE_SKILL_REFERENCE", visible)
        self.assertNotIn("<skill_content", visible)
        self.assertNotIn('{"id":"tdd"}', visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"PRIVATE_SKILL_INSTRUCTIONS", log.read_bytes())
        self.assertIn(b"PRIVATE_SKILL_REFERENCE", log.read_bytes())

    def test_opencode_successful_questions_show_supplied_answers_and_keep_question_relationships(
        self,
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn(
            "✓ Choose a database (question)\n  └ Selected PostgreSQL",
            visible,
        )
        self.assertIn(
            "✓ Answer 3 questions (question)\n"
            "  ├ Databases: Which databases do you prefer?\n"
            "    Selected PostgreSQL, SQLite\n"
            "  ├ Constraints and risks: What must the migration preserve?\n"
            "    Keep the current table names; preserve naïve labels.\n"
            "  └ Region: Choose a deployment region\n"
            "    Selected EU West",
            visible,
        )
        self.assertIn(
            "✓ Share optional notes (question)\n  └ No answer supplied",
            visible,
        )
        self.assertIn(
            '✓ Submit a blank note (question)\n  └ ""',
            visible,
        )
        self.assertNotIn("PRIVATE_QUESTION_MODEL_TEXT", visible)
        self.assertNotIn("PRIVATE_MULTI_QUESTION_MODEL_TEXT", visible)
        self.assertNotIn("PRIVATE_NO_ANSWER_MODEL_TEXT", visible)
        self.assertNotIn("PRIVATE_EMPTY_STRING_QUESTION_OUTPUT", visible)
        self.assertNotIn("Relational database", visible)
        self.assertNotIn("Embedded database", visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"PRIVATE_QUESTION_MODEL_TEXT", log.read_bytes())
        self.assertIn(b"PRIVATE_MULTI_QUESTION_MODEL_TEXT", log.read_bytes())
        self.assertIn(b"PRIVATE_NO_ANSWER_MODEL_TEXT", log.read_bytes())
        self.assertIn(b"PRIVATE_EMPTY_STRING_QUESTION_OUTPUT", log.read_bytes())
        self.assertIn("preserve naïve labels".encode(), log.read_bytes())

    def test_opencode_partial_skill_and_question_schemas_keep_generic_results(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("✓ Use tool (skill)", visible)
        self.assertNotIn("Loaded incomplete-skill", visible)
        self.assertIn("PARTIAL_SKILL_RESULT", visible)
        self.assertIn("✓ Use tool (question)", visible)
        self.assertNotIn("Selected PostgreSQL", visible)
        self.assertIn("PARTIAL_QUESTION_RESULT", visible)
        self.assertIn("✓ Use tool (question)", visible)
        self.assertIn("UNSUPPORTED_QUESTION_RESULT", visible)
        self.assertIn("✓ Inspect extension question result (question_extension)", visible)
        self.assertIn("EXTENSION_QUESTION_RESULT", visible)
        self.assertIn("LONG_HEADER_QUESTION_RESULT", visible)
        self.assertIn("✓ Use tool (question)", visible)
        unsupported_answers = next(
            block for block in visible.split("\n\n") if "UNSUPPORTED_SINGLE_CHOICE_ANSWERS" in block
        )
        self.assertNotIn("Selected PostgreSQL, SQLite", unsupported_answers)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_incomplete_questions_do_not_get_success_summaries(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertNotIn("✓ Choose a pending database (question)", visible)
        self.assertNotIn("✓ Choose a cancelled region (question)", visible)
        self.assertNotIn("Selected PostgreSQL", visible)
        self.assertNotIn("Selected EU", visible)
        self.assertIn("OpenCode tool event", visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_failed_skill_and_question_calls_keep_full_evidence(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("✗ Load Test-driven development instructions (skill)", visible)
        self.assertNotIn("Loaded tdd", visible)
        self.assertIn("✗ Choose a database (question)", visible)
        for line in range(1, 7):
            self.assertIn(f"SKILL_ERROR_LINE_{line}", visible)
            self.assertIn(f"SKILL_OUTPUT_LINE_{line}", visible)
            self.assertIn(f"QUESTION_ERROR_LINE_{line}", visible)
            self.assertIn(f"QUESTION_OUTPUT_LINE_{line}", visible)
        self.assertNotIn("additional line(s) omitted", visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_tool_output_preserves_supplied_ansi_in_preview_and_raw_log(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events([event], extra_env={"NO_COLOR": ""})

        self.assertIn("✓ Show colored output (ansi_tool)", visible)
        self.assertIn(
            "  └ \x1b[31mred first line\n    red second line\x1b[0m",
            visible,
        )
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(), raw_output.encode("utf-8")
        )
        self.assertIn(b"\\u001b[31mred first line", raw_output.encode("utf-8"))

    def test_opencode_file_modification_results_hide_inputs_and_strip_patch_header(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("✓ Apply patch (patch)", visible)
        patch_block = next(block for block in visible.split("\n\n") if "(patch)" in block)
        for result_line in (
            "M src/settings.py",
            "A tests/test_settings.py",
            "M src/config.py",
            "D src/old_config.py",
            "A docs/settings.md",
        ):
            self.assertIn(result_line, patch_block)
        self.assertNotIn("Success. Updated the following files:", patch_block)
        self.assertNotIn("sixth-result.py", patch_block)
        self.assertIn("... 1 additional line(s) omitted", patch_block)
        diff_block = next(
            block for block in visible.split("\n\n") if "Show returned change" in block
        )
        for diff_line in (
            "--- a/src/returned.py",
            "+++ b/src/returned.py",
            "@@ -1 +1 @@",
            "-old",
            "+new",
        ):
            self.assertIn(diff_line, diff_block)
        self.assertIn("✓ Edit src/app.py (edit)", visible)
        self.assertIn("Edited src/app.py (1 replacement)", visible)
        self.assertIn("✓ Write src/new.py (write)", visible)
        self.assertIn("Wrote file successfully: src/new.py", visible)
        for private_input in (
            "SECRET_PATCH_INPUT",
            "SECRET_PATCH_DIFF",
            "SECRET_PATCH_FOR_RETURNED_DIFF",
            "SECRET_OLD_CONTENT",
            "SECRET_NEW_CONTENT",
            "SECRET_FILE_CONTENT",
        ):
            self.assertNotIn(private_input, visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        for private_input in (
            "SECRET_PATCH_INPUT",
            "SECRET_PATCH_DIFF",
            "SECRET_OLD_CONTENT",
            "SECRET_NEW_CONTENT",
            "SECRET_FILE_CONTENT",
        ):
            self.assertIn(private_input.encode("utf-8"), log.read_bytes())

    def test_opencode_file_and_webfetch_previews_preserve_supplied_ansi(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events, extra_env={"NO_COLOR": ""})

        self.assertIn("✓ Update colored file (patch)", visible)
        self.assertIn("  └ \x1b[35m+src/colored.py\x1b[0m", visible)
        self.assertIn("✓ Fetch colored docs (webfetch)", visible)
        self.assertIn("  │ https://example.test/colored", visible)
        self.assertIn("  └ \x1b[36m# Colored web title\x1b[0m", visible)
        self.assertNotIn("\x1b[32m", visible)
        self.assertNotIn("\x1b[1m", visible)
        agent_log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(agent_log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"\\u001b[35m", agent_log.read_bytes())
        self.assertIn(b"\\u001b[36m", agent_log.read_bytes())

    def test_opencode_webfetch_shows_url_and_bounded_content_preview(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        block = next(block for block in visible.split("\n\n") if "(webfetch)" in block)
        self.assertIn("✓ Fetch web content (webfetch)", block)
        self.assertIn(f"  │ {url}", block)
        for line in (
            "# asyncio — Asynchronous I/O",
            "Retrieved content line two",
            "Retrieved content line three",
            "Retrieved content line four",
            "Retrieved content line five",
        ):
            self.assertIn(line, block)
        self.assertNotIn("sixth content line", block)
        self.assertIn("... 1 additional line(s) omitted", block)
        self.assertIn("✓ Fetch web content (webfetch)", visible)
        self.assertIn("  │ https://example.test/empty\n  └ No output", visible)
        self.assertNotIn('"url":', visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"sixth content line", log.read_bytes())

    def test_opencode_file_and_webfetch_fallbacks_keep_results_without_guessing(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn('✓ Edit src/partial.py (edit)\n  │ {"path":"src/partial.py"}', visible)
        self.assertIn("Meaningful first edit result", visible)
        self.assertNotIn("SECRET_UNSUPPORTED_REPLACEMENT", visible)
        self.assertIn('  │ {"url":42,"format":"markdown"}', visible)
        self.assertIn('{"message":"Unsupported structured response remains visible"}', visible)
        self.assertNotIn("  │ 42", visible)
        extension_block = next(
            block for block in visible.split("\n\n") if "Inspect extension output" in block
        )
        self.assertIn("Success. Updated the following files:", extension_block)
        self.assertIn("Keep this unrelated first line", extension_block)
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(), raw_output.encode("utf-8")
        )

    def test_opencode_file_and_webfetch_failures_keep_full_error_details(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        action_titles = {
            "patch": "Apply patch",
            "edit": "Edit src/edit.py",
            "write": "Write src/write.py",
            "webfetch": "Fetch web content",
        }
        for tool_name, _ in failed_tools:
            block = next(block for block in visible.split("\n\n") if f"({tool_name})" in block)
            self.assertIn(f"✗ {action_titles[tool_name]} ({tool_name})", block)
            self.assertNotIn(f"✓ {action_titles[tool_name]}", block)
            for line in range(1, 7):
                self.assertIn(f"{tool_name}_error_{line}", block)
            self.assertNotIn("additional line(s) omitted", block)
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(), raw_output.encode("utf-8")
        )

    def test_opencode_failed_patch_keeps_any_available_output_unfiltered(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events([event])

        block = next(block for block in visible.split("\n\n") if "(patch)" in block)
        self.assertIn("✗ Apply patch (patch)", block)
        self.assertIn("Patch failed after a partial application.", block)
        self.assertIn("Success. Updated the following files:", block)
        for line in range(1, 7):
            self.assertIn(f"partial-result-{line}", block)
        self.assertNotIn("additional line(s) omitted", block)
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(), raw_output.encode("utf-8")
        )

    def test_opencode_successful_reads_show_targets_and_reliable_counts_only(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(
            [event, zero_directory_event, media_event, direct_metadata_event, no_count_event]
        )

        self.assertIn("✓ Read src/private.py (read)\n  └ 120 lines", visible)
        self.assertIn("✓ Read empty-directory (read)\n  └ 0 entries", visible)
        self.assertIn("✓ Inspect image metadata (read)\n  └ assets/diagram.png", visible)
        self.assertIn("✓ Read src/summary.py (read)\n  └ 12 lines", visible)
        self.assertNotIn("99 lines", visible)
        no_count_block = next(
            block for block in visible.split("\n\n") if "src/no-metadata.py" in block
        )
        self.assertEqual(no_count_block, "✓ Read src/no-metadata.py (read)")
        for label in ("target:", "lines:", "entries:", "count:", "not provided"):
            self.assertNotIn(label, visible)
        for private_content in (
            "SECRET_FILE_CONTENT",
            "SECRET_DIRECT_COUNT_CONTENT",
            "SECRET_IMAGE_CONTENT",
            "data:image/png;base64,SECRET",
        ):
            self.assertNotIn(private_content, visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"SECRET_FILE_CONTENT", log.read_bytes())
        self.assertIn(b"SECRET_DIRECT_COUNT_CONTENT", log.read_bytes())
        self.assertIn(b"SECRET_IMAGE_CONTENT", log.read_bytes())

    def test_opencode_reads_without_reliable_counts_still_hide_file_contents(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events([event])

        self.assertIn("src/no-count.py", visible)
        self.assertNotIn("SECRET_CONTENT_WITHOUT_METADATA", visible)
        self.assertNotIn("lines:", visible)
        self.assertIn(b"SECRET_CONTENT_WITHOUT_METADATA", raw_output.encode("utf-8"))
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(),
            raw_output.encode("utf-8"),
        )

    def test_opencode_does_not_present_a_truncated_read_count_as_a_total(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events([event])

        self.assertIn("src/large.py", visible)
        self.assertNotIn("999 lines", visible)
        self.assertNotIn("SECRET_TRUNCATED_READ_CONTENT", visible)
        self.assertIn(b"SECRET_TRUNCATED_READ_CONTENT", raw_output.encode("utf-8"))
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(),
            raw_output.encode("utf-8"),
        )

    def test_opencode_unsupported_read_shapes_keep_the_generic_result_fallback(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("✓ Inspect unsupported read (read)", visible)
        self.assertIn('{"path":"src/private.py","mode":"preview"}', visible)
        self.assertIn("Unsupported read shape keeps this result visible.", visible)
        self.assertIn("✓ Inspect custom reader (read_extension)", visible)
        self.assertIn("Custom read output stays visible.", visible)
        self.assertIn("✓ Inspect legacy read shape (read)", visible)
        self.assertIn('{"filePath":"src/legacy.py"}', visible)
        self.assertIn("Unsupported read alias keeps this result visible.", visible)
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(), raw_output.encode("utf-8")
        )

    def test_opencode_successful_searches_show_natural_filters_and_reliable_counts(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn(
            "✓ Search text (grep)\n"
            "  ├ for NEEDLE_QUERY\n"
            "  ├ in lib/\n"
            "  ├ files matching *.py\n"
            "  ├ literal text\n"
            "  ├ case-insensitive\n"
            "  ├ up to 10 matches\n"
            "  └ 2 matches",
            visible,
        )
        self.assertIn(
            "✓ Find files (glob)\n"
            "  ├ empty/**\n"
            "  ├ in workspace/empty\n"
            "  ├ including hidden files\n"
            "  └ 0 files",
            visible,
        )
        self.assertIn("workspace", visible)
        self.assertIn("src/**/*.py", visible)
        self.assertNotIn("17 files", visible)
        self.assertNotIn("Found 2 matches", visible)
        self.assertNotIn("Found 17 matches", visible)
        self.assertNotIn("count:", visible)
        self.assertNotIn("not provided", visible)
        self.assertIn("more than 5 matches", visible)
        self.assertIn("✓ Search the web (websearch)\n  └ for OpenCode JSON event schema", visible)
        self.assertNotIn("https://example.test", visible)
        self.assertNotIn("OpenCode JSON event schema:", visible)
        for match in (
            "SECRET_MATCH_ONE",
            "SECRET_MATCH_TWO",
            "SECRET_GLOB_MATCH_ONE",
            "SECRET_GLOB_MATCH_TWO",
            "SECRET_CAPPED_MATCH",
            "SECRET_WEB_RESULT",
        ):
            self.assertNotIn(match, visible)
            self.assertIn(match.encode("utf-8"), raw_output.encode("utf-8"))
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_unsupported_search_shapes_keep_the_generic_result_fallback(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("✓ Inspect unsupported grep (grep)", visible)
        self.assertIn('{"query":"not the verified pattern field"}', visible)
        self.assertIn("Found 1 match", visible)
        self.assertIn("UNSUPPORTED_GREP_RESULT", visible)
        self.assertIn("✓ Inspect custom grep (grep_extension)", visible)
        self.assertIn("CUSTOM_GREP_RESULT", visible)
        self.assertNotIn("count: not provided", visible)
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(), raw_output.encode("utf-8")
        )

    def test_opencode_identifies_parent_level_delegation_and_bounds_returned_results(self) -> None:
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

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("✓ Inspect parser behavior (task: explore)", visible)
        self.assertIn("finding-1", visible)
        self.assertIn("finding-5", visible)
        self.assertNotIn("finding-6", visible)
        self.assertNotIn("finding-7", visible)
        self.assertIn("... 2 additional line(s) omitted", visible)
        self.assertIn(
            "✓ Review the failure path (subagent: reviewer)\n"
            "  └ A second parent-level delegated result.",
            visible,
        )
        self.assertIn(
            "✓ Inspect the queue worker (job_dispatcher: queue-agent)\n  └ Queue worker finding.",
            visible,
        )
        self.assertIn("✓ Review the failure path (invoke_worker)", visible)
        self.assertIn("Ambiguous worker output stays visible.", visible)
        self.assertNotIn("Delegated: Review the failure path", visible)
        self.assertIn(
            "✓ Check missing agent identity (subagent)\n  └ Result without agent identity.",
            visible,
        )
        self.assertIn("✓ Inspect malformed subagent result (subagent)", visible)
        self.assertIn("Unsupported subagent result stays visible.", visible)
        self.assertIn("A second parent-level delegated result.", visible)
        self.assertIn("✓ Run a task (workflow_runner)", visible)
        self.assertNotIn("tool: workflow_runner", visible)
        self.assertNotIn("Delegated: Run a task", visible)
        self.assertIn(
            "✓ Preserve task result text (task: explore)\n"
            "  └ task_id: This is a useful finding.\n"
            "    A second finding.",
            visible,
        )
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
            self.assertNotIn(private_value, visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"finding-7", log.read_bytes())
        self.assertIn(b"A private task prompt", log.read_bytes())
        self.assertIn(b"ses_review_child", log.read_bytes())

    def test_opencode_exploration_and_delegation_failures_keep_full_error_details(self) -> None:
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
                        "output": "\n".join(
                            f"DELEGATION_OUTPUT_LINE_{line}" for line in range(1, 7)
                        ),
                    },
                },
            },
        ]

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("✗ Read failed (read)", visible)
        self.assertIn("READ_ERROR_DETAIL", visible)
        self.assertIn("✗ Search failed (grep)", visible)
        self.assertIn("SEARCH_ERROR_DETAIL", visible)
        self.assertIn("✗ Investigate delegated failure (task: explore)", visible)
        for line in range(1, 7):
            self.assertIn(f"DELEGATION_ERROR_LINE_{line}", visible)
            self.assertIn(f"READ_OUTPUT_LINE_{line}", visible)
            self.assertIn(f"SEARCH_OUTPUT_LINE_{line}", visible)
            self.assertIn(f"DELEGATION_OUTPUT_LINE_{line}", visible)
        self.assertNotIn("ses_failed_child", visible)
        self.assertNotIn("A private failed delegation prompt", visible)
        self.assertNotIn("additional line(s) omitted", visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))

    def _run_opencode_events(
        self,
        events: list[dict[str, object]],
        *,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[str, str, list[Path]]:
        raw_output = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
        self.state["opencode_raw_output"] = raw_output
        self.save_state()
        result, runs = self.run_loop("--max-iterations", "1", harness=None, extra_env=extra_env)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return raw_output, result.stdout.decode("utf-8"), runs

    def test_opencode_shell_success_shows_command_and_compact_output(self) -> None:
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
        raw_output, visible, runs = self._run_opencode_events(events)
        for title, command, _ in outputs:
            self.assertIn(f"✓ {title} (shell)", visible)
            self.assertIn(f"  │ {command}", visible)
        self.assertIn("  └ No output", visible)
        for line in ("short-1", "short-2", "short-3", "five-1", "five-5"):
            self.assertIn(line, visible)
        for line in ("long-1", "long-5"):
            self.assertIn(line, visible)
        self.assertNotIn("long-6", visible)
        self.assertNotIn("long-7", visible)
        self.assertEqual(visible.count("additional line(s) omitted"), 1)
        self.assertIn("... 2 additional line(s) omitted", visible)
        self.assertNotIn("tool:", visible)
        self.assertNotIn("command:", visible)
        self.assertNotIn("output:", visible)
        self.assertNotIn("input:", visible)
        self.assertNotIn('"command":', visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"long-6", log.read_bytes())

    def test_opencode_shell_failures_show_full_details_and_generic_fallbacks(self) -> None:
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
        raw_output, visible, runs = self._run_opencode_events(events)
        self.assertIn("✗ Failed shell command (shell)", visible)
        self.assertIn("  │ git status --short", visible)
        for line in range(1, 7):
            self.assertIn(f"failure-{line}", visible)
        self.assertIn('  │ {"workdir":"/workspace"}', visible)
        self.assertIn("No command field is available.", visible)
        self.assertIn("✗ Run command (shell)", visible)
        self.assertIn("No input details are available.", visible)
        self.assertNotIn("tool:", visible)
        self.assertNotIn("command:", visible)
        self.assertNotIn("input:", visible)
        self.assertNotIn("output:", visible)
        self.assertNotIn("additional line(s) omitted", visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_failed_tools_and_run_errors_show_available_details(self) -> None:
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
        self.state["opencode_raw_output"] = raw_output
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "1", harness=None)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        visible = result.stdout.decode("utf-8")
        self.assertIn("✗ Failed inspection (failing_tool)", visible)
        self.assertIn('  │ {"action":"inspect"}', visible)
        self.assertIn("Error: first error line", visible)
        self.assertIn("sixth error line", visible)
        self.assertIn("output-1", visible)
        self.assertIn("output-6", visible)
        self.assertNotIn("additional line(s) omitted", visible)
        self.assertNotIn("tool:", visible)
        self.assertNotIn("input:", visible)
        self.assertNotIn("error:", visible)
        self.assertNotIn("output:", visible)
        self.assertIn("✗ OpenCode error", visible)
        self.assertIn("Run-level failure detail.", visible)
        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertEqual((attempt / "agent.log").read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_stream_handles_split_unicode_multiple_records_and_final_line(self) -> None:
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
            lines = [
                json.dumps(event, ensure_ascii=False, separators=(",", ":")) for event in records
            ]
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
            return ("\n".join([*lines, json.dumps(final_tool, separators=(",", ":"))])).encode(
                "utf-8"
            )

        long_text = "雪"
        raw_bytes = encode_stream(long_text)
        unicode_offset = raw_bytes.index("雪".encode())
        padding = (8191 - unicode_offset % 8192) % 8192
        long_text = "x" * padding + long_text
        raw_bytes = encode_stream(long_text)
        unicode_offset = raw_bytes.index("雪".encode())
        self.assertEqual(unicode_offset % 8192, 8191)
        split_at = unicode_offset + 1
        self.assertEqual(split_at, 8192)
        raw_output = raw_bytes.decode("utf-8")
        self.state["opencode_raw_output"] = raw_output
        self.save_state()

        ready_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ready_listener.bind(("127.0.0.1", 0))
        ready_listener.listen()
        ready_listener.settimeout(LIVE_READY_TIMEOUT)
        env = dict(self.env)
        env["IMPLEMENT_LOOP_TEST_OPENCODE_SPLIT_BYTES"] = str(split_at)
        env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
        process: subprocess.Popen[bytes] | None = None
        visible = bytearray()
        try:
            process = subprocess.Popen(
                [str(SCRIPT), "1", "--max-iterations", "1"],
                cwd=self.repo,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            assert process.stdout is not None
            with ready_listener.accept()[0] as ready_connection:
                ready_connection.settimeout(LIVE_READY_TIMEOUT)
                self.assertEqual(ready_connection.recv(1), b"R")
            self._read_until_output(
                process.stdout.fileno(),
                visible,
                b"First complete block",
                timeout=LIVE_OUTPUT_TIMEOUT,
            )
            self.assertIsNone(process.poll(), "harness exited before receiving the second chunk")
            self.assertNotIn("雪".encode(), visible)
            attempts = list(
                (self.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
            )
            self.assertEqual(len(attempts), 1)
            self.assertEqual((attempts[0] / "agent.log").read_bytes(), raw_bytes[:split_at])

            assert process.stdin is not None
            process.stdin.write(b"x")
            process.stdin.flush()
            process.stdin.close()
            process.stdin = None
            remaining, _ = process.communicate(timeout=LIVE_READY_TIMEOUT)
            visible.extend(remaining)
            self.assertEqual(process.returncode, 0, bytes(visible).decode("utf-8", "replace"))
            self.assertIn("雪".encode(), visible)
            self.assertNotIn(b"hidden reasoning", visible)
            self.assertNotIn(b"OpenCode step_start event", visible)
            self.assertNotIn(b"OpenCode step_finish event", visible)
            self.assertIn(b"Final unterminated event", visible)
            self.assertIn(b"unterminated result rendered", visible)
            self.assertEqual((attempts[0] / "agent.log").read_bytes(), raw_bytes)
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.write(b"x")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError, ValueError):
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

    def test_opencode_final_unterminated_write_failure_does_not_fail_raw_logging(self) -> None:
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
        self.state["opencode_raw_output"] = raw_output
        self.save_state()

        ready_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ready_listener.bind(("127.0.0.1", 0))
        ready_listener.listen()
        ready_listener.settimeout(LIVE_READY_TIMEOUT)
        env = dict(self.env)
        env["IMPLEMENT_LOOP_TEST_OPENCODE_SPLIT_BYTES"] = str(len(raw_bytes))
        env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
        env["IMPLEMENT_LOOP_TEST_RESULT_MODE"] = "missing"
        process: subprocess.Popen[bytes] | None = None
        visible = bytearray()
        try:
            process = subprocess.Popen(
                [str(SCRIPT), "1", "--max-iterations", "1"],
                cwd=self.repo,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            assert process.stdout is not None
            with ready_listener.accept()[0] as ready_connection:
                ready_connection.settimeout(LIVE_READY_TIMEOUT)
                self.assertEqual(ready_connection.recv(1), b"R")
            self._read_until_output(
                process.stdout.fileno(),
                visible,
                b"Completed block before pipe closes",
                timeout=LIVE_OUTPUT_TIMEOUT,
            )
            self.assertIsNone(process.poll(), "harness exited before the fake released its stream")
            attempts = list(
                (self.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
            )
            self.assertEqual(len(attempts), 1)
            agent_log = attempts[0] / "agent.log"
            self.assertEqual(agent_log.read_bytes(), raw_bytes)

            process.stdout.close()
            process.stdout = None
            assert process.stdin is not None
            process.stdin.write(b"x")
            process.stdin.flush()
            process.stdin.close()
            process.stdin = None
            _, stderr = process.communicate(timeout=LIVE_READY_TIMEOUT)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn(b"did not write the required per-attempt result file", stderr or b"")
            self.assertNotIn(b"Unable to persist the complete OpenCode output log", stderr or b"")
            process_record = json.loads((attempts[0] / "process.json").read_text())
            self.assertTrue(process_record["log_persisted"])
            self.assertIsNotNone(process_record["stream_error"])
            self.assertEqual(agent_log.read_bytes(), raw_bytes)
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.write(b"x")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError, ValueError):
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

    def test_opencode_redirected_output_is_live_before_harness_exit(self) -> None:
        self._assert_live_harness_output("opencode", terminal=False)

    def test_opencode_terminal_output_is_live_before_harness_exit(self) -> None:
        self._assert_live_harness_output("opencode", terminal=True)

    def test_opencode_terminal_failed_tool_uses_red_cross(self) -> None:
        self._assert_live_harness_output("opencode", terminal=True, include_failure=True)

    def test_opencode_terminal_output_honors_no_color_presence(self) -> None:
        self._assert_live_harness_output("opencode", terminal=True, no_color=True)

    def test_copilot_redirected_output_is_live_before_harness_exit(self) -> None:
        self._assert_live_harness_output("copilot", terminal=False)

    def test_copilot_terminal_output_is_live_before_harness_exit(self) -> None:
        self._assert_live_harness_output("copilot", terminal=True)

    def _assert_signal_stops_run_without_alert(
        self, stop_signal: int, *, platform: str = "darwin", harness: str = "copilot"
    ) -> None:
        ready_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ready_listener.bind(("127.0.0.1", 0))
        ready_listener.listen()
        ready_listener.settimeout(LIVE_READY_TIMEOUT)
        env = dict(self.env)
        env.update(self.platform_test_environment(platform))
        env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_FRAGMENT"] = LIVE_OUTPUT_FRAGMENT.decode("ascii")
        env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
        process: subprocess.Popen[bytes] | None = None
        output = bytearray()

        try:
            process = subprocess.Popen(
                [
                    str(SCRIPT),
                    "1",
                    "--max-iterations",
                    "1",
                    "--harness",
                    harness,
                ],
                cwd=self.repo,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with ready_listener.accept()[0] as ready_connection:
                ready_connection.settimeout(LIVE_READY_TIMEOUT)
                self.assertEqual(ready_connection.recv(1), b"R")
            assert process.stdout is not None
            self._read_until_output(
                process.stdout.fileno(),
                output,
                LIVE_OUTPUT_FRAGMENT,
                timeout=LIVE_OUTPUT_TIMEOUT,
            )
            attempt_directories = list(
                (self.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
            )
            self.assertEqual(len(attempt_directories), 1)
            run_directory = attempt_directories[0].parent
            attempt_directory = attempt_directories[0]

            process.send_signal(stop_signal)
            assert process.stdin is not None
            process.stdin.write(b"x")
            process.stdin.flush()
            remaining, _ = process.communicate(timeout=LIVE_READY_TIMEOUT)
            output.extend(remaining)

            self.assertEqual(process.returncode, 130, output.decode("utf-8", errors="replace"))
            self.assertIn(b"Interrupted.", output)
            self.assertIn(f"Run records: {run_directory.resolve()}".encode(), output)
            self.assertIn(
                f"Ticket #2 records: {attempt_directory.resolve()}".encode(),
                output,
            )
            self.assertIn(
                b"This output, the records, and release of the local lock do not confirm shutdown",
                output,
            )
            self.assertTrue(run_directory.is_dir())
            self.assertTrue(attempt_directory.is_dir())
            self.assertFalse((run_directory / "active.pid").exists())
            self.assertFalse(self.repository_lock_path().exists())
            state = self.load_state()
            self.assertEqual(len(state[f"{harness}_calls"]), 1)
            other_harness = "opencode" if harness == "copilot" else "copilot"
            self.assertEqual(state[f"{other_harness}_calls"], [])
            agent_log = (attempt_directory / "agent.log").read_bytes()
            if harness == "opencode":
                self.assertEqual(json.loads(agent_log)["type"], "tool_use")
                self.assertIn(LIVE_OUTPUT_FRAGMENT, agent_log)
            else:
                self.assertEqual(agent_log, LIVE_OUTPUT_FRAGMENT)
            self.assertEqual(self.alert_calls(), [])
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.write(b"x")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError, ValueError):
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

    def test_ctrl_c_reports_existing_run_and_ticket_records_without_alert(self) -> None:
        self._assert_signal_stops_run_without_alert(signal.SIGINT)

    def test_termination_reports_existing_run_and_ticket_records_without_alert(self) -> None:
        self._assert_signal_stops_run_without_alert(signal.SIGTERM)

    def test_opencode_ctrl_c_preserves_interrupt_recovery_without_alert(self) -> None:
        self._assert_signal_stops_run_without_alert(signal.SIGINT, harness="opencode")

    def test_opencode_termination_preserves_interrupt_recovery_without_alert(self) -> None:
        self._assert_signal_stops_run_without_alert(signal.SIGTERM, harness="opencode")

    def test_linux_ctrl_c_reports_existing_records_without_alert(self) -> None:
        self._assert_signal_stops_run_without_alert(signal.SIGINT, platform="linux")

    def test_linux_termination_reports_existing_records_without_alert(self) -> None:
        self._assert_signal_stops_run_without_alert(signal.SIGTERM, platform="linux")

    def test_transcript_marker_without_result_file_does_not_close_ticket(self) -> None:
        for harness, selected_calls, unselected_calls in (
            (None, "opencode_calls", "copilot_calls"),
            ("copilot", "copilot_calls", "opencode_calls"),
        ):
            with self.subTest(harness=harness or "default"):
                self.state = self.default_state()
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    harness=harness,
                    extra_env={"IMPLEMENT_LOOP_TEST_RESULT_MODE": "missing"},
                )
                self.assertNotEqual(result.returncode, 0)
                state = self.load_state()
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])
                self.assertEqual(len(state[selected_calls]), 1)
                self.assertEqual(state[unselected_calls], [])
                self.assertEqual(len(runs), 1)
                attempt = runs[0] / "attempt-1-ticket-2"
                self.assertEqual(
                    json.loads((attempt / "completion-evidence.json").read_text())["status"],
                    "missing",
                )
                self.assertIn(b"did not write the required per-attempt result file", result.stderr)
                self.assertIn(b"IMPLEMENT_LOOP_COMPLETE", (attempt / "agent.log").read_bytes())

    def test_result_file_closes_ticket_after_clean_descendant_commit(self) -> None:
        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"COPILOT_TEST_CONFIG": "inherited-setting"},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        state = self.load_state()
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual([event[0] for event in state["events"]], ["assign", "close"])
        self.assertEqual(set(state["api_hosts"]), {"github.com"})
        self.assertEqual(len(runs), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        metadata = json.loads((attempt / "attempt.json").read_text())
        completion = json.loads((attempt / "completion.json").read_text())
        self.assertEqual(completion["issue"], 2)
        self.assertEqual(completion["attempt_id"], metadata["attempt_id"])
        self.assertTrue(state["copilot_calls"][0]["result_initially_absent"])
        self.assertFalse(Path(metadata["result_path"]).exists())
        self.assertFalse(Path(metadata["result_path"]).is_relative_to(self.repo))
        self.assertEqual(state["copilot_calls"][0]["copilot_config"], "inherited-setting")
        call_args = state["copilot_calls"][0]["args"]
        self.assertEqual(call_args[call_args.index("--model") + 1], "gpt-6-luna")
        self.assertEqual(call_args[call_args.index("--reasoning-effort") + 1], "max")
        self.assertNotIn("--deny-tool", call_args)
        prompt = state["copilot_calls"][0]["prompt"]
        self.assertIn("/implement Implement only ticket #2", prompt)
        self.assertIn("Spec fixture with shared requirements", prompt)
        self.assertIn("Parent comment fixture.", prompt)
        self.assertIn("Ticket comment fixture.", prompt)
        self.assertIn("Second paginated ticket comment.", prompt)
        self.assertIn("Any delegated workers must follow these same scope", prompt)
        self.assertIn("incorporate its results before reporting completion", prompt)
        self.assertIn("branch main", state["events"][1][2])
        self.assertIn("The wrapper did not push or open a PR", state["events"][1][2])
        self.assertEqual((attempt / "commit").read_text().strip(), self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertTrue((attempt / "agent.log").is_file())
        self.assertTrue((attempt / "process.json").is_file())
        self.assertTrue((attempt / "commit").is_file())
        self.assertFalse((runs[0] / "active.pid").exists())

    def test_invocation_from_checkout_subdirectory_uses_checkout_root(self) -> None:
        nested = self.repo / "nested"
        nested.mkdir()
        (nested / "tracked.txt").write_text("tracked fixture\n")
        self.git("add", "nested")
        self.git("commit", "--quiet", "-m", "Add nested fixture")

        result, _ = self.run_loop("--max-iterations", "1", cwd=nested)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(self.git("branch", "--show-current", cwd=nested), "main")
        self.assertTrue((self.repo / "implementation-2.txt").exists())

    def test_result_file_rejections_never_close_or_unassign_ticket(self) -> None:
        modes = (
            "malformed",
            "wrong-root",
            "wrong-ticket",
            "wrong-ticket-number",
            "wrong-attempt",
            "wrong-status",
            "failed-acceptance",
            "failed-validation",
        )
        for mode in modes:
            with self.subTest(mode=mode):
                state = self.load_state()
                state["tickets"]["2"]["state"] = "open"
                state["tickets"]["2"]["assignees"] = []
                state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = 0
                state["events"] = []
                state["make_commit"] = False
                self.state = state
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    extra_env={"IMPLEMENT_LOOP_TEST_RESULT_MODE": mode},
                )
                after = self.load_state()
                self.assertNotEqual(result.returncode, 0, result.stdout.decode())
                self.assertEqual(after["tickets"]["2"]["state"], "open")
                self.assertEqual(after["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in after["events"]], ["assign"])
                attempt = runs[-1] / "attempt-1-ticket-2"
                evidence = json.loads((attempt / "completion-evidence.json").read_text())
                self.assertNotEqual(evidence["status"], "valid")
                self.assertTrue((attempt / "completion-result.json").is_file())

    def test_nonzero_harness_exit_rejects_even_valid_result_file(self) -> None:
        for harness, selected_calls, unselected_calls, display_name in (
            (None, "opencode_calls", "copilot_calls", "OpenCode"),
            ("copilot", "copilot_calls", "opencode_calls", "Copilot"),
        ):
            with self.subTest(harness=harness or "default"):
                self.state = self.default_state()
                self.state["agent_exit"] = 7
                self.state["make_commit"] = False
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    harness=harness,
                )
                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state[selected_calls]), 1)
                self.assertEqual(state[unselected_calls], [])
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertIn(f"{display_name} exited with status 7".encode(), result.stderr)
                attempt = runs[-1] / "attempt-1-ticket-2"
                self.assertIn(
                    f"Run records: {runs[-1].resolve()}".encode(),
                    result.stderr,
                )
                self.assertIn(
                    f"Ticket #2 records: {attempt.resolve()}".encode(),
                    result.stderr,
                )
                self.assertEqual(json.loads((attempt / "process.json").read_text())["exit_code"], 7)
                self.assertEqual(
                    json.loads((attempt / "completion-evidence.json").read_text())["status"],
                    "valid",
                )

    def test_required_record_persistence_failure_prevents_closure(self) -> None:
        self.state["agent_action"] = "break_records"
        self.state["make_commit"] = False
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        attempt = runs[-1] / "attempt-1-ticket-2"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Cannot persist required run record", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])
        attempt.chmod(0o700)

    def test_agent_log_write_failure_prevents_closure(self) -> None:
        def limit_file_size() -> None:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (8192, 8192))

        for harness, display_name in ((None, "OpenCode"), ("copilot", "Copilot")):
            with self.subTest(harness=harness or "default"):
                self.state = self.default_state()
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    harness=harness,
                    extra_env={"IMPLEMENT_LOOP_TEST_OUTPUT": "x" * 20_000},
                    preexec_fn=limit_file_size,
                )

                state = self.load_state()
                attempt = runs[-1] / "attempt-1-ticket-2"
                process = json.loads((attempt / "process.json").read_text())
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    f"Unable to persist the complete {display_name} output log".encode(),
                    result.stderr,
                )
                self.assertFalse(process["log_persisted"])
                self.assertLessEqual((attempt / "agent.log").stat().st_size, 8192)
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_missing_copilot_cli_fails_before_claim(self) -> None:
        limited_bin = self.controlled_path_directory("python3", "git", "gh", "opencode")

        result, runs = self.run_loop(
            harness="copilot",
            extra_env={"PATH": str(limited_bin)},
        )

        self.assert_missing_executable_guidance(result, "copilot", harness="Copilot")
        self.assert_missing_executable_stopped_before_run(runs)

    def test_missing_default_opencode_cli_does_not_fall_back_to_copilot(self) -> None:
        limited_bin = self.controlled_path_directory("python3", "git", "gh", "copilot")

        result, runs = self.run_loop(harness=None, extra_env={"PATH": str(limited_bin)})

        self.assert_missing_executable_guidance(result, "opencode", harness="OpenCode")
        self.assert_missing_executable_stopped_before_run(runs)

    def test_default_opencode_runs_without_a_copilot_executable(self) -> None:
        limited_bin = self.controlled_path_directory("python3", "git", "gh", "opencode")

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"PATH": str(limited_bin)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(len(state["opencode_calls"]), 1)
        self.assertEqual(state["copilot_calls"], [])

    def test_explicit_copilot_runs_without_an_opencode_executable(self) -> None:
        limited_bin = self.controlled_path_directory("python3", "git", "gh", "copilot")

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness="copilot",
            extra_env={"PATH": str(limited_bin)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])

    def test_rejected_model_stops_selected_harness_without_fallback(self) -> None:
        for selected_harness, call_key, other_key in (
            (None, "opencode_calls", "copilot_calls"),
            ("copilot", "copilot_calls", "opencode_calls"),
        ):
            with self.subTest(harness=selected_harness or "default"):
                self.state = self.default_state()
                self.state["model_rejected_for"] = call_key.removesuffix("_calls")
                self.save_state()

                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    harness=selected_harness,
                )

                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state[call_key]), 1)
                self.assertEqual(state[other_key], [])
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])
                attempt = runs[0] / "attempt-1-ticket-2"
                self.assertEqual(json.loads((attempt / "process.json").read_text())["exit_code"], 1)
                self.assertIn(b"Model unavailable", (attempt / "agent.log").read_bytes())
