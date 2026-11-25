"""Opt-in retained-volume cluster regression; creates and removes its own test data.

Run with GITSTORE_TEST_SCYLLA_RESTART=1 python3 -m unittest discover -s tests
-p test_scylla_restart_live.py -v. Requires Docker and the Scylla image.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

from test_compose import ROOT, compose


@unittest.skipUnless(os.environ.get("GITSTORE_TEST_SCYLLA_RESTART") == "1",
                     "live Scylla restart test not configured")
class ScyllaRestartTest(unittest.TestCase):
    def test_recreation_preserves_node_identity_and_replicated_data(self):
        project = "qs-scylla-restart-" + uuid.uuid4().hex[:10]
        prefix = os.environ.get("GITSTORE_TEST_SCYLLA_SUBNET_PREFIX", "172.30.211")
        with tempfile.TemporaryDirectory(prefix=project) as directory:
            result = compose(datastore="scylla", profile="cluster", cluster_prefix=prefix)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads(self.run_command(
                ["docker", "compose", "-f", "-", "config", "--format", "json", "--no-interpolate"],
                input=result.stdout,
            ))
            # Exercise the maintained node definitions without starting application services.
            config["services"] = {f"scylla-{i}": config["services"][f"scylla-{i}"]
                                  for i in range(1, 4)}
            config["name"] = project
            config["networks"]["gitstore"]["name"] = project
            config["volumes"] = {f"scylla-{i}-data": {"name": f"{project}-node-{i}"}
                                 for i in range(1, 4)}
            config_file = Path(directory) / "compose.json"
            config_file.write_text(json.dumps(config))
            command = ["docker", "compose", "--project-name", project, "-f", str(config_file)]

            def dc(*args):
                return self.run_command(command + list(args))

            def cql(statement):
                return dc("exec", "-T", "scylla-1", "cqlsh", "-e", statement)

            def identities():
                values = {}
                for i in range(1, 4):
                    container = dc("ps", "--quiet", f"scylla-{i}").strip()
                    details = json.loads(self.run_command(["docker", "inspect", container]))[0]
                    address = details["NetworkSettings"]["Networks"][project]["IPAddress"]
                    host_id = dc("exec", "-T", f"scylla-{i}", "cqlsh", "-e",
                                 "SELECT host_id FROM system.local;")
                    values[f"scylla-{i}"] = (address, host_id)
                return values

            try:
                dc("up", "--detach", "--wait", "--wait-timeout", "300")
                original = identities()
                cql("CREATE KEYSPACE restart_probe WITH replication = "
                    "{'class':'NetworkTopologyStrategy','datacenter1':3}; "
                    "CREATE TABLE restart_probe.marker (id int PRIMARY KEY, value text); "
                    "CONSISTENCY ALL; INSERT INTO restart_probe.marker (id,value) "
                    "VALUES (1,'retained-after-recreation');")

                # Remove containers AND their network while retaining all three data volumes.
                dc("down")
                # Allocate peers in reverse order; dynamic IPs can otherwise swap silently.
                dc("up", "--detach", "--wait", "--wait-timeout", "300", "scylla-3")
                dc("up", "--detach", "--wait", "--wait-timeout", "300")
                self.assertEqual(identities(), original)

                # ALL proves that the restarted cluster can read the replicated marker.
                deadline = time.monotonic() + 60
                while True:
                    try:
                        output = cql("CONSISTENCY ALL; SELECT value FROM restart_probe.marker WHERE id=1;")
                        self.assertIn("retained-after-recreation", output)
                        break
                    except AssertionError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(2)
            except BaseException:
                logs = subprocess.run(command + ["logs", "--no-color", "--tail", "100"],
                                      cwd=ROOT, text=True, capture_output=True, timeout=30)
                print(logs.stdout)
                raise
            finally:
                # The generated config contains only this test's unique volumes/network.
                dc("down", "--volumes", "--remove-orphans")

    def run_command(self, args, **kwargs):
        result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                                timeout=420, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


if __name__ == "__main__":
    unittest.main()
