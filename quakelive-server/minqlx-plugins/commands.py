# This is an extension plugin for minqlx.
# Copyright (C) 2018 BarelyMiSSeD (github)
#
# You can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with minqlx. If not, see <http://www.gnu.org/licenses/>.
#
# This is a plugin and command listing script for the minqlx admin bot.
# This plugin lists all the in-game commands loaded on the server.
#
# PATCHED (v1.1):
#   The original version sent one or more separate player.tell() calls per
#   loaded plugin. With many plugins enabled (full MinoMino + tjone270 set),
#   "!lc" without an argument fired ~60 "reliable" server commands in a single
#   frame, overflowing the QL engine's reliable-command buffer and crashing the
#   whole QLDS process (systemd then restarted it -> looked like a "reset").
#
#   This version builds the full output first, packs it into a few length-
#   bounded messages, and sends them a few per frame (spread over time), so the
#   client's command buffer is never flooded. Behaviour/output is the same.
"""
//Server Config cvars
//Set the permission level needed to list the commands
set qlx_commandsAdmin "0"
//Enable to show only the commands the calling player can use, disable to show all commands (0=disable, 1=enable)
set qlx_commandsOnlyEligible "1"
"""

import minqlx

VERSION = "1.1"

# Keep each "tell" well under the QL engine string limit (~1024 chars).
MAX_TELL_LEN = 900
# How many messages to push per server frame before yielding to the next one.
MSGS_PER_FRAME = 3
# Delay between frame batches (seconds).
BATCH_DELAY = 0.15


class commands(minqlx.Plugin):
    def __init__(self):
        # cvars
        self.set_cvar_once("qlx_commandsAdmin", "0")
        self.set_cvar_once("qlx_commandsOnlyEligible", "1")

        # Minqlx bot commands
        self.add_command("plugins", self.list_plugins, self.get_cvar("qlx_commandsAdmin", int))
        self.add_command(("lc", "listcmds", "listcommands"), self.cmd_list,
                         self.get_cvar("qlx_commandsAdmin", int), usage="<plugin_name>")

    # ------------------------------------------------------------------ #
    #  Safe, throttled sender — this is the actual crash fix.
    # ------------------------------------------------------------------ #
    def _send_lines(self, player, lines):
        """Pack short lines into a few messages (each <= MAX_TELL_LEN) and send
        them a few per frame, so we never flood the client's reliable command
        buffer (which would crash QLDS)."""
        chunks = []
        buf = ""
        for line in lines:
            piece = line + "\n"
            if buf and len(buf) + len(piece) > MAX_TELL_LEN:
                chunks.append(buf.rstrip("\n"))
                buf = ""
            buf += piece
        if buf:
            chunks.append(buf.rstrip("\n"))
        self._send_chunks(player, chunks, 0)

    def _send_chunks(self, player, chunks, index):
        if index >= len(chunks):
            return
        end = min(index + MSGS_PER_FRAME, len(chunks))
        for i in range(index, end):
            try:
                player.tell(chunks[i])
            except Exception:
                # Player likely disconnected mid-listing; stop quietly.
                return
        if end < len(chunks):
            @minqlx.delay(BATCH_DELAY)
            def _continue():
                self._send_chunks(player, chunks, end)
            _continue()

    # ------------------------------------------------------------------ #
    #  !plugins
    # ------------------------------------------------------------------ #
    def list_plugins(self, player, msg, channel):
        names = sorted(self.plugins)
        if not names:
            return
        body = "^7, ^6".join(names)
        self._send_lines(player, [
            "^1{} ^3Plugins found:".format(len(names)),
            "^6" + body,
        ])

    # ------------------------------------------------------------------ #
    #  !lc  /  !listcmds  /  !listcommands   [plugin_name]
    # ------------------------------------------------------------------ #
    def cmd_list(self, player, msg, channel):
        p = self.plugins
        only_eligible = self.get_cvar("qlx_commandsOnlyEligible", bool)
        caller_perm = self.db.get_permission(player)
        search = msg[1].lower() if len(msg) > 1 else None

        lines = ["^1Plugin^7: ^2Number of Commands"]
        count = 0

        for name in sorted(p):
            if search and search not in name.lower():
                continue
            try:
                cmds = p[name].commands
            except Exception:
                continue
            if not cmds:
                continue

            entries = []
            for cmd in cmds:
                perm = cmd.permission
                if only_eligible and caller_perm < perm:
                    continue
                entries.append("^7(^2{}^7) ^6{}".format(perm, "^7|^6".join(cmd.name)))

            if entries:
                m = len(entries)
                lines.append("^1{}^7: {} ^3Command{}".format(name, m, "s" if m > 1 else ""))
                lines.append("^7, ".join(entries))
                count += 1

        if not count:
            player.tell("^3No Plugin matches ^4{}".format(search))
            return

        self._send_lines(player, lines)
