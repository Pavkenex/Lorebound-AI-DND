"""t_13502038: one-command 'docker compose up' ship path stays valid.

Asserts the required files exist and the run steps are accurate. Pure
file/text checks — no Docker daemon needed, no other-stream imports.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

REQUIRED_FILES = (
    "docker-compose.yml",
    "Dockerfile",
    ".env.example",
    "README.md",
    "backend/Dockerfile",
    "frontend/Dockerfile",
)


def test_required_ship_files_exist():
    missing = [f for f in REQUIRED_FILES if not (REPO / f).exists()]
    assert not missing, f"missing ship files: {missing}"


def test_env_example_covers_backend_settings():
    text = (REPO / ".env.example").read_text()
    for var in ("DATABASE_URL", "REDIS_URL", "JWT_SECRET", "AI_PROVIDER", "AI_MODEL", "ENV"):
        assert var in text, f".env.example missing {var}"


def test_compose_services_and_health():
    text = (REPO / "docker-compose.yml").read_text()
    for service in ("db:", "redis:", "backend:", "frontend:"):
        assert service in text, f"compose missing service {service}"
    assert "8000:8000" in text and "3000:3000" in text
    assert "pg_isready" in text  # db healthcheck gates backend startup


def test_backend_dockerfile_copy_targets_exist():
    lines = (REPO / "backend" / "Dockerfile").read_text().splitlines()
    copies = [ln for ln in lines if ln.startswith("COPY")]
    assert copies, "backend Dockerfile copies nothing"
    # Every bare filename COPY source must exist under backend/.
    for ln in copies:
        for token in ln.split()[1:-1]:
            if "/" in token or token.startswith("--"):
                continue
            assert (REPO / "backend" / token).exists(), (
                f"backend/Dockerfile COPYs missing file: {token}"
            )


def test_readme_run_steps_accurate():
    text = (REPO / "README.md").read_text()
    assert "cp .env.example .env" in text
    assert "docker compose up" in text
    assert "localhost:3000" in text  # frontend reachable
    assert "localhost:8000" in text  # backend reachable
    assert "/health" in text  # health endpoint documented
