"""Deployment artifact guards — Dockerfile per app, compose for local dev.

Three Dockerfiles, one compose file and `docs/deployment.md` carry claims that are checkable
text: a base-image tag either satisfies `requires-python` or it does not, and a documented
`docker compose up -d postgres` either names a service that exists or it does not. Everything
asserted here is in that category, which is where doc prose gets bound by test rather than by
discipline.

Two things are deliberately NOT asserted here, so their absence reads as a decision:

* Nothing runs `docker`. These tests parse artifacts and must pass on a runner with no daemon.
  The behaviour that needs a daemon — that the framing origin reaches the wire, and that a
  widening value fails the build — was measured against the compose setup and is recorded in
  `docs/deployment.md`, because a test that shells out to `docker build` is a test that skips.
* No test asserts the admin panel image builds. It cannot, outside the Biznet Gio network
  (`@gio/*` is internal-only, `docs/admin-web.md`), and a test that passes only on an internal
  runner is a test that is red or skipped everywhere else.
"""

from __future__ import annotations

import re
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment.md"

# The three deploy artifacts — one repo, three deploy artifacts — and nothing else. Keyed by the
# compose service that builds each one, because the service name is what the rest of this file
# cross-checks.
DEPLOYABLES = {
    "api": Path("apps/api"),
    "admin-web": Path("apps/admin-web"),
    "embed": Path("apps/web-embed"),
}
WEB_DEPLOYABLES = ("admin-web", "embed")

# `pnpm install` on Node 20 dies with `ERR_UNKNOWN_BUILTIN_MODULE: No such built-in module:
# node:sqlite` before it resolves a package, because `packageManager` pins pnpm 11.x and pnpm
# 11's own `engines` demand `>=22.13`. MEASURED against the compose setup on
# `node:20-bookworm-slim`, not read off a changelog.
#
# This floor belongs to pnpm 11 and to nothing else, which is why
# `test_web_packages_still_pin_the_pnpm_major_this_floor_was_measured_for` exists: a pnpm major
# bump has to re-measure rather than inherit.
MEASURED_PNPM_NODE_FLOOR = 22
MEASURED_PNPM_MAJOR = 11

# The variables that may only ever be build arguments. Named once.
#
# Two names, one origin, both baked — and one name contains the other, which is why the checks
# below match assigned KEYS exactly instead of searching for a substring. A substring test for the
# private name also fires on a line that sets only the public one, so it reports the wrong
# variable; and a substring test for the public name never fires on the private one at all. Widening
# the old substring check by adding a second name would have left one direction uncovered while
# reading as coverage for both.
FRAMING_ORIGIN_VAR = "NOA_LIBRECHAT_ORIGIN"
PUBLIC_FRAMING_ORIGIN_VAR = f"NEXT_PUBLIC_{FRAMING_ORIGIN_VAR}"
FRAMING_ORIGIN_VARS = (FRAMING_ORIGIN_VAR, PUBLIC_FRAMING_ORIGIN_VAR)

# An `KEY=value` assignment anywhere on a Dockerfile line.
#
# Anywhere, not anchored to `ENV`, because a multi-line `ENV A=1 \` puts its second and later keys
# on continuation lines that start with nothing at all — the embed image's runtime stage is exactly
# that shape. The old check was an unanchored substring search for the same reason; this keeps the
# reach and stops matching one name inside the other.
ENV_ASSIGNMENT = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=")


