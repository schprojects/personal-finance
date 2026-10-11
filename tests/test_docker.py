"""Tests for the container deployment (Dockerfile, docker-compose.yml).

Static tests always run. The integration class builds and runs the image and is
opt-in: set RUN_DOCKER_TESTS=1 (needs a reachable docker daemon + network)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PERSONAL = ("data", "var", "output", "backup", "members.yaml", "auto-rules.csv", "dist", "dist.zip")


def read(name):
    return (ROOT / name).read_text()


class TestContainerFiles(unittest.TestCase):
    def test_dockerfile_shape(self):
        d = read("Dockerfile")
        self.assertRegex(d, r"(?m)^FROM python:[\d.]+-slim")
        self.assertIn("pip install -r requirements.txt", d)
        self.assertRegex(d, r"(?m)^USER app$")  # not root
        self.assertIn("EXPOSE 8501", d)
        self.assertIn("streamlit", d.split("ENTRYPOINT")[1])
        self.assertIn("--server.address=0.0.0.0", d)
        self.assertIn("HEALTHCHECK", d)

    def test_image_copies_only_app_code_never_personal_data(self):
        copies = re.findall(r"(?m)^COPY (.+)$", read("Dockerfile"))
        sources = {tok for line in copies for tok in line.split()[:-1]}
        self.assertEqual(sources, {"requirements.txt", "personal-finance.py",
                                   "impex.py", "ui_theme.py", ".streamlit", "finance",
                                   "backend"})

    def test_dockerignore_excludes_personal_data(self):
        lines = {l.strip() for l in read(".dockerignore").splitlines()}
        for item in PERSONAL:
            self.assertIn(item, lines, f".dockerignore must exclude {item}")

    def test_compose_services_and_mounts(self):
        c = yaml.safe_load(read("docker-compose.yml"))
        app, tool = c["services"]["app"], c["services"]["impex"]
        self.assertIn("8501:8501", app["ports"])
        mounts = {v.split(":")[1] for v in app["volumes"]}
        self.assertEqual(mounts, {"/app/var", "/app/data", "/app/output",
                                  "/app/backup", "/app/members.yaml",
                                  "/app/auto-rules.csv"})
        members = next(v for v in app["volumes"] if "members.yaml" in v)
        self.assertTrue(members.endswith(":ro"))
        self.assertEqual(app["environment"]["FINANCE_DB"], "/app/var/networth.db")
        # the CLI shares image + mounts but only runs on demand
        self.assertEqual(tool["volumes"], app["volumes"])
        self.assertEqual(tool["profiles"], ["tools"])
        self.assertEqual(tool["entrypoint"], ["python", "impex.py"])
        self.assertNotIn("ports", tool)

    def test_compose_declares_no_personal_data_in_environment(self):
        self.assertNotRegex(read("docker-compose.yml").lower(),
                            r"password|api_key|secret")

    def test_dist_bundle_ships_container_files(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for name in ("personal-finance.py", "impex.py", "ui_theme.py", "README.md",
                         "requirements.txt", "Dockerfile", "docker-compose.yml",
                         ".dockerignore", "auto-rules.example.csv", "members.example.yaml", "LICENSE"):
                shutil.copy(ROOT / name, root / name)
            for d in ("finance", "backend", ".streamlit"):
                shutil.copytree(ROOT / d, root / d,
                                ignore=shutil.ignore_patterns("__pycache__"))
            (root / "scripts").mkdir()
            shutil.copy(ROOT / "scripts/make_dist.py", root / "scripts/make_dist.py")
            r = subprocess.run([sys.executable, "scripts/make_dist.py"], cwd=root,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name in ("Dockerfile", "docker-compose.yml", ".dockerignore",
                         "ui_theme.py", ".streamlit/config.toml", "LICENSE",
                         "members.example.yaml", "auto-rules.example.csv"):
                self.assertTrue((root / "dist" / name).exists(), name)


class TestLicenseAndPins(unittest.TestCase):
    def test_license_is_zero_clause_bsd_and_has_no_attribution_condition(self):
        text = read("LICENSE")
        self.assertIn("BSD Zero Clause License", text)
        self.assertIn("with or without fee", text)
        self.assertNotIn("Redistributions of source code must retain", text)

    def test_requirements_are_pinned(self):
        lines = [l for l in read("requirements.txt").splitlines() if l.strip()]
        self.assertTrue(lines and all("==" in l for l in lines), lines)


def _docker_ready():
    if os.environ.get("RUN_DOCKER_TESTS") != "1" or not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@unittest.skipUnless(_docker_ready(), "set RUN_DOCKER_TESTS=1 with a docker daemon")
class TestContainerRuns(unittest.TestCase):
    """Build once, then drive the CLI and the dashboard in containers against
    host-supplied data (nothing baked into the image)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.work = Path(cls.tmp)
        cls.tag = f"personal-finance-test:{os.getpid()}"
        for name in ("Dockerfile", "docker-compose.yml", ".dockerignore",
                     "requirements.txt", "personal-finance.py", "impex.py",
                     "ui_theme.py"):
            shutil.copy(ROOT / name, cls.work / name)
        for d in ("finance", "backend", ".streamlit"):
            shutil.copytree(ROOT / d, cls.work / d,
                            ignore=shutil.ignore_patterns("__pycache__"))
        subprocess.run(["docker", "build", "-t", cls.tag, "."], cwd=cls.work,
                       check=True, capture_output=True, timeout=900)
        (cls.work / "members.yaml").write_text(
            "members:\n  - name: alice\n    data_dir: data/alice\n")
        stmts = cls.work / "data/alice/bank-statements/2024-25"
        stmts.mkdir(parents=True)
        (stmts / "s.csv").write_text(
            '\n"Statement of Transactions in SavingsNumber: XX1"\n\n'
            "DATE,MODE,PARTICULARS,DEPOSITS,WITHDRAWALS,BALANCE\n"
            "01-04-2024,,B/F,0,0,1000\n02-04-2024,,UPI/SHOP,0,100,900\n"
            "03-04-2024,,ACH/SALARY,250,0,1150\n")
        for d in ("var", "output", "backup"):
            (cls.work / d).mkdir()
            os.chmod(cls.work / d, 0o777)  # container user is uid 1000

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker", "rmi", "-f", cls.tag], capture_output=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def mounts(self):
        w = self.work
        return ["-v", f"{w}/var:/app/var", "-v", f"{w}/data:/app/data",
                "-v", f"{w}/output:/app/output", "-v", f"{w}/backup:/app/backup",
                "-v", f"{w}/members.yaml:/app/members.yaml:ro"]

    def impex(self, *args, stdin="pw\n"):
        return subprocess.run(
            ["docker", "run", "--rm", "-i", *self.mounts(),
             "--entrypoint", "python", self.tag, "impex.py", *args,
             "--password-stdin"] if args[0] != "backup" else
            ["docker", "run", "--rm", *self.mounts(), "--entrypoint", "python",
             self.tag, "impex.py", *args],
            input=stdin, capture_output=True, text=True, timeout=120)

    def test_cli_import_export_backup_and_app_health(self):
        r = self.impex("auth", "--user", "alice")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.impex("import", "--user", "alice", "--type", "bank-statement")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("'created': 2", r.stdout)
        # data persisted on the host, not in the container
        self.assertTrue((self.work / "var/networth.db").exists())
        r = self.impex("export", "--login", "alice", "--type", "bank")
        self.assertIn("UPI/SHOP", r.stdout)
        # a wrong password fails and is logged on the host
        bad = self.impex("export", "--login", "alice", stdin="nope\n")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("auth-failure", (self.work / "var/logs/auth.log").read_text())
        r = self.impex("backup")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(list((self.work / "backup").glob("networth-*.db")))

        name = f"pf-test-{os.getpid()}"
        subprocess.run(["docker", "run", "-d", "--name", name, "-p", "18501:8501",
                        *self.mounts(), self.tag], check=True,
                       capture_output=True, timeout=60)
        try:
            ok = False
            for _ in range(30):
                try:
                    with urllib.request.urlopen(
                            "http://localhost:18501/_stcore/health", timeout=2) as h:
                        ok = h.read().strip() == b"ok"
                        break
                except OSError:
                    time.sleep(1)
            self.assertTrue(ok, "dashboard container never became healthy")
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    def test_image_contains_no_personal_data(self):
        r = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "sh", self.tag, "-c",
             "ls /app; test ! -e /app/members.yaml && ls /app/data /app/var"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        listing = r.stdout.split()
        self.assertNotIn("tests", listing)
        self.assertNotIn("members.yaml", listing)


if __name__ == "__main__":
    unittest.main()
