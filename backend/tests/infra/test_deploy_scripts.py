"""Behavioural tests for scripts/deploy.sh and scripts/rollback.sh.

These two scripts decide what code runs in production and what a rollback
returns to, and until 2026-08-23 neither had a single test. Both carried a
defect that made them do the opposite of what their own header promised, and
both were found by hand while updating the reference host:

* **deploy.sh ignored `FH_TAG` from the environment.** It sourced `.env` with
  `set -o allexport` *after* the caller's environment, unconditionally, so
  `FH_TAG=v2.15.0 scripts/deploy.sh` deployed whatever `.env` already said and
  reported success. A deploy tool that silently deploys the wrong version is
  worse than one that fails.
* **deploy.sh's source-build fallback overwrote published images.** Any
  non-zero `docker pull` dropped it into building the working tree and tagging
  it `ghcr.io/<owner>/fileheron-<svc>:$FH_TAG` - so one flaky pull replaced a
  real release with local code wearing its name, and destroyed the only local
  image a rollback could return to.
* **rollback.sh preflighted five images and rolled back four.** The fifth,
  `updater-executor`, is never left on a host by a normal update (the shim
  pulls it per run and `docker run --rm` takes the container with it), so the
  preflight `exit 2`'d and rolled back nothing - the emergency path was
  unavailable exactly when it was needed.

The tests run the REAL scripts against a fake `docker` on PATH that records
invocations, in a throwaway tree. Nothing touches a real daemon, a real
registry, or the live stack.

Verified to discriminate: run against the pre-fix scripts, four of the original
eight fail and four pass, and the four that pass are the deliberate controls.
(This paragraph first claimed five and three; it was measured afterwards and was
wrong - the kind of unverified record this repo keeps having to correct.)

An adversarial review of the first version of the fix then found three more
defects, all covered below: `latest` - the shipped default and, because it is
exempt from pruning, often the only local rollback anchor - was not treated as a
published tag; the new short-circuit made the source-build fallback fire only
once, so a `local-*` hotpatch silently shipped the previous build forever; and an
empty `docker images` aborted both scripts under `set -o pipefail`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = _ROOT / "scripts"
_SERVICES = ("backend", "worker", "frontend", "updater-shim")

_FAKE_DOCKER = """#!/usr/bin/env bash
# Records every invocation to $FAKE_LOG. Behaviour knobs:
#   FAKE_PULL_FAILS=1     -> `docker compose pull` exits 1
#   FAKE_PRESENT="a b"    -> refs that `docker image inspect` resolves
#   FAKE_NO_IMAGES=1      -> `docker images` prints nothing (exit 0), which is
#                            what real docker does for a repo with no local
#                            images - the case that tripped `pipefail`
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "compose pull")
      [ "${FAKE_PULL_FAILS:-0}" = "1" ] && { echo "fake: pull failed" >&2; exit 1; }; exit 0 ;;
  "compose up") exit 0 ;;
  "compose logs") exit 0 ;;
  "image inspect")
      for p in ${FAKE_PRESENT:-}; do [ "$p" = "$3" ] && exit 0; done; exit 1 ;;
esac
case "$1" in
  inspect) echo healthy; exit 0 ;;
  build|tag|rmi|pull) exit 0 ;;
  # Must emit rows: deploy.sh's prune pipes through grep under `set -o
  # pipefail`, and an empty grep exits 1 and would abort the script.
  images)
      [ "${FAKE_NO_IMAGES:-0}" = "1" ] && exit 0
      printf 'v0.9.0 2026-01-01 00:00:00 +0000 UTC\\nlatest 2026-01-01 00:00:00 +0000 UTC\\n'; exit 0 ;;
