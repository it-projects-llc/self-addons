import json

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestTelegramWebhook(HttpCase):
    def setUp(self):
        super().setUp()
        team = self.env["crm.team"].create({"name": "Webhook test"})
        self.bot = self.env["crm.telegram.bot"].create(
            {"token": "webhook:test", "team_id": team.id}
        )
        self.path = "/telegram/webhook/" + self.bot.webhook_key
        self.headers = {
            "Content-Type": "application/json",
            "X-Telegram-Bot-Api-Secret-Token": self.bot.webhook_secret,
        }

    def post(self, data, headers=None, path=None):
        return self.url_open(
            path or self.path,
            data=data,
            headers=self.headers if headers is None else headers,
        )

    def test_authentication_and_malformed_updates(self):
        self.assertEqual(
            self.post(
                '{"update_id": 1}', headers={"Content-Type": "application/json"}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.post('{"update_id": 1}', path="/telegram/webhook/missing").status_code,
            404,
        )
        self.assertEqual(self.post("not json").status_code, 400)
        for payload in (
            [],
            {},
            {"update_id": True},
            {"update_id": -1},
            {"update_id": 1, "message": []},
        ):
            self.assertEqual(self.post(json.dumps(payload)).status_code, 400)
        self.assertEqual(self.post("x" * (1024 * 1024 + 1)).status_code, 413)

    def test_accept_duplicate_and_archived_bot(self):
        payload = json.dumps({"update_id": 42, "message": {"text": "/start"}})
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(
            self.env["crm.telegram.inbox"].search_count(
                [("bot_id", "=", self.bot.id), ("update_id", "=", 42)]
            ),
            1,
        )
        self.bot.active = False
        self.assertEqual(self.post(payload).status_code, 404)

    def test_regular_json_rpc_is_unchanged(self):
        response = self.url_open(
            "/web/webclient/version_info",
            data=json.dumps(
                {"jsonrpc": "2.0", "method": "call", "params": {}, "id": 9}
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["jsonrpc"], "2.0")
        self.assertEqual(response.json()["id"], 9)
        self.assertIn("server_version", response.json()["result"])
