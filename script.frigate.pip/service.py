# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 flubshi
"""Service entry point, started by Kodi at login."""
from resources.lib.service import PipService

PipService().run()
