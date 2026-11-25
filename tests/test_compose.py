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


def compose(datastore="memdb", profile="single", identity="none", proxy="none", operator=True,
            tag="latest", bridge_tag="", cluster_prefix="172.30.11", cluster_ips=None):
    env = os.environ.copy()
    env["GITSTORE_TAG"] = tag
    env["GITSTORE_OIDC_BRIDGE_TAG"] = bridge_tag
    env["SCYLLA_CLUSTER_SUBNET_PREFIX"] = cluster_prefix
    for i in range(1, 4):
        env[f"SCYLLA_CLUSTER_NODE_{i}_IP"] = cluster_ips[i - 1] if cluster_ips else ""
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
    def test_coordinated_release_tags_and_bridge_override(self):
        for tag, bridge_tag, expected in (
            ("latest", "", "latest"),
            ("0.1.0-alpha.11", "", "0.1.0-alpha.11"),
            ("0.1.0-alpha.11", "custom-bridge", "custom-bridge"),
        ):
            with self.subTest(tag=tag, bridge_tag=bridge_tag):
                result = compose(identity="oidc", tag=tag, bridge_tag=bridge_tag)
                self.assertEqual(result.returncode, 0, result.stderr)
                for image in ("api", "git-service", "controller-manager"):
                    self.assertIn(f"image: ghcr.io/gitstore-dev/{image}:{tag}", result.stdout)
                self.assertIn(f"image: gitstore-quickstart-bootstrap:{tag}", result.stdout)
                self.assertIn(f"image: ghcr.io/gitstore-dev/oidc-bridge:{expected}", result.stdout)

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
                        resolved = json.loads(conversion.stdout)
                        services = resolved["services"]
                        if datastore == "scylla" and profile == "cluster":
                            self.assertEqual(resolved["networks"]["gitstore"]["ipam"]["config"],
                                             [{"subnet": "172.30.11.0/24"}])
                            for i in range(1, 4):
                                self.assertEqual(services[f"scylla-{i}"]["networks"]["gitstore"]["ipv4_address"],
                                                 f"172.30.11.{10 + i}")
                        else:
                            self.assertNotIn("ipam", resolved["networks"]["gitstore"])
                        api = services["api"]
                        expected_configs = ["/etc/gitstore/gitstore.toml"]
                        if datastore == "scylla":
                            suffix = "cluster" if profile == "cluster" else "scylla"
                            expected_configs.append(f"/etc/gitstore/scylla/gitstore.{suffix}.toml")
                        if identity == "oidc":
                            expected_configs.append("/etc/gitstore/oidc/gitstore.oidc.toml")
                        self.assertEqual(api["command"], ["/app/api"] + [
                            arg for path in expected_configs for arg in ("--config-file", path)
                        ] + ["--serviceaccount-signing-key-file", "/run/secrets/serviceaccount-signing-key"])
                        self.assertEqual(api["healthcheck"]["test"],
                                         ["CMD", "/app/api", "gitctl", "healthcheck"])
                        self.assertEqual(services["serviceaccount-enrollment"]["command"][:3],
                                         ["/app/api", "gitctl", "enroll-serviceaccount"])
                        self.assertEqual(services["serviceaccount-enrollment"]["user"], "0:0")
                        controller = services["controller-manager"]
                        self.assertNotIn("extra_hosts", controller)
                        self.assertNotIn("GITSTORE_API__AUTH__OIDC_JWT__ISSUER_URI", controller.get("environment", {}))
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
                                self.assertEqual(api["environment"]["GITSTORE_API__AUTH__OIDC_JWT__ISSUER_URI"],
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

    def test_cluster_subnet_and_existing_node_addresses_can_be_preserved(self):
        addresses = ["172.30.12.21", "172.30.12.24", "172.30.12.25"]
        result = compose(datastore="scylla", profile="cluster", cluster_prefix="172.30.12",
                         cluster_ips=addresses)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("subnet: 172.30.12.0/24", result.stdout)
        for address in addresses:
            self.assertIn(f"ipv4_address: {address}", result.stdout)

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
