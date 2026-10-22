import hmac
import json

from werkzeug.wrappers import Response

from odoo import http
from odoo.http import request


class TelegramWebhook(http.Controller):
    @http.route(
        "/telegram/webhook/<string:key>",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def telegram_webhook(self, key, **kwargs):
        bot = (
            request.env["crm.telegram.bot"]
            .sudo()
            .search([("webhook_key", "=", key)], limit=1)
        )
        secret = request.httprequest.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not bot or not hmac.compare_digest(
            secret.encode(), (bot.webhook_secret or "").encode()
        ):
            return Response("Not found", status=404)
        if request.httprequest.content_length is None:
            return Response("Content-Length required", status=411)
        if request.httprequest.content_length > 1024 * 1024:
            return Response("Payload too large", status=413)
        try:
            raw = request.httprequest.get_data()
            if len(raw) > 1024 * 1024:
                return Response("Payload too large", status=413)
            payload = json.loads(raw)
        except ValueError:
            return Response("Invalid JSON", status=400)
        if (
            not isinstance(payload, dict)
            or type(payload.get("update_id")) is not int
            or not 0 <= payload["update_id"] <= 2147483647
        ):
            return Response("Invalid update", status=400)
        message = payload.get("message")
        if message is not None and not isinstance(message, dict):
            return Response("Invalid message", status=400)
        request.env["crm.telegram.inbox"].sudo()._accept(bot, payload)
        return Response("OK", status=200)
