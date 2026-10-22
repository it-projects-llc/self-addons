import re

from odoo import http

WEBHOOK_PATH = re.compile(r"^/telegram/webhook/[^/]+$")


def post_load():
    """Let Telegram send ordinary JSON to an HTTP route on Odoo 15.

    Odoo 15 Root.get_request chooses JsonRequest by Content-Type, before routing
    or ir.http hooks. JsonRequest expects JSON-RPC, eagerly reads the entire
    body, and wraps responses in JSON-RPC. Select HttpRequest for this addon's
    exact route family so authentication, body limits and HTTP errors work.
    All other request paths keep the original dispatcher.
    """
    original = http.Root.get_request
    if getattr(original, "_crm_telegram_webhook", False):
        return

    def get_request(root, httprequest):
        if WEBHOOK_PATH.fullmatch(httprequest.path):
            return http.HttpRequest(httprequest)
        return original(root, httprequest)

    get_request._crm_telegram_webhook = True
    http.Root.get_request = get_request
