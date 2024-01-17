from odoo import api, fields, models, _
import pytz
from datetime import timezone


class Calendar(models.Model):
    _inherit = 'calendar.event'

    videocall_location = fields.Char(compute="_meeting_url")
    # tz = pytz.timezone('Asia/Yekaterinburg')

    @api.depends('start')
    def _meeting_url(self):
        for rec in self:
            rec.date_combination = rec.start.astimezone(tz=pytz.timezone('Asia/Yekaterinburg'))\
                .strftime('%Y%m%d')  # timezone(self.env.user.tz or 'Asia/Yekaterinburg').
            # list of timezones https://gist.github.com/heyalexej/8bf688fd67d7199be4a1682b3eec7568
            rec.videocall_location = "https://meet.jit.si/discussion_%s" % rec.date_combination
