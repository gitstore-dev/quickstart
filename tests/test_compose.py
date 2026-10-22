"""Daemon-free checks for the quickstart's public port contract.

Run with: python3 -m unittest discover -s tests -v
"""

import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SECRETS = (
    "HYDRA_DB_PASSWORD", "HYDRA_SECRETS_SYSTEM", "HYDRA_SECRETS_COOKIE",
    "GITSTORE_OIDC_CLIENT_SECRET", "KRATOS_DB_PASSWORD", "KRATOS_SECRETS_COOKIE",
    "KRATOS_SECRETS_DEFAULT", "KRATOS_UI_COOKIE_SECRET", "KRATOS_UI_CSRF_SECRET",
)


def compose(datastore="memdb", profile="single", identity="none", proxy="none", operator=True):
    env = os.environ.copy()
    env.update({name: "test-only-" + "a" * 32 for name in SECRETS})
    env["GITSTORE_PROXY_OPERATOR_USERNAME"] = "operator" if operator else ""
    env["GITSTORE_PROXY_OPERATOR_PASSWORD_HASH"] = (
        "$2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx4IjWJPDhjvG"
        if operator else ""
    )
    # Avoid consuming the developer's private .env, including host port overrides.
    env["COMPOSE_ENV_FILES"] = str(ROOT / "env.example")
    result = subprocess.run(
        ["make", "--no-print-directory", "-s", "config", f"DATASTORE={datastore}",
         f"PROFILE={profile}", f"IDENTITY={identity}", f"PROXY={proxy}",
         "COMPOSE=docker compose --env-file env.example --profile local "
         "-f compose.yml -f compose.local.yml $(DATASTORE_COMPOSE_FILE) "
         "$(IDENTITY_COMPOSE_FILE) $(PROXY_COMPOSE_FILES)"],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )
    return result


class ComposePortsTest(unittest.TestCase):
    def test_all_profile_combinations(self):
        for datastore, profile in (("memdb", "single"), ("scylla", "single"), ("scylla", "cluster")):
            for identity in ("none", "oidc"):
                for proxy in ("none", "caddy"):
                    with self.subTest(datastore=datastore, profile=profile, identity=identity, proxy=proxy):
                        result = compose(datastore, profile, identity, proxy)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        # Compose emits YAML by default; use its own JSON converter.
                        conversion = subprocess.run(
                            ["docker", "compose", "--profile", "local", "-f", "-", "config",
                             "--format", "json", "--no-interpolate"],
                            input=result.stdout, cwd=ROOT, text=True, capture_output=True,
                        )
                        self.assertEqual(conversion.returncode, 0, conversion.stderr)
                        services = json.loads(conversion.stdout)["services"]
                        controller = services["controller-manager"]
                        self.assertNotIn("extra_hosts", controller)
                        self.assertNotIn("GITSTORE_AUTH__OIDC__ISSUER_URI", controller.get("environment", {}))
                        self.assertNotIn("SSL_CERT_FILE", controller.get("environment", {}))
                        self.assertFalse(any(v.get("target") == "/run/proxy-ca"
                                             for v in controller["volumes"]))
                        published = {
                            name: sorted(int(p["published"]) for p in service.get("ports", []))
                            for name, service in services.items() if service.get("ports")
                        }
                        if proxy == "caddy":
                            self.assertEqual(published, {"proxy": [80, 443]})
                            if identity == "oidc":
                                api = services["api"]
                                self.assertEqual(api["environment"]["GITSTORE_AUTH__OIDC__ISSUER_URI"],
                                                 "https://localhost/sso/")
                                self.assertEqual(api["depends_on"]["proxy"]["condition"], "service_healthy")
                                self.assertNotIn("depends_on", services["proxy"])
                                # API sees only the exported certificate, never Caddy's CA keys.
                                self.assertFalse(any(v.get("target") == "/data" for v in api["volumes"]))
                        else:
                            expected = {"api": [4000, 9000]}
                            if identity == "oidc":
                                expected.update(hydra=[4444], kratos=[4433],
                                                **{"kratos-selfservice-ui": [4455],
                                                   "oidc-bridge": [4445], "mailslurper": [4436]})
                            self.assertEqual(published, expected)

    def test_operator_is_required_only_for_proxied_oidc(self):
        for identity, proxy, success in (("oidc", "caddy", False), ("none", "caddy", True),
                                         ("oidc", "none", True)):
            with self.subTest(identity=identity, proxy=proxy):
                result = compose(identity=identity, proxy=proxy, operator=False)
                self.assertEqual(result.returncode == 0, success, result.stderr)

    def test_unknown_proxy_is_rejected(self):
        result = compose(proxy="typo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PROXY must be", result.stderr)


if __name__ == "__main__":
    unittest.main()
