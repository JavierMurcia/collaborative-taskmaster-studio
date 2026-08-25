from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from studio.capabilities.github import GitHubReader
from studio.security import IdentityContext


class FakeConnections:
    def access_token(self, identity: IdentityContext, plugin_id: str) -> str:
        assert identity.user_id == "javier"
        assert plugin_id == "github"
        return "github-token"


class RecordingGitHubReader(GitHubReader):
    def __init__(self, responses: list[list[dict[str, object]]]) -> None:
        super().__init__(FakeConnections())  # type: ignore[arg-type]
        self.responses = responses
        self.urls: list[str] = []

    def _json_request(self, url: str, token: str) -> list[dict[str, object]]:
        assert token == "github-token"
        self.urls.append(url)
        return self.responses.pop(0)


def identity() -> IdentityContext:
    return IdentityContext(
        user_id="javier",
        workspace_id="personal_javier",
        authenticated=True,
        mode="identity_platform",
    )


def test_github_lists_and_counts_visible_owned_repositories() -> None:
    reader = RecordingGitHubReader(
        [
            [
                {
                    "id": 10,
                    "name": "collaborative-taskmaster-studio",
                    "full_name": "JavierMurcia/collaborative-taskmaster-studio",
                    "description": "Estudio de agentes",
                    "private": False,
                    "updated_at": "2026-08-24T12:00:00Z",
                    "html_url": "https://github.com/JavierMurcia/collaborative-taskmaster-studio",
                    "owner": {"login": "JavierMurcia"},
                }
            ]
        ]
    )

    result = reader.list_repositories(identity())

    query = parse_qs(urlsplit(reader.urls[0]).query)
    assert query["affiliation"] == ["owner"]
    assert query["visibility"] == ["all"]
    assert result["visible_repository_count"] == 1
    assert result["matching_repository_count"] == 1
    assert result["read_only"] is True
    assert result["repositories"][0]["full_name"] == (  # type: ignore[index]
        "JavierMurcia/collaborative-taskmaster-studio"
    )


def test_github_filters_visible_repositories_without_another_api_call() -> None:
    reader = RecordingGitHubReader(
        [
            [
                {"id": 1, "name": "alpha", "full_name": "Javier/alpha"},
                {"id": 2, "name": "taskmaster", "full_name": "Javier/taskmaster"},
            ]
        ]
    )

    result = reader.list_repositories(identity(), "taskmaster")

    assert result["visible_repository_count"] == 2
    assert result["matching_repository_count"] == 1
    assert result["repositories"][0]["name"] == "taskmaster"  # type: ignore[index]
