"""Opt-in checks against a running PROXY=caddy IDENTITY=oidc stack.

Set GITSTORE_TEST_PROXY_CA to the exported CA path, and
GITSTORE_TEST_OPERATOR_USERNAME / GITSTORE_TEST_OPERATOR_PASSWORD to a test operator.
These tests do not create users, clients, or repositories.
"""

import base64
import http.cookiejar
import json
import os
import ssl
import unittest
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@unittest.skipUnless(os.environ.get("GITSTORE_TEST_PROXY_CA"), "live proxy not configured")
class LiveProxyTest(unittest.TestCase):
    def setUp(self):
        context = ssl.create_default_context(cafile=os.environ["GITSTORE_TEST_PROXY_CA"])
        self.cookies = http.cookiejar.CookieJar()
        self.client = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect(),
        )

    def request(self, path, auth=None, method="GET", scheme="https", body=None, headers=None):
        headers = dict(headers or {})
        if auth:
            headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
        req = urllib.request.Request(f"{scheme}://localhost{path}", headers=headers, method=method,
                                     data=body)
        try:
            return self.client.open(req, timeout=15)
        except urllib.error.HTTPError as response:
            return response

    def test_http_redirect_preserves_target(self):
        with self.request("/admin/api?probe=1", method="POST", scheme="http") as response:
            self.assertEqual(response.status, 308)
            self.assertEqual(response.headers["Location"], "https://localhost/admin/api?probe=1")

    @unittest.skipUnless(os.environ.get("GITSTORE_TEST_ADMIN_PASSWORD"), "native API login not configured")
    def test_graphql_keeps_native_auth(self):
        username = os.environ.get("GITSTORE_TEST_ADMIN_USERNAME", "admin")
        password = os.environ["GITSTORE_TEST_ADMIN_PASSWORD"]
        login = {
            "query": "mutation($input: LoginInput!) { login(input: $input) { token { accessToken } } }",
            "variables": {"input": {"username": username, "password": password}},
        }
        with self.request("/admin/api", method="POST", body=json.dumps(login).encode(),
                          headers={"Content-Type": "application/json"}) as response:
            self.assertEqual(response.status, 200)
            self.assertNotIn("WWW-Authenticate", response.headers)
            data = json.load(response)
            self.assertNotIn("errors", data)
            token = data["data"]["login"]["token"]["accessToken"]
        query = {"query": "{ namespaces(first: 1) { edges { node { metadata { name } } } } }"}
        with self.request("/admin/api", method="POST", body=json.dumps(query).encode(),
                          headers={"Content-Type": "application/json",
                                   "Authorization": "Bearer " + token}) as response:
            self.assertEqual(response.status, 200)
            data = json.load(response)
            self.assertNotIn("errors", data)
            self.assertIn("namespaces", data["data"])

    def test_identity_admin_requires_operator(self):
        username = os.environ.get("GITSTORE_TEST_OPERATOR_USERNAME")
        password = os.environ.get("GITSTORE_TEST_OPERATOR_PASSWORD")
        for path in ("/admin/sso/clients", "/admin/user/identities"):
            with self.subTest(path=path):
                for auth in (None, "invalid:invalid"):
                    with self.request(path, auth=auth) as response:
                        self.assertEqual(response.status, 401)
                        self.assertIn("Basic", response.headers["WWW-Authenticate"])
                if username and password:
                    with self.request(path, auth=f"{username}:{password}") as response:
                        self.assertEqual(response.status, 200)
                        self.assertIsInstance(json.load(response), list)

    def test_admin_paths_cannot_bypass_auth(self):
        for path in ("/admin/clients", "/admin/identities", "/admin/ssoevil/clients",
                     "/admin/userevil/identities", "/sso/admin/clients",
                     "/user/api/admin/identities"):
            with self.subTest(path=path), self.request(path) as response:
                self.assertEqual(response.status, 404)

    def test_oidc_discovery_and_jwks(self):
        with self.request("/sso/.well-known/openid-configuration") as response:
            self.assertEqual(response.status, 200)
            discovery = json.load(response)
        self.assertEqual(discovery["issuer"], "https://localhost/sso/")
        for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"):
            self.assertTrue(discovery[key].startswith("https://localhost/sso/"), discovery[key])
        with self.request(urllib.parse.urlsplit(discovery["jwks_uri"]).path) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.load(response)["keys"])

    def follow(self, path):
        for _ in range(10):
            response = self.request(path)
            if response.status not in (301, 302, 303, 307, 308):
                return response
            target = urllib.parse.urljoin("https://localhost" + path, response.headers["Location"])
            response.close()
            parsed = urllib.parse.urlsplit(target)
            self.assertEqual((parsed.scheme, parsed.netloc), ("https", "localhost"), target)
            path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
        self.fail("identity redirect loop")

    def test_registration_form_and_secure_cookies(self):
        with self.follow("/user/registration") as response:
            self.assertEqual(response.status, 200)
            html = response.read().decode()
        self.assertIn("https://localhost/user/api/self-service/registration", html)
        self.assertTrue(list(self.cookies))
        self.assertTrue(all(cookie.secure for cookie in self.cookies))

    def test_authorization_reaches_login(self):
        query = urllib.parse.urlencode({
            "client_id": "gitstore", "response_type": "code", "scope": "openid profile email",
            "redirect_uri": "http://localhost:3000/callback", "state": "proxy-routing-smoke-test",
        })
        with self.follow("/sso/oauth2/auth?" + query) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("https://localhost/user/api/self-service/login", response.read().decode())

    def test_mail_ui_and_api_use_same_origin(self):
        with self.request("/mail/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn('src="/mail/www/', response.read().decode())
        with self.request("/mail/www/mailslurper/js/services/SettingsService.js") as response:
            js = response.read().decode()
            self.assertIn('window.location.origin + "/mail/api"', js)
            self.assertIn('"/mail/servicesettings"', js)
        # Exercise the UI's actual URL builders: prefixing these suffixes makes the
        # inbox call /mail/api/mail/mail, which the server treats as mail ID "mail".
        with self.request("/mail/www/mailslurper/js/services/MailService.js") as response:
            js = response.read().decode()
            self.assertIn('serviceURL + "/mail?pageNumber="', js)
            self.assertIn('serviceURL + "/mail/" + mailID + "/attachment/"', js)
            self.assertNotIn('serviceURL + "/mail/mail', js)
        with self.request("/mail/www/mailslurper/js/services/TemplateService.js") as response:
            self.assertIn('"/mail/www/mailslurper/templates/"', response.read().decode())
        with self.request("/mail/api/mail") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("mailItems", json.load(response))


if __name__ == "__main__":
    unittest.main()