def assigned_env_keys(line: str) -> set[str]:
    """Every key a Dockerfile line assigns. Comments assign nothing."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return set()
    return set(ENV_ASSIGNMENT.findall(stripped))


# Values `core/config.py` treats as secrets. None may appear as a container
# environment key in compose: a service that does not read one should not be handed one.
SECRET_ENV_VARS = frozenset(
    {
        "AUTH_JWT_SECRET",
        "NOA_SECRET_ENCRYPTION_KEY",
        "LDAP_BIND_PASSWORD",
    }
)

# The only literal credential in the compose file, and the reason it is tolerable is asserted
# separately by `test_compose_publishes_only_on_loopback`: nothing off this host can reach the
# database, whatever the password is. It is the pair `.env.example`'s `POSTGRES_URL` carries.
POSTGRES_DEV_ENV = {"POSTGRES_USER": "noa", "POSTGRES_PASSWORD": "noa", "POSTGRES_DB": "noa"}

FROM_LINE = re.compile(r"^FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?\s*$", re.MULTILINE)
IMAGE_TAG = re.compile(r"^(?P<name>[^:]+):(?P<tag>.+)$")
REQUIRES_PYTHON = re.compile(r"^>=(?P<low>\d+\.\d+),<(?P<high>\d+\.\d+)$")
MARKED_BLOCK = re.compile(
    r"```\n# noa-(?P<marker>[a-z-]+) \(asserted by [^)]+\)\n(?P<body>.*?)```",
    re.DOTALL,
)
# `docker compose [--profile x] up -d <service>` as an operator would paste it.
COMPOSE_COMMAND = re.compile(
    r"docker compose(?P<flags>(?:\s+--profile\s+\S+)*)\s+up\s+-d(?P<args>[^\n`]*)"
)
PROFILE_FLAG = re.compile(r"--profile\s+(\S+)")


# --------------------------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------------------------


def dockerfile(service: str) -> str:
    return (REPO_ROOT / DEPLOYABLES[service] / "Dockerfile").read_text(encoding="utf-8")


def dockerignore(service: str) -> list[str]:
    """Ignore patterns, comments and blanks dropped."""
    path = REPO_ROOT / DEPLOYABLES[service] / ".dockerignore"
    if service == "api":
        # The API builds from the repo root, so its ignore file lives there.
        path = REPO_ROOT / ".dockerignore"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def package_json(service: str) -> dict:
    import json

    path = REPO_ROOT / DEPLOYABLES[service] / "package.json"
    return json.loads(path.read_text(encoding="utf-8"))


def base_images(service: str) -> list[str]:
    """Every `FROM` image in build order, external bases only (stage references dropped)."""
    stages = {match.group("stage") for match in FROM_LINE.finditer(dockerfile(service))}
    return [
        match.group("image")
        for match in FROM_LINE.finditer(dockerfile(service))
        if match.group("image") not in stages
    ]


def compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def compose_services() -> dict[str, dict]:
    return compose()["services"]


def marked_block(marker: str) -> list[str]:
    """The body lines of a ```# noa-<marker>``` block in `docs/deployment.md`."""
    doc = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    for match in MARKED_BLOCK.finditer(doc):
        if match.group("marker") == marker:
            return [line for line in match.group("body").splitlines() if line.strip()]
    raise AssertionError(f"docs/deployment.md has no `# noa-{marker}` block")


def host_of(origin: str) -> str:
    """`http://embed.noa.internal:3001` -> `embed.noa.internal`."""
    return origin.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[0]


def under_session_cookie_domain(host: str) -> bool:
    """Does `Domain=.noa.internal` reach this host?"""
    return host == "noa.internal" or host.endswith(".noa.internal")


# --------------------------------------------------------------------------------------------
# Three deployables, three images, no cross-app context
# --------------------------------------------------------------------------------------------


def test_every_deployable_has_exactly_one_dockerfile() -> None:
    """Three deploy artifacts, and a fourth cannot appear unnoticed."""
    found = {
        path.relative_to(REPO_ROOT)
        for path in REPO_ROOT.rglob("Dockerfile")
        if not any(part in {".venv", "node_modules", ".git", "spikes"} for part in path.parts)
    }

    assert found == {directory / "Dockerfile" for directory in DEPLOYABLES.values()}


def test_web_images_build_from_their_own_app_directory() -> None:
    """The two web packages share no source, so neither may see the other's files.

    A repo-root context would put `apps/admin-web` inside the embed image and vice versa —
    the boundary AGENTS.md states, expressed as a build context.
    """
    services = compose_services()

    assert services["admin-web"]["build"]["context"] == "apps/admin-web"
    assert services["embed"]["build"]["context"] == "apps/web-embed"

    for service in WEB_DEPLOYABLES:
        # No explicit `dockerfile:` needed, which is the tell that the context is the app dir.
        assert "dockerfile" not in services[service]["build"]


def test_api_image_builds_from_the_workspace_root() -> None:
    """`noa-api` is a uv workspace member; `noa-core` is the workspace root.

    The inverse of the test above, and it has to be asserted separately: an `apps/api` context
    cannot resolve `noa-core` at all, so this one context genuinely has to be the repo root.
    """
    build = compose_services()["api"]["build"]

    assert build["context"] == "."
    assert build["dockerfile"] == "apps/api/Dockerfile"


# --------------------------------------------------------------------------------------------
# Base image versions — Python window, exact web pins
# --------------------------------------------------------------------------------------------


def test_api_image_python_matches_requires_python() -> None:
    """The `>=3.11,<3.13` Python window, read off `pyproject.toml`, not restated.

    `pgpy==0.6.0` imports the removed stdlib `imghdr` at import time, which takes down the whole
    tool registry on 3.13+. A base image past the bound is that failure shipped, and it surfaces
    at an operator's first tool call rather than at build time.
    """
    with (REPO_ROOT / "apps" / "api" / "pyproject.toml").open("rb") as handle:
        requires = tomllib.load(handle)["project"]["requires-python"]

    bounds = REQUIRES_PYTHON.match(requires)
    assert bounds, f"cannot read a floor and ceiling out of {requires!r}"
    low = tuple(int(part) for part in bounds.group("low").split("."))
    high = tuple(int(part) for part in bounds.group("high").split("."))

    images = base_images("api")
    assert images, "no external base image in the API Dockerfile"

    for image in images:
        tag = IMAGE_TAG.match(image)
        assert tag, f"base image {image!r} carries no tag — an implicit `latest` is a silent bump"
        version = tuple(int(part) for part in tag.group("tag").split("-")[0].split("."))
        assert low <= version < high, f"{image} sits outside requires-python {requires}"


def test_api_image_uses_one_python_base_for_every_stage() -> None:
    """A wheel compiled on one minor and run on another is an import error at boot.

    `python-ldap` is compiled in the builder stage (no wheel exists on PyPI), so the runtime
    stage has to be the same base — otherwise the extension module and the interpreter that
    loads it come from different Python minors.
    """
    assert len(set(base_images("api"))) == 1


@pytest.mark.parametrize("service", WEB_DEPLOYABLES)
def test_web_image_node_major_satisfies_package_engines(service: str) -> None:
    """Exact-pin floor, read off the package rather than restated."""
    declared = package_json(service)["engines"]["node"]
    floor = int(declared.removeprefix(">=").split(".")[0])

    for image in base_images(service):
        tag = IMAGE_TAG.match(image)
        assert tag, f"base image {image!r} carries no tag"
        assert int(tag.group("tag").split("-")[0]) >= floor


@pytest.mark.parametrize("service", WEB_DEPLOYABLES)
def test_web_image_node_major_satisfies_the_measured_pnpm_floor(service: str) -> None:
    """The real floor is the package manager's, and it is higher than `engines.node`.

    `engines.node` allows 20 and the built app runs there; `pnpm install` does not. See
    `MEASURED_PNPM_NODE_FLOOR`.
    """
    for image in base_images(service):
        tag = IMAGE_TAG.match(image)
        assert tag
        assert int(tag.group("tag").split("-")[0]) >= MEASURED_PNPM_NODE_FLOOR


@pytest.mark.parametrize("service", WEB_DEPLOYABLES)
def test_web_packages_still_pin_the_pnpm_major_this_floor_was_measured_for(service: str) -> None:
    """A pnpm major bump has to re-measure the Node floor instead of inheriting it.

    Without this, `MEASURED_PNPM_NODE_FLOOR` silently degrades from a measurement into a guess
    the moment `packageManager` moves.
    """
    declared = package_json(service)["packageManager"]

    assert declared.startswith(f"pnpm@{MEASURED_PNPM_MAJOR}."), (
        f"{service} pins {declared}; the Node floor above was measured for "
        f"pnpm {MEASURED_PNPM_MAJOR}.x"
    )


@pytest.mark.parametrize("service", WEB_DEPLOYABLES)
def test_web_image_installs_pnpm_through_corepack(service: str) -> None:
    """`--frozen-lockfile` is only a check if the resolver is the one that wrote the lockfile."""
    body = dockerfile(service)

    assert "corepack enable" in body
    assert "pnpm install --frozen-lockfile" in body
    # An explicit `npm i -g pnpm@…` would be a second place the version lives.
    assert "npm install -g pnpm" not in body
    assert "npm i -g pnpm" not in body


def test_both_web_images_share_one_node_base() -> None:
    """Two bases is two things to bump and one to forget."""
    assert set(base_images("admin-web")) == set(base_images("embed"))


# --------------------------------------------------------------------------------------------
# The framing origin is a build input and only a build input
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("variable", FRAMING_ORIGIN_VARS)
def test_embed_image_takes_each_framing_origin_as_a_build_arg(variable: str) -> None:
    """Both names enter at build: both are compiled into the output, neither is read later.

    `output: 'standalone'` bakes `frame-ancestors` from the private name, and Next bakes the
    `NEXT_PUBLIC_` twin — the origin a sizing message is posted to — into the compiled bundle. Two
    variables rather than one so that a build setting only the private name cannot leave the header
    correct while every sizing message goes to the development default.

    Position matters as much as presence: an `ARG` after `pnpm build`, or in a different stage,
    is declared and unread — the build would then silently use the fallback default.
    """
    body = dockerfile("embed")
    lines = body.splitlines()

    arg_index = next(
        (i for i, line in enumerate(lines) if line.strip() == f"ARG {variable}"),
        None,
    )
    assert arg_index is not None, f"embed Dockerfile declares no `ARG {variable}`"

    build_index = next(
        (i for i, line in enumerate(lines) if line.strip() == "RUN pnpm build"), None
    )
    assert build_index is not None, "embed Dockerfile has no `RUN pnpm build`"
    assert arg_index < build_index, "the ARG is declared after the build that has to read it"

    between = [line for line in lines[arg_index:build_index] if line.startswith("FROM ")]
    assert between == [], "a stage boundary sits between the ARG and the build"


def test_no_container_is_given_a_framing_origin_at_runtime() -> None:
    """The other half of the build-input rule, and the one that fails silently.

    Either framing origin in a compose `environment:` block, a ConfigMap or an image `ENV` reads as
    the lever that moves the framing allowlist while doing nothing at all — the standalone output
    never re-reads the Next config, and the `NEXT_PUBLIC_` value was compiled into the bundle. That
    is a dev default reaching production one setting over, so its absence is asserted rather than
    assumed, for both names.
    """
    for service, spec in compose_services().items():
        keys = set(spec.get("environment", {}) or {})
        for variable in FRAMING_ORIGIN_VARS:
            assert variable not in keys, f"service {service} sets {variable} at runtime"

    for service in DEPLOYABLES:
        for line in dockerfile(service).splitlines():
            assigned = assigned_env_keys(line) & set(FRAMING_ORIGIN_VARS)
            assert not assigned, f"{service} assigns {sorted(assigned)} in its image"


def test_the_runtime_guard_separates_the_two_names_and_reads_a_continuation_line() -> None:
    """The negative control for the guard above, which is otherwise a check nobody watched fire.

    Three properties, and the first is the one a widened substring search would not have had: each
    name is matched exactly, so a line setting only the public one is never reported as the private
    one, and a line setting only the private one is still caught. The second is reach — a
    multi-line `ENV A=1 \\` leaves later keys on lines that begin with nothing, which is the embed
    image's runtime stage. The third is that a commented example is not a setting.
    """
    private, public = FRAMING_ORIGIN_VAR, PUBLIC_FRAMING_ORIGIN_VAR

    assert assigned_env_keys(f"ENV {private}=https://chat.example") == {private}
    assert assigned_env_keys(f"ENV {public}=https://chat.example") == {public}

    # The continuation line, and the whole reason the pattern is not anchored to `ENV`.
    assert assigned_env_keys(f"    {public}=https://chat.example \\") == {public}

    # A declared ARG carries no value and is the one permitted form; an ARG with a default is a
    # hardcoded origin in the image, which is the same mistake wearing a different keyword.
    assert assigned_env_keys(f"ARG {public}") == set()
    assert assigned_env_keys(f"ARG {public}=https://chat.example") == {public}

    # The build command in the embed Dockerfile's header comment sets nothing.
    assert assigned_env_keys(f"#   --build-arg {private}=https://chat.example") == set()


@pytest.mark.parametrize("variable", FRAMING_ORIGIN_VARS)
def test_only_the_embed_service_passes_a_framing_origin_as_a_build_arg(variable: str) -> None:
    """One origin, one image, one place either name can enter."""
    passers = {
        service
        for service, spec in compose_services().items()
        if variable in (spec.get("build", {}) or {}).get("args", {})
    }

    assert passers == {"embed"}


def test_admin_image_names_no_framing_origin_at_all() -> None:
    """`frame-ancestors 'none'` names no origin, so no variable can widen it."""
    body = dockerfile("admin-web")
    directives = [
        line.strip()
        for line in body.splitlines()
        if line.strip().startswith(("ARG ", "ENV "))
        and any(variable in line for variable in FRAMING_ORIGIN_VARS)
    ]

    assert directives == []


# --------------------------------------------------------------------------------------------
# The cookie domain reaches every operator-facing origin
# --------------------------------------------------------------------------------------------


def test_documented_hosts_line_covers_every_operator_facing_origin() -> None:
    """`Domain=.noa.internal` either reaches an origin or the session silently does not.

    An origin the hosts line does not name is a URL that resolves nowhere; an origin outside
    `noa.internal` resolves fine and then drops the cookie, which is the worse failure because
    sign-in appears to succeed. Both are checked, and the predicate is exercised on a known-bad
    origin below so that "every origin passed" cannot be vacuously true.
    """
    hosts_line = marked_block("hosts")
    assert len(hosts_line) == 1, "expected exactly one hosts entry"
    address, *hosts = hosts_line[0].split()
    assert address == "127.0.0.1"

    origins = {line.split()[0]: line.split()[1] for line in marked_block("origins")}
    assert set(origins) == {"api", "admin-web", "embed", "librechat"}

    for name, origin in origins.items():
        host = host_of(origin)
        assert under_session_cookie_domain(host), f"{name} sits outside the cookie's domain"
        assert host in hosts, f"{name}'s host {host} is not in the hosts line"

    # Negative control: the two predicates above reject what they are meant to reject.
    assert not under_session_cookie_domain(host_of("http://embed.example.com:3001"))
    assert not under_session_cookie_domain(host_of("https://noa.internal.example.com"))
    assert "embed.example.com" not in hosts


def test_documented_origins_match_the_ports_compose_publishes() -> None:
    """The doc's port and the compose file's port are one fact stated twice.

    Without this, changing a published port leaves `docs/deployment.md` describing an address
    that answers nothing — a dev default seen by an operator, arriving by a different route.
    """
    origins = {line.split()[0]: line.split()[1] for line in marked_block("origins")}
    services = compose_services()

    for service in ("api", "admin-web", "embed"):
        published = [entry.split(":")[-1] for entry in services[service]["ports"]]
        documented = origins[service].rsplit(":", 1)[1]

        assert documented in published, f"{service}: doc says {documented}, compose {published}"


def test_compose_publishes_only_on_loopback() -> None:
    """Dev credentials and no TLS. Loopback publishing is what makes both tolerable."""
    for service, spec in compose_services().items():
        for entry in spec.get("ports", []):
            assert entry.startswith("127.0.0.1:"), f"{service} publishes {entry} on all interfaces"


# --------------------------------------------------------------------------------------------
# The documented commands name real things
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("doc_name", ["README.md", "AGENTS.md", "docs/deployment.md"])
def test_documented_compose_commands_name_real_services_and_profiles(doc_name: str) -> None:
    """`docker compose up -d postgres` was in `README.md` and `AGENTS.md` before any compose
    file existed. This is what stops it drifting back:
    every service and profile an operator is told to type has to exist.
    """
    doc = (REPO_ROOT / doc_name).read_text(encoding="utf-8")
    spec = compose()
    services = set(spec["services"])
    profiles = {
        profile for service in spec["services"].values() for profile in service.get("profiles", [])
    }

    commands = list(COMPOSE_COMMAND.finditer(doc))
    assert commands, f"{doc_name} names no `docker compose up -d` command"

    for command in commands:
        for profile in PROFILE_FLAG.findall(command.group("flags")):
            assert profile in profiles, f"{doc_name}: no service carries profile {profile!r}"
        # A trailing `# …` is the shell's comment, not an argument.
        for argument in command.group("args").split("#")[0].split():
            if argument.startswith("-"):
                continue
            assert argument in services, f"{doc_name}: {argument!r} is not a compose service"


def test_the_profile_free_service_is_the_one_the_docs_tell_you_to_start() -> None:
    """`docker compose up -d postgres` must not build an image to succeed.

    A fresh clone cannot build `admin-web` at all (internal registry), so a profile on
    `postgres` — or the absence of one anywhere else — would break the documented first command
    for a reason unrelated to what it asks for.
    """
    services = compose_services()
    profile_free = {name for name, spec in services.items() if not spec.get("profiles")}

    assert profile_free == {"postgres"}
    assert "build" not in services["postgres"]


# --------------------------------------------------------------------------------------------
# No key material in the compose file or an image layer
# --------------------------------------------------------------------------------------------


def test_compose_hands_no_service_a_secret_it_does_not_read() -> None:
    """Credentials Fernet-encrypted at rest; a container that reads one address should not be
    given three credentials.

    The API takes the whole `.env` because it reads the whole thing; the web services are
    handed their variables individually for exactly this reason.
    """
    for service, spec in compose_services().items():
        keys = set(spec.get("environment", {}) or {})
        leaked = keys & SECRET_ENV_VARS

        assert leaked == set(), f"{service} is handed {sorted(leaked)}"


def test_only_the_api_side_reads_the_shared_env_file() -> None:
    """The web containers read one or two settings each; `env_file` would hand them everything."""
    readers = {service for service, spec in compose_services().items() if spec.get("env_file")}

    assert readers == {"api", "migrate"}


def test_compose_overrides_only_container_topology_values() -> None:
    """One env source (`.env`), overridden only where the container network forces it.

    The rule `apps/*/config/root-env.ts` already states: two places for `NOA_API_URL` to
    disagree is a proxy quietly talking to the wrong API, which is not a failure anyone sees.
    So an override here has to be a service name standing in for `localhost` — anything else is
    a second home for a setting.
    """
    services = compose_services()

    assert services["postgres"]["environment"] == POSTGRES_DEV_ENV

    for service in ("api", "migrate"):
        environment = services[service]["environment"]
        assert set(environment) == {"POSTGRES_URL"}
        assert "@postgres:5432/" in environment["POSTGRES_URL"], "not the service name"

    assert services["admin-web"]["environment"] == {"NOA_API_URL": "http://api:8000"}
    # Read pre-interpolation, deliberately: the assertion is about the FORM, not the value a
    # particular `.env` happens to supply. `${NOA_SIGN_IN_URL:-}` interpolates from `.env` with
    # no default of its own — a default here would be a second truth beside `.env.example`'s,
    # and blank is a valid answer anyway (the 401 card then names the state and offers no link).
    # A literal address in this slot is the failure being excluded.
    assert services["embed"]["environment"] == {
        "NOA_API_URL": "http://api:8000",
        "NOA_SIGN_IN_URL": "${NOA_SIGN_IN_URL:-}",
    }


@pytest.mark.parametrize("service", sorted(DEPLOYABLES))
def test_no_image_can_bake_an_env_file(service: str) -> None:
    """No secrets in git; an image layer is just as permanent and harder to inspect."""
    patterns = dockerignore(service)

    assert ".env" in patterns
    assert ".env.*" in patterns


@pytest.mark.parametrize("service", WEB_DEPLOYABLES)
def test_dockerignore_excludes_no_typescript_the_build_type_checks(service: str) -> None:
    """MEASURED against the compose setup: excluding `tests/` broke the build, and dropping the
    specs would have been worse.

    `tsconfig.json` includes `**/*.ts` and `**/*.tsx` across the whole package and `next build`
    runs that same check, so specs colocated in `src/` import from `tests/support/`. With
    `tests/` out of the context the build failed with `TS2307: Cannot find module
    '../../../../tests/support/approval-card'`. Excluding the specs instead would have made the
    image's type check a strictly smaller program than `pnpm typecheck` — a weaker gate that
    still reads as green, which is the outcome worth a test.
    """
    app_dir = REPO_ROOT / DEPLOYABLES[service]
    patterns = dockerignore(service)
    directory_patterns = [pattern.rstrip("/") for pattern in patterns if pattern.endswith("/")]
    file_patterns = [pattern for pattern in patterns if not pattern.endswith("/")]

    sources = [
        path.relative_to(app_dir)
        for suffix in ("*.ts", "*.tsx")
        for path in app_dir.rglob(suffix)
        if "node_modules" not in path.parts and ".next" not in path.parts
    ]
    assert sources, "no TypeScript found — the walk is wrong, not the package"

    for source in sources:
        parts = source.parts
        for pattern in directory_patterns:
            assert pattern not in parts[:-1], f"{source} is excluded by `{pattern}/`"
        for pattern in file_patterns:
            assert not fnmatch(source.name, pattern), f"{source} is excluded by `{pattern}`"
            assert not fnmatch(str(source), pattern), f"{source} is excluded by `{pattern}`"


# --------------------------------------------------------------------------------------------
# Liveness only, one process
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "route", "port"),
    [("api", "/health", 8000), ("admin-web", "/healthz", 3000), ("embed", "/healthz", 3001)],
)
def test_healthcheck_targets_the_dependency_free_liveness_route(
    service: str, route: str, port: int
) -> None:
    """`/health` answers 200 ok with Postgres down on purpose.

    A healthcheck that reached the database or the API would report that dependency's outage as
    this process being dead, and a restart is then the wrong remedy. The probe address is
    `127.0.0.1` for the same reason — a probe that resolves a service name is testing DNS too.
    """
    body = dockerfile(service)

    assert "HEALTHCHECK" in body, f"{service} declares no healthcheck"

    probes = [line for line in body.splitlines() if f"127.0.0.1:{port}{route}" in line]
    assert len(probes) == 1, f"{service}: expected one probe at 127.0.0.1:{port}{route}"

    probe = probes[0]
    assert "NOA_API_URL" not in probe
    assert "postgres" not in probe
    # The port the probe hits is the port the image actually listens on.
    assert f"{port}" in body.split("EXPOSE")[1].splitlines()[0]


def test_postgres_readiness_gate_is_pg_isready_and_gates_the_migration() -> None:
    """Ordering is where readiness is expressed in this stack, per `docs/deployment.md`.

    `pg_isready` rather than a `SELECT`: a gate that runs a query of its own is a second
    definition of "ready" to keep in step with the first.
    """
    services = compose_services()

    assert "pg_isready" in " ".join(services["postgres"]["healthcheck"]["test"])
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]


def test_api_image_runs_exactly_one_uvicorn_process() -> None:
    """The revisit trigger for zero clock leeway is ">1 API replica", so the image must not quietly
    be that.

    Mint and verify share a clock at zero leeway, and the executor, the reaper, the expiry sweep and
    the list-changed session register are all in-process. `--workers 2` is the trigger tripped
    without anyone deciding to trip it.
    """
    body = dockerfile("api")
    command = next(line for line in body.splitlines() if line.startswith('CMD ["uvicorn"'))

    assert "--workers" not in command
    assert "--reload" not in command
    # `--proxy-headers` without an explicit `--forwarded-allow-ips` lets a client dictate its
    # own source address; enabling both is an ingress decision, recorded in the doc.
    assert "--proxy-headers" not in command


def test_the_workspace_install_does_not_read_a_shared_uv_cache() -> None:
    """A cache mount on the sync that installs `noa-core` ships `core/` from an older commit.

    `--no-editable` builds the two workspace members into wheels, and uv keys a local package's
    rebuild on its `cache-keys`, which default to `pyproject.toml`. A commit that changes only
    `.py` files touches no `pyproject.toml`, so on a runner whose BuildKit cache survives
    between builds that sync reinstalls the wheel built from the *previous* source — while
    `COPY core/ core/` above busts its Docker layer and every layer looks freshly built.

    MEASURED 2026-09-14, not reasoned about: staging image `staging-15707d34` was built from a
    commit whose `core/integrations/whm/availability.py` defines `FIREWALL_PROBE_TARGET`, and
    the running container had no such name and answered with the previous commit's
    `firewall_gate.MESSAGE_SUDO_REQUIRED`. Reproduced locally by building the two commits in
    order against one cache mount, and closed by removing the mount: the same pair of builds
    then put the new code in the second image.

    The dependency sync above keeps its cache. Its input is `uv.lock`, which a code-only commit
    also does not change — and that is correct there, because a locked third-party wheel built
    from an unchanged lock IS the same wheel. The hazard is specific to the local packages,
    whose source is not in the cache key.
    """
    syncs = [run for run in _run_instructions(dockerfile("api")) if "uv sync" in run]
    installs_workspace = [run for run in syncs if "--no-install-workspace" not in run]

    assert len(installs_workspace) == 1, f"expected one workspace-installing sync, got {syncs}"
    assert "--mount=type=cache" not in installs_workspace[0], (
        "the workspace install must not read a shared uv cache — it reinstalls `noa-core` from a "
        "wheel built before this commit's `core/`, and the image ships code it is not tagged with"
    )


def _run_instructions(body: str) -> list[str]:
    """Every `RUN` in one Dockerfile, each with its continuation lines folded into one string."""
    folded = body.replace("\\\n", " ")
    return [" ".join(line.split()) for line in folded.splitlines() if line.startswith("RUN ")]


def test_no_service_declares_more_than_one_replica() -> None:
    """The compose half of the rule above."""
    for service, spec in compose_services().items():
        replicas = (spec.get("deploy", {}) or {}).get("replicas")
        assert replicas in (None, 1), f"{service} declares {replicas} replicas"


def test_deployment_doc_records_the_single_replica_trigger() -> None:
    """The constraint is only useful if the reason travels with it — doc prose bound by test."""
    doc = DEPLOYMENT_DOC.read_text(encoding="utf-8")

    assert "zero clock leeway" in doc
    assert "one clock" in doc
    assert "--workers" in doc
    assert "--forwarded-allow-ips" in doc


# --------------------------------------------------------------------------------------------
# The readiness question the admin auth work parked here
# --------------------------------------------------------------------------------------------


def test_readiness_decision_is_recorded_and_no_longer_deferred() -> None:
    """`docs/admin-web.md` deferred `/readyz` to the deployment artifacts. They answered it: no.

    Both halves are asserted, because recording the decision while leaving the deferral in
    place is how two truths end up live. The deferral used to be a registry pointer, so the
    negative half is now asserted on the DECISION's own words instead — a pointer that no
    longer exists anywhere cannot fail a test.
    """
    deployment = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    admin_web = (REPO_ROOT / "docs" / "admin-web.md").read_text(encoding="utf-8")

    assert "/readyz" in deployment
    assert "no readiness probe" in deployment.lower()

    assert "no readiness probe, and there will not be one" in admin_web, (
        "the decision is not stated where the deferral used to sit"
    )
    assert "docs/deployment.md" in admin_web, "the answer is not pointed at from where it was asked"


def test_docs_index_lists_the_deployment_reference() -> None:
    """A doc nothing points at is a doc nobody reads."""
    index = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")

    assert "deployment.md" in index
