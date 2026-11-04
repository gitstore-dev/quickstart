"""Regression checks for the GitStore 0.1.0-alpha.6+ configuration contract."""

from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseConfigTest(unittest.TestCase):
    def test_base_config_uses_service_grouped_keys(self):
        with (ROOT / "configs/gitstore.toml").open("rb") as config_file:
            config = tomllib.load(config_file)

        self.assertEqual(config["git_service"]["grpc_port"], 50051)
        self.assertEqual(config["api"]["datastore"]["backend"], "memdb")
        self.assertEqual(config["api"]["auth"]["jwt"]["ttl"], "24h")
        self.assertEqual(config["grpc_auth"]["hmac_secret"], "local-development-grpc-hmac-secret")
        self.assertEqual(config["push_limits"], {"max_pack_size": "512MiB", "max_file_size": "100MiB"})
        self.assertNotIn("features", config)
        self.assertNotIn("datastore", config)

    def test_scylla_overlays_use_journal_contract(self):
        for name in ("gitstore.scylla.toml", "gitstore.cluster.toml"):
            with (ROOT / "configs/scylla" / name).open("rb") as config_file:
                config = tomllib.load(config_file)
            journal = config["api"]["watch"]["journal"]
            self.assertEqual(config["api"]["datastore"]["backend"], "scylla")
            self.assertEqual(journal["retention"], "168h")
            self.assertEqual(journal["cdc"]["confidence_window"], "500ms")
            self.assertNotIn("features", config)

    def test_policy_uses_v1beta1_controller_permissions(self):
        policy = (ROOT / "configs/policy.yaml").read_text()
        self.assertIn("apiVersion: rbac.authorization.gitstore.dev/v1beta1", policy)
        self.assertIn("kind: RbacLocalConfiguration", policy)
        self.assertIn("resources: [namespace, repository, categoryTaxonomy, product]", policy)
        self.assertIn("verbs: [purge]", policy)
        self.assertIn("resources: [product/management]", policy)
        self.assertIn("verbs: [read, list]", policy)

    def test_bootstrap_uses_renamed_serviceaccount_variable(self):
        compose = (ROOT / "compose.local.yml").read_text()
        self.assertIn("GITSTORE_API__AUTH__SERVICEACCOUNT__SIGNING_KEY", compose)
        self.assertNotIn("GITSTORE_AUTH__SERVICEACCOUNT__SIGNING_KEY", compose)
        self.assertIn("--record-output-path /run/controller-secrets/gitstore-controller-manager-signing-key.json", compose)
        self.assertIn("--private-key-path /run/controller-secrets/gitstore-controller-manager-signing-key/privateKey", compose)


if __name__ == "__main__":
    unittest.main()
