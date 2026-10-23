"""GitHub CLI integration for issue lookup, claiming, and confirmed closure."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .errors import LoopError

JsonObject = dict[str, Any]

_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class Repository:
    """A checkout's inferred GitHub repository and host."""

    host: str
    name_with_owner: str
    repo_ref: str
    api_prefix: str
    url: str


class GitHub:
    """Use GitHub CLI subprocesses while keeping the checkout as the authority."""

    def __init__(self, cwd: os.PathLike[str] | str) -> None:
        self.cwd = os.fspath(cwd)
        self.environment = os.environ.copy()

    def _run(
        self,
        args: list[str],
        *,
        env: Mapping[str, str] | None = None,
        operation: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["gh", *args],
                cwd=self.cwd,
                env=dict(env) if env is not None else self.environment,
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            raise LoopError(f"Unable to run GitHub CLI for {operation}: {error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            raise LoopError(f"GitHub CLI failed during {operation}: {detail}")
        return result

    def _json(
        self,
        args: list[str],
        *,
        env: Mapping[str, str] | None = None,
        operation: str,
    ) -> Any:
        result = self._run(args, env=env, operation=operation)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise LoopError(f"GitHub CLI returned malformed JSON during {operation}.") from error

    def resolve_repository(self) -> Repository:
        """Infer the repo without GH_REPO, then reject a conflicting override."""

        inference_environment = self.environment.copy()
        repo_override = inference_environment.pop("GH_REPO", None)
        result = self._json(
            ["repo", "view", "--json", "nameWithOwner,url"],
            env=inference_environment,
            operation="repository discovery",
        )
        if not isinstance(result, dict):
            raise LoopError("GitHub repository discovery returned malformed metadata.")
        name = result.get("nameWithOwner")
        url = result.get("url")
        if not isinstance(name, str) or not _REPOSITORY_NAME.fullmatch(name):
            raise LoopError("GitHub repository discovery did not return a valid repository name.")
        if not isinstance(url, str):
            raise LoopError("GitHub repository discovery did not return a repository URL.")

        parsed_url = urlsplit(url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise LoopError("GitHub returned an invalid repository URL.")
        url_name = parsed_url.path.strip("/")
        if url_name.casefold() != name.casefold():
            raise LoopError("GitHub returned inconsistent repository name and URL metadata.")

        host = parsed_url.netloc.lower()
        repository = Repository(
            host=host,
            name_with_owner=name,
            repo_ref=f"{host}/{name}",
            api_prefix=f"repos/{name}",
            url=url,
        )
        if repo_override:
            self._check_repository_override(repo_override, repository)
        return repository

    @staticmethod
    def _check_repository_override(value: str, repository: Repository) -> None:
        pieces = value.strip("/").split("/")
        if len(pieces) == 2:
            override_name = "/".join(pieces)
        elif len(pieces) == 3:
            override_host, owner, name = pieces
            if override_host.casefold() != repository.host.casefold():
                raise LoopError(
                    "GH_REPO points to a different GitHub host than the current checkout. "
                    "Unset it or make it match the checkout."
                )
            override_name = f"{owner}/{name}"
        else:
            raise LoopError(
                "GH_REPO is not a valid repository override; use OWNER/REPO or HOST/OWNER/REPO."
            )
        if override_name.casefold() != repository.name_with_owner.casefold():
            raise LoopError(
                "GH_REPO conflicts with the repository inferred from this checkout. "
                "Unset it or make it match the checkout."
            )

    def api_json(self, repository: Repository, endpoint: str, *, paginate: bool = False) -> Any:
        args = ["api", "--hostname", repository.host]
        if paginate:
            args.extend(["--paginate", "--slurp"])
        args.append(endpoint)
        return self._json(args, operation=f"reading {endpoint}")

    def authenticated_login(self, repository: Repository) -> str:
        result = self.api_json(repository, "user")
        login = result.get("login") if isinstance(result, dict) else None
        if not isinstance(login, str):
            raise LoopError("GitHub did not return an authenticated user login.")
        if not login:
            raise LoopError("GitHub returned an empty authenticated user login.")
        return login

    def _get_issue(
        self,
        repository: Repository,
        number: int,
        description: str,
    ) -> JsonObject:
        value = self.api_json(repository, f"{repository.api_prefix}/issues/{number}")
        if not isinstance(value, dict):
            raise LoopError(f"{description} #{number} is missing native issue metadata.")
        if type(value.get("number")) is not int or value["number"] != number:
            raise LoopError(
                f"GitHub returned malformed metadata for {description.lower()} #{number}."
            )
        self._validate_issue_identity(value, repository, f"{description.lower()} #{number}")
        self._validate_body(value, f"{description.lower()} #{number}")
        return value

    def get_spec(self, repository: Repository, number: int) -> JsonObject:
        value = self._get_issue(repository, number, "Spec")
        if value.get("state") != "open":
            raise LoopError("The spec must be an open GitHub issue, not a pull request.")
        return value

    def get_children(
        self,
        repository: Repository,
        spec_number: int,
        repository_url: str,
    ) -> list[JsonObject]:
        value = self.api_json(
            repository,
            f"{repository.api_prefix}/issues/{spec_number}/sub_issues?per_page=100",
            paginate=True,
        )
        if not isinstance(value, list) or any(not isinstance(page, list) for page in value):
            raise LoopError("GitHub did not return native sub-issue pages.")

        children: list[JsonObject] = []
        for page in value:
            for child in page:
                if not isinstance(child, dict):
                    raise LoopError("GitHub returned a malformed native child issue.")
                number = child.get("number")
                if type(number) is not int or number <= 0:
                    raise LoopError("A child is missing a valid native issue number.")
                if child.get("state") not in ("open", "closed"):
                    raise LoopError(f"Child #{number} is missing a valid issue state.")
                if child.get("repository_url") != repository_url:
                    raise LoopError(
                        "All children must be native issues in this repository; "
                        "cross-repository children are not supported."
                    )
                if child.get("pull_request") is not None:
                    raise LoopError(f"Child #{number} is a pull request, not an issue.")
                children.append(child)
        return children

    def get_ticket(
        self,
        repository: Repository,
        number: int,
        repository_url: str,
    ) -> JsonObject:
        value = self._get_issue(repository, number, "Ticket")
        if value.get("repository_url") != repository_url:
            raise LoopError(f"Ticket #{number} belongs to a different repository.")
        if value.get("state") not in ("open", "closed"):
            raise LoopError(f"Ticket #{number} is missing a valid issue state.")

        assignees = value.get("assignees")
        if not isinstance(assignees, list):
            raise LoopError(f"Ticket #{number} is missing readable assignment data.")
        for assignee in assignees:
            if (
                not isinstance(assignee, dict)
                or not isinstance(assignee.get("login"), str)
                or not assignee["login"]
            ):
                raise LoopError(f"Ticket #{number} has malformed assignment data.")

        summary = value.get("issue_dependencies_summary")
        blocked_by = summary.get("blocked_by") if isinstance(summary, dict) else None
        if type(blocked_by) is not int or blocked_by < 0:
            raise LoopError(
                f"Ticket #{number} is missing readable native dependency data; refusing to guess."
            )
        return value

    def get_comments(self, repository: Repository, issue_number: int) -> list[JsonObject]:
        value = self.api_json(
            repository,
            f"{repository.api_prefix}/issues/{issue_number}/comments?per_page=100",
            paginate=True,
        )
        if not isinstance(value, list) or any(not isinstance(page, list) for page in value):
            raise LoopError(f"GitHub did not return comment pages for issue #{issue_number}.")

        comments: list[JsonObject] = []
        for page in value:
            for comment in page:
                if not isinstance(comment, dict) or "body" not in comment:
                    raise LoopError(
                        f"GitHub returned malformed comments for issue #{issue_number}."
                    )
                body = comment.get("body")
                if body is not None and not isinstance(body, str):
                    raise LoopError(
                        f"GitHub returned malformed comments for issue #{issue_number}."
                    )
                user = comment.get("user")
                login: str | None = None
                if user is not None:
                    if (
                        not isinstance(user, dict)
                        or not isinstance(user.get("login"), str)
                        or not user["login"]
                    ):
                        raise LoopError(
                            f"GitHub returned malformed comment authors for issue #{issue_number}."
                        )
                    login = user["login"]
                comments.append({"author": login, "body": body})
        return comments

    def assign(self, repository: Repository, issue_number: int, login: str) -> None:
        try:
            self._run(
                [
                    "issue",
                    "edit",
                    str(issue_number),
                    "--repo",
                    repository.repo_ref,
                    "--add-assignee",
                    login,
                ],
                operation=f"assigning ticket #{issue_number}",
            )
        except LoopError as error:
            raise LoopError(
                f"{error}\n"
                f"Assignment of ticket #{issue_number} may have taken effect. "
                f"Inspect ticket #{issue_number}'s live GitHub state before rerunning."
            ) from error

    def close(self, repository: Repository, issue_number: int, comment: str) -> None:
        try:
            self._run(
                [
                    "issue",
                    "close",
                    str(issue_number),
                    "--repo",
                    repository.repo_ref,
                    "--comment",
                    comment,
                ],
                operation=f"closing ticket #{issue_number}",
            )
        except LoopError as error:
            raise LoopError(
                f"{error}\n"
                f"The closure comment or closure of ticket #{issue_number} may have taken effect. "
                f"Inspect ticket #{issue_number}'s live GitHub state before rerunning."
            ) from error

    @staticmethod
    def _validate_issue_identity(
        issue: JsonObject,
        repository: Repository,
        description: str,
    ) -> None:
        repository_url = issue.get("repository_url")
        if not isinstance(repository_url, str) or not repository_url:
            raise LoopError(f"{description} is missing repository metadata.")
        parsed_repository_url = urlsplit(repository_url)
        expected_suffix = f"/repos/{repository.name_with_owner}"
        if repository.host == "github.com":
            expected_api_hosts = {"api.github.com"}
        else:
            expected_api_hosts = {repository.host, f"api.{repository.host}"}
        if (
            parsed_repository_url.scheme != "https"
            or parsed_repository_url.netloc.lower() not in expected_api_hosts
            or parsed_repository_url.query
            or parsed_repository_url.fragment
            or not parsed_repository_url.path.rstrip("/")
            .casefold()
            .endswith(expected_suffix.casefold())
        ):
            raise LoopError(f"{description} has malformed repository metadata.")
        if issue.get("pull_request") is not None:
            raise LoopError(f"{description} is a pull request, not an issue.")

    @staticmethod
    def _validate_body(issue: JsonObject, description: str) -> None:
        if "body" not in issue:
            raise LoopError(f"{description} is missing its issue body metadata.")
        if issue["body"] is not None and not isinstance(issue["body"], str):
            raise LoopError(f"{description} has malformed body metadata.")
