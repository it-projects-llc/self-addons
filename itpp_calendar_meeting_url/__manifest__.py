# Copyright 2024 Ilmir Karamov <https://it-projects.info>
# Copyright IT-Projects LLC <https://it-projects.info>
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

{
    "name": """Calendar Meeting URL Autogenerate""",
    "summary": """The meeting URL gets autogenerated based on the calendar start date""",
    "category": "Extra",
    "images": [],
    "version": "15.0.1.0.0",
    "application": False,

    "author": "IT-Projects LLC, Ilmir Karamov",
    "support": "apps@it-projects.info",
    "website": "https://github.com/it-projects-llc/self-addons",
    "license": "AGPL-3",

    "depends": [
        "calendar",
    ],
    "external_dependencies": {"python": [], "bin": []},
    "data": [],
    "demo": [],
    "qweb": [],

    "post_load": None,
    "pre_init_hook": None,
    "post_init_hook": None,
    "uninstall_hook": None,

    "auto_install": False,
    "installable": True,
}