esac
exit 0
"""

_FAKE_GIT = """#!/usr/bin/env bash
[ "$1" = "rev-parse" ] && { echo deadbeefcafe; exit 0; }
exit 0
"""


def _images(tag: str) -> str:
    return " ".join(f"ghcr.io/phoen-ix/fileheron-{s}:{tag}" for s in _SERVICES)


@pytest.fixture
def harness(tmp_path: Path):
    binp = tmp_path / "bin"
    binp.mkdir()
    (binp / "docker").write_text(_FAKE_DOCKER)
    (binp / "git").write_text(_FAKE_GIT)
    for f in binp.iterdir():
        f.chmod(0o755)
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    for name in ("deploy.sh", "rollback.sh"):
        shutil.copy(_SCRIPTS / name, tree / "scripts" / name)
    log = tmp_path / "log"

    def run(script: str, *args: str, **env: str):
        # rollback.sh REWRITES .env (it pins FH_TAG there on purpose), so each
        # run starts from a known one or the cases poison each other.
        if env.pop("FAKE_NO_FH_TAG", None):
            (tree / ".env").write_text("OTHER=x\n")
        else:
            (tree / ".env").write_text("FH_TAG=v1.0.0\nOTHER=x\n")
        log.write_text("")
        e = {
            **os.environ,
            "PATH": f"{binp}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
        }
        e.pop("FH_TAG", None)
        e.update(env)
        # S603/S607: argv is an absolute bash resolved here plus a path built
        # from this file's own constants - no untrusted input reaches it. Same
        # shape as tests/test_promote_user_script.py.
        bash = shutil.which("bash")
        assert bash, "bash is required to exercise the deploy scripts"
        p = subprocess.run(  # noqa: S603
            [bash, str(tree / "scripts" / script), *args],
            capture_output=True, text=True, env=e, timeout=120,
        )
        return p.returncode, p.stdout + p.stderr, log.read_text()

    return run


# --- deploy.sh: the environment must beat .env -------------------------------

def test_caller_fh_tag_beats_dotenv(harness) -> None:
    """`FH_TAG=x scripts/deploy.sh` must deploy x, not what .env says.

    This is docker compose's own precedence and what deploy.sh's header has
    always claimed. Sourcing .env with allexport silently reversed it.
    """
    rc, out, _ = harness("deploy.sh", FH_TAG="v9.9.9", FAKE_PRESENT=_images("v9.9.9"))
    assert "target tag: v9.9.9" in out, out
    assert rc == 0, out


def test_dotenv_is_still_used_when_the_environment_says_nothing(harness) -> None:
    """Control: the fix must not break the ordinary path."""
    rc, out, _ = harness("deploy.sh", FAKE_PRESENT=_images("v1.0.0"))
    assert "target tag: v1.0.0" in out, out
    assert rc == 0, out


# --- deploy.sh: the fallback must not overwrite a published release ----------

def test_failed_pull_with_images_already_local_does_not_rebuild(harness) -> None:
    """Retrying a deploy of a tag you already have must not build anything."""
    rc, out, log = harness(
        "deploy.sh", FH_TAG="v2.0.0", FAKE_PULL_FAILS="1", FAKE_PRESENT=_images("v2.0.0")
    )
    assert rc == 0, out
    assert "build " not in log, f"rebuilt despite having the images:\n{log}"


def test_failed_pull_of_a_release_tag_refuses_to_build(harness) -> None:
    """The one that destroyed the rollback anchor.

    A source build is not the release it would be tagged as, and tagging it so
    overwrites the published image locally. Fail loudly instead.
    """
    rc, out, log = harness(
        "deploy.sh", FH_TAG="v3.0.0", FAKE_PULL_FAILS="1", FAKE_PRESENT=""
    )
    assert rc == 3, f"expected refusal (exit 3), got {rc}:\n{out}"
    assert "build " not in log, f"built a release tag from source:\n{log}"


def test_bootstrap_build_still_works_for_a_non_release_tag(harness) -> None:
    """Control: the fallback exists to bootstrap a host, and must survive."""
    rc, out, log = harness(
        "deploy.sh", FH_TAG="local-deadbeef", FAKE_PULL_FAILS="1", FAKE_PRESENT=""
    )
    assert rc == 0, out
    assert "build " in log, f"bootstrap path no longer builds:\n{out}"
    # Assert the TAG, not just that a build happened: pre-fix this same control
    # passed while building `…:v1.0.0`, the clobbered .env tag, which is bug #1
    # wearing a green test.
    assert "fileheron-backend:local-deadbeef" in log, (
        f"built under the wrong tag - .env clobbered the caller again:\n{log}"
    )


# --- rollback.sh: preflight only what it actually rolls ----------------------

def test_absent_updater_executor_does_not_block_a_rollback(harness) -> None:
    """Absent is the NORMAL state for that image; it must never block."""
    rc, out, log = harness("rollback.sh", "v1.2.3", FAKE_PRESENT=_images("v1.2.3"))
    assert rc == 0, f"rollback refused with only the executor missing:\n{out}"
    assert "compose up -d" in log, f"nothing was rolled:\n{log}"
    assert "not local" in out, "should say the executor is absent, as a note"


def test_a_missing_service_image_still_blocks_the_rollback(harness) -> None:
    """Control: the preflight must still refuse when it genuinely cannot roll."""
    rc, out, log = harness(
        "rollback.sh", "v1.2.3", FAKE_PRESENT="ghcr.io/phoen-ix/fileheron-backend:v1.2.3"
    )
    assert rc == 2, f"expected exit 2, got {rc}:\n{out}"
    assert "compose up -d" not in log, f"rolled despite a missing image:\n{log}"


# --- the derivation that stops the two lists drifting again ------------------

def test_the_image_list_derives_from_the_service_list(harness) -> None:
    """`fileheron-<service>` must match what docker-compose.yml actually runs.

    Both scripts now derive their image names from SERVICES instead of keeping
    a second hand-written list. That derivation is only safe while the naming
    holds, so pin it against compose rather than assuming.
    """
    compose = (_ROOT / "docker-compose.yml").read_text()
    for svc in _SERVICES:
        assert f"ghcr.io/phoen-ix/fileheron-{svc}:" in compose, (
            f"service {svc} does not run image fileheron-{svc}; the derivation in "
            "deploy.sh/rollback.sh no longer holds"
        )


# --- the three defects the adversarial review found in the first fix ---------

def test_latest_is_treated_as_a_published_tag(harness) -> None:
    """`latest` is CI-maintained, is the shipped default, and is exempt from the
    prune - so on a stock self-host it is the ONLY local rollback anchor.

    The first version of the guard matched `^v\\d+\\.\\d+\\.\\d+$` only, so a
    flaky pull under the default configuration still built the working tree over
    published images and exited 0.
    """
    rc, out, log = harness("deploy.sh", FH_TAG="latest", FAKE_PULL_FAILS="1", FAKE_PRESENT="")
    assert rc == 3, f"expected refusal for :latest, got {rc}:\n{out}"
    assert "build " not in log, f"built over the :latest anchor:\n{log}"


def test_latest_proceeds_when_every_image_is_already_local(harness) -> None:
    """Control: a registry blip on :latest with the images present is fine."""
    rc, out, log = harness(
        "deploy.sh", FH_TAG="latest", FAKE_PULL_FAILS="1", FAKE_PRESENT=_images("latest")
    )
    assert rc == 0, out
    assert "build " not in log, log


def test_a_published_tag_with_only_some_images_local_refuses(harness) -> None:
    """A partial pull must not roll a mixed-version stack.

    `HAVE_ALL` requires all four at the target tag; three-of-four is a refusal,
    not a deploy.
    """
    partial = " ".join(
        f"ghcr.io/phoen-ix/fileheron-{s}:v4.0.0" for s in ("backend", "worker", "frontend")
    )
    rc, out, log = harness(
        "deploy.sh", FH_TAG="v4.0.0", FAKE_PULL_FAILS="1", FAKE_PRESENT=partial
    )
    assert rc == 3, f"expected refusal on a partial set, got {rc}:\n{out}"
    assert "compose up -d" not in log, f"rolled a mixed-version stack:\n{log}"


def test_the_source_build_fallback_is_repeatable(harness) -> None:
    """A `local-*` tag can NEVER be pulled, so it must rebuild every time.

    The first fix short-circuited on "the images are already here", which made
    run 2 silently ship run 1's binaries while printing 'done' - every later
    edit invisible. That is the deploys-the-wrong-thing-and-reports-success
    failure this whole change exists to prevent.
    """
    rc1, out1, log1 = harness(
        "deploy.sh", FH_TAG="local-abc", FAKE_PULL_FAILS="1", FAKE_PRESENT=""
    )
    assert rc1 == 0 and "build " in log1, out1
    # Second run: images now exist locally at that tag.
    rc2, out2, log2 = harness(
        "deploy.sh", FH_TAG="local-abc", FAKE_PULL_FAILS="1", FAKE_PRESENT=_images("local-abc")
    )
    assert rc2 == 0, out2
    assert "build " in log2, (
        "the fallback became one-shot: run 2 shipped run 1's build without "
        f"rebuilding, so an edited tree would never reach the stack:\n{out2}"
    )


def test_deploy_survives_an_empty_image_list(harness) -> None:
    """`grep -v` exits 1 when it filters everything away; under `set -o pipefail`
    that aborted the prune AFTER a successful deploy - "healthy", then exit 1.
    """
    rc, out, _ = harness(
        "deploy.sh", FH_TAG="v5.0.0", FAKE_PRESENT=_images("v5.0.0"), FAKE_NO_IMAGES="1"
    )
    assert rc == 0, f"a successful deploy exited {rc} in the prune:\n{out}"
    assert "done" in out, f"never reached the done line:\n{out}"


def test_rollback_listing_survives_an_empty_image_list(harness) -> None:
    """`scripts/rollback.sh` with no args is the first command run in an
    incident; it must not die on a repo with no local images."""
    rc, out, _ = harness("rollback.sh", FAKE_NO_IMAGES="1")
    assert rc == 0, f"the listing exited {rc}:\n{out}"


def test_missing_image_still_exits_2_with_an_empty_image_list(harness) -> None:
    """The documented exit 2 must survive the listing it prints on the way out."""
    rc, out, _ = harness("rollback.sh", "v1.2.3", FAKE_PRESENT="", FAKE_NO_IMAGES="1")
    assert rc == 2, f"expected the documented exit 2, got {rc}:\n{out}"


def test_an_exported_empty_fh_tag_does_not_silently_fall_through(harness) -> None:
    """`FH_TAG=""` is a caller value, not "unset" - it comes out of a lookup that
    returned nothing. Compose resolves an empty one to the default, so match it
    rather than silently using .env."""
    rc, out, _ = harness("deploy.sh", FH_TAG="", FAKE_PRESENT=_images("latest"))
    assert "target tag: latest" in out, (
        f"an exported-empty FH_TAG fell through to .env instead of the default:\n{out}"
    )
    assert rc == 0, out


def test_a_caller_tag_that_differs_from_dotenv_is_called_out(harness) -> None:
    """.env is what `docker compose up -d` and the updater's rollback anchor
    read, so a deploy that does not match it must say so."""
    _, out, _ = harness("deploy.sh", FH_TAG="v9.9.9", FAKE_PRESENT=_images("v9.9.9"))
    assert ".env resolves to" in out, f"no drift warning:\n{out}"


def test_no_drift_warning_when_dotenv_resolves_to_the_same_tag(harness, tmp_path) -> None:
    """Every image line in docker-compose.yml is `${FH_TAG:-latest}`, so a .env
    with no FH_TAG resolves to `latest` - deploying `latest` there reverts to
    nothing and must not warn. Comparing raw values instead of compose-resolved
    ones made this fire on a perfectly consistent host."""
    rc, out, _ = harness(
        "deploy.sh", FH_TAG="latest", FAKE_PRESENT=_images("latest"), FAKE_NO_FH_TAG="1"
    )
    assert rc == 0, out
    assert "NOTE: deploying" not in out, f"false drift warning:\n{out}"


def test_a_published_tag_missing_only_its_first_image_refuses(harness) -> None:
    """Guards the accumulator, not just the outcome.

    `HAVE_ALL` is set once before the loop and only ever cleared inside it.
    Hoisting the initialiser INTO the loop makes it last-iteration-wins, which
    still passes a partial-presence test whose missing image happens to be last.
    This one omits the FIRST service, so that mutation is caught.
    """
    partial = " ".join(
        f"ghcr.io/phoen-ix/fileheron-{s}:v6.0.0"
        for s in ("worker", "frontend", "updater-shim")   # backend absent
    )
    rc, out, log = harness(
        "deploy.sh", FH_TAG="v6.0.0", FAKE_PULL_FAILS="1", FAKE_PRESENT=partial
    )
    assert rc == 3, f"expected refusal when only the first image is missing, got {rc}:\n{out}"
    assert "compose up -d" not in log, f"rolled a mixed-version stack:\n{log}"


def test_both_scripts_agree_on_the_service_list(harness) -> None:
    """The de-duplication only helps if the two scripts still match each other.

    The five-versus-four bug came from two hand-written lists drifting. Each
    script now derives its images from its OWN SERVICES array, so a drift
    between the two scripts would reintroduce exactly the same class of defect
    one level up.
    """
    import re

    def services(path):
        m = re.search(r"^SERVICES=\(([^)]*)\)", (_SCRIPTS / path).read_text(), re.M)
        assert m, f"no SERVICES array in {path}"
        return tuple(m.group(1).split())

    d, r = services("deploy.sh"), services("rollback.sh")
    assert d == r, f"deploy.sh {d} != rollback.sh {r}"
    assert d == _SERVICES, (
        f"this test file's own _SERVICES {_SERVICES} has drifted from the scripts {d} - "
        "it is a third copy of the same list and must track them"
    )
