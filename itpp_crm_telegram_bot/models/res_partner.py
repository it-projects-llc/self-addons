from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    telegram_id = fields.Char("Telegram ID", index=True, copy=False)

    @api.constrains("telegram_id")
    def _check_telegram_id(self):
        for partner in self:
            if partner.telegram_id and (
                not partner.telegram_id.isascii()
                or not partner.telegram_id.isdigit()
                or int(partner.telegram_id) <= 0
            ):
                raise ValidationError(
                    _("Telegram ID must be a positive numeric user ID.")
                )
