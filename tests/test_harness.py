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
        events = [
            {
                "type": "text",
                "timestamp": 1,
                "sessionID": "ses_fixture",
                "part": {"type": "text", "text": "Assistant wording stays unchanged."},
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
        self.assertIn(b"Assistant wording stays unchanged.", result.stdout)
        self.assertIn(b"Assistant\nAssistant wording stays unchanged.\n\n", result.stdout)
        self.assertIn(b"\xe2\x9c\x93 Inspect fixture", result.stdout)
        self.assertIn(b"tool: example_tool", result.stdout)
        self.assertIn(b"result line", result.stdout)
        self.assertNotIn(b"\x1b[", result.stdout)
        self.assertNotIn(b'"type": "tool_use"', result.stdout)
        self.assertEqual(result.stdout.count(b"OpenCode: github-copilot"), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertEqual((attempt / "agent.log").read_bytes(), raw_output.encode("utf-8"))

    def test_opencode_unrecognized_and_malformed_output_stays_visible(self) -> None:
        raw_lines = [
            '{"type":"future_event","value":"future details"}',
            '{"type":"tool_use","part":{"tool":"odd_tool","state":{"status":[]}}}',
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
        self.assertIn(b"OpenCode diagnostic", result.stdout)
        self.assertIn(b"OpenCode startup diagnostic", result.stdout)
        self.assertIn(b"Tool result", result.stdout)
        self.assertIn(b"tool: unknown tool", result.stdout)
        self.assertIn(b"(no output)", result.stdout)
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
        self.assertIn("(no output)", visible)
        for index in range(1, 4):
            self.assertIn(f"three-{index}", visible)
        for index in range(1, 6):
            self.assertIn(f"five-{index}", visible)
            self.assertIn(f"seven-{index}", visible)
        self.assertNotIn("seven-6", visible)
        self.assertNotIn("seven-7", visible)
        self.assertIn("... 2 additional line(s) omitted", visible)
        self.assertIn("tool: unfamiliar_tool", visible)
        attempt = runs[0] / "attempt-1-ticket-2"
        log = attempt / "agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertIn(b"seven-6", log.read_bytes())

    def test_opencode_successful_file_reads_summarize_target_without_displaying_contents(
        self,
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
                    "input": {"filePath": "src/private.py", "offset": 4, "limit": 3},
                    "output": "SECRET_FILE_CONTENT\nmore private contents",
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

        direct_metadata_event = {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_read",
            "part": {
                "type": "tool",
                "tool": "read",
                "state": {
                    "status": "completed",
                    "title": "read",
                    "input": {"filePath": "src/summary.py"},
                    "output": "SECRET_DIRECT_COUNT_CONTENT",
                    "metadata": {"metadata": {"lineCount": 12, "truncated": False}},
                },
            },
        }

        raw_output, visible, runs = self._run_opencode_events([event, direct_metadata_event])

        self.assertIn("src/private.py", visible)
        self.assertIn("lines: 120", visible)
        self.assertIn("src/summary.py", visible)
        self.assertIn("lines: 12", visible)
        self.assertNotIn("SECRET_FILE_CONTENT", visible)
        self.assertNotIn("more private contents", visible)
        self.assertNotIn("SECRET_DIRECT_COUNT_CONTENT", visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"SECRET_FILE_CONTENT", log.read_bytes())
        self.assertIn(b"SECRET_DIRECT_COUNT_CONTENT", log.read_bytes())

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
                    "input": {"filePath": "src/no-count.py"},
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
                    "input": {"filePath": "src/large.py"},
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
        self.assertNotIn("lines: 999", visible)
        self.assertNotIn("SECRET_TRUNCATED_READ_CONTENT", visible)
        self.assertIn(b"SECRET_TRUNCATED_READ_CONTENT", raw_output.encode("utf-8"))
        self.assertEqual(
            (runs[0] / "attempt-1-ticket-2/agent.log").read_bytes(),
            raw_output.encode("utf-8"),
        )

    def test_opencode_successful_searches_show_query_scope_and_only_emitted_counts(self) -> None:
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
                            "limit": 10,
                        },
                        "output": "Found 2 matches\nSECRET_MATCH_ONE\nSECRET_MATCH_TWO",
                        "metadata": {"metadata": {"matches": 2}},
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
                        "input": {"pattern": "src/**/*.py", "path": "workspace"},
                        "output": "Found 17 matches\nSECRET_GLOB_MATCH_ONE\nSECRET_GLOB_MATCH_TWO",
                    },
                },
            },
        ]

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("NEEDLE_QUERY", visible)
        self.assertIn("lib/", visible)
        self.assertIn("matches: 2", visible)
        self.assertIn("src/**/*.py", visible)
        self.assertIn("workspace", visible)
        self.assertIn("count: not provided", visible)
        self.assertNotIn("Found 17 matches", visible)
        self.assertNotIn("results: 17", visible)
        for match in (
            "SECRET_MATCH_ONE",
            "SECRET_MATCH_TWO",
            "SECRET_GLOB_MATCH_ONE",
            "SECRET_GLOB_MATCH_TWO",
        ):
            self.assertNotIn(match, visible)
            self.assertIn(match.encode("utf-8"), raw_output.encode("utf-8"))
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))

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
                        "output": "finding-1\nfinding-2\nfinding-3\nfinding-4\n"
                        "finding-5\nfinding-6\nfinding-7",
                    },
                },
            },
            {
                "type": "tool_use",
                "timestamp": 2,
                "sessionID": "ses_delegation",
                "part": {
                    "type": "tool",
                    "tool": "invoke_worker",
                    "state": {
                        "status": "completed",
                        "title": "Review the failure path",
                        "input": {"description": "Review the failure path"},
                        "output": "A second parent-level delegated result.",
                        "metadata": {
                            "metadata": {
                                "agent_type": "reviewer",
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
                    "tool": "workflow_runner",
                    "state": {
                        "status": "completed",
                        "title": "Run a task",
                        "input": {"description": "Run a task", "task_id": "not-enough-evidence"},
                        "output": "Ordinary generic output.",
                    },
                },
            },
        ]

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("Delegated: Inspect parser behavior", visible)
        self.assertIn("agent: explore", visible)
        self.assertIn("task id: ses_child_123", visible)
        self.assertIn("finding-1", visible)
        self.assertIn("finding-5", visible)
        self.assertNotIn("finding-6", visible)
        self.assertNotIn("finding-7", visible)
        self.assertIn("... 2 additional line(s) omitted", visible)
        self.assertIn("Delegated: Review the failure path", visible)
        self.assertIn("agent: reviewer", visible)
        self.assertIn("session: ses_review_child", visible)
        self.assertIn("A second parent-level delegated result.", visible)
        self.assertIn("✓ Run a task", visible)
        self.assertIn("tool: workflow_runner", visible)
        self.assertNotIn("Delegated: Run a task", visible)
        self.assertNotIn("A private task prompt", visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))
        self.assertIn(b"finding-7", log.read_bytes())
        self.assertIn(b"A private task prompt", log.read_bytes())

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
                        "input": {"filePath": "src/unavailable.py"},
                        "error": "READ_ERROR_DETAIL",
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
                        "input": {"subagent_type": "explore"},
                        "error": "DELEGATION_ERROR_LINE_1\nDELEGATION_ERROR_LINE_2\n"
                        "DELEGATION_ERROR_LINE_3\nDELEGATION_ERROR_LINE_4\n"
                        "DELEGATION_ERROR_LINE_5\nDELEGATION_ERROR_LINE_6",
                    },
                },
            },
        ]

        raw_output, visible, runs = self._run_opencode_events(events)

        self.assertIn("✗ Read failed", visible)
        self.assertIn("READ_ERROR_DETAIL", visible)
        self.assertIn("✗ Search failed", visible)
        self.assertIn("SEARCH_ERROR_DETAIL", visible)
        self.assertIn("✗ Delegated task failed", visible)
        for line in range(1, 7):
            self.assertIn(f"DELEGATION_ERROR_LINE_{line}", visible)
        self.assertNotIn("additional line(s) omitted", visible)
        log = runs[0] / "attempt-1-ticket-2/agent.log"
        self.assertEqual(log.read_bytes(), raw_output.encode("utf-8"))

    def _run_opencode_events(self, events: list[dict[str, object]]) -> tuple[str, str, list[Path]]:
        raw_output = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
        self.state["opencode_raw_output"] = raw_output
        self.save_state()
        result, runs = self.run_loop("--max-iterations", "1", harness=None)
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
            self.assertIn(f"✓ {title}", visible)
            self.assertIn("tool: shell", visible)
            self.assertIn(f"command:\n    {command}", visible)
        self.assertIn("(no output)", visible)
        for line in ("short-1", "short-2", "short-3", "five-1", "five-5"):
            self.assertIn(line, visible)
        for line in ("long-1", "long-5"):
            self.assertIn(line, visible)
        self.assertNotIn("long-6", visible)
        self.assertNotIn("long-7", visible)
        self.assertEqual(visible.count("additional line(s) omitted"), 1)
        self.assertIn("... 2 additional line(s) omitted", visible)
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
        self.assertIn("✗ Failed shell command", visible)
        self.assertIn("tool: shell", visible)
        self.assertIn("command:\n    git status --short", visible)
        for line in range(1, 7):
            self.assertIn(f"failure-{line}", visible)
        self.assertIn('input: {"workdir":"/workspace"}', visible)
        self.assertIn("No command field is available.", visible)
        self.assertIn("Tool result", visible)
        self.assertIn("No input details are available.", visible)
        self.assertNotIn("command:\n", visible[visible.index("No command field is available.") :])
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
        self.assertIn("✗ Failed inspection", visible)
        self.assertIn("tool: failing_tool", visible)
        self.assertIn("error:", visible)
        self.assertIn("sixth error line", visible)
        self.assertNotIn("additional line(s) omitted", visible)
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
