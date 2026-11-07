"""Contract commands -> the existing UI actions / Nova methods. No business logic lives here."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .enroll import EnrollError
from .snapshot import AUTH_METHOD_IN

if TYPE_CHECKING:
    from ..agent.nova import Nova
    from ..ui.actions import UiActions
    from ..voice.pipeline import VoicePipeline
    from .enroll import EnrollService

TABS = ("home", "agent", "shop", "nav")
SETTLE_S = 0.25


class CommandError(Exception):
    pass


def _req(args: dict[str, Any], key: str, typ: type = str) -> Any:
    if key not in args or args[key] is None:
        raise CommandError(f"missing argument: {key}")
    try:
        return typ(args[key])
    except (TypeError, ValueError) as e:
        raise CommandError(f"bad argument: {key}") from e


class Commands:
    def __init__(self, actions: UiActions, nova: Nova, pipeline: VoicePipeline | None = None,
                 enroll: EnrollService | None = None):
        self.actions = actions
        self.nova = nova
        self.pipeline = pipeline
        self.enroll = enroll

    async def run(self, name: str, args: dict[str, Any]) -> None:
        """Validate and start the command. Raises CommandError (bad request) or the action's own exception when
        it fails within the settle window; slower actions keep running and report through state."""
        handler = getattr(self, f"cmd_{name}", None)
        if handler is None:
            raise CommandError(f"unknown command: {name}")
        task = handler(args)
        if isinstance(task, asyncio.Future):
            done, _ = await asyncio.wait({task}, timeout=SETTLE_S)
            if task in done and not task.cancelled() and task.exception():
                raise task.exception()

    # ---- navigation between tabs / voice
    def cmd_show_tab(self, a):
        tab = _req(a, "tab")
        if tab not in TABS:
            raise CommandError(f"unknown tab: {tab}")
        return self.actions.start("tab", tab)

    def cmd_ptt(self, a):
        if not _req(a, "down", bool):
            return None
        if self.pipeline is None or not self.pipeline.available:
            self.nova.notify("Voice input isn't available")
            raise CommandError("voice unavailable")
        self.pipeline.listen()
        return None

    def cmd_end_conversation(self, a):
        """End: close the wake-once conversation, drop pending audio and stop speech."""
        if self.pipeline is not None:
            self.pipeline.end()
        else:
            self.nova.barge_in()
        return None

    def cmd_set_voice_muted(self, a):
        if self.pipeline is None or not self.pipeline.available:
            raise CommandError("voice unavailable")
        self.pipeline.set_muted(_req(a, "muted", bool))
        return None

    def cmd_say_text(self, a):
        text = _req(a, "text").strip()
        if not text:
            raise CommandError("empty text")
        return self.actions.say(text)

    # ---- shop
    def cmd_find(self, a):
        category, query = a.get("category"), a.get("query")
        if not category and not query:
            raise CommandError("missing argument: category or query")
        if query and not category:
            category = self.nova.shop.category or "food"
        if query:
            return self.actions.spawn(self.nova.discover(str(category), str(query)))
        return self.actions.start("discover", str(category))

    def cmd_open_place(self, a):
        return self.actions.start("openPlace", _req(a, "id"))

    def cmd_open_item(self, a):
        return self.actions.start("openItem", _req(a, "id"))

    def cmd_set_option(self, a):
        """Tap on an option chip: pick it in its group, or toggle it in a multi-select group."""
        sh = self.nova.shop
        gid, oid = _req(a, "group"), _req(a, "option")
        group = sh.item.group(gid) if sh.item else None
        if group is None or group.option(oid) is None:
            raise CommandError("unknown option")
        chosen = list(sh.item_sel.get(gid, []))
        if group.max_select > 1:
            chosen = [x for x in chosen if x != oid] if oid in chosen else [*chosen, oid][-group.max_select:]
        elif oid in chosen and group.min_select == 0:
            chosen = []
        else:
            chosen = [oid]
        return self.actions.spawn(self.nova.set_item_option({gid: chosen}))

    def cmd_add_item(self, a):
        qty = int(a.get("qty", 1) or 1)
        options = a.get("options") or None
        sh = self.nova.shop
        if options is None and sh.item is not None and sh.item.id == a.get("id"):
            options = dict(sh.item_sel) or None        # the sheet sends only the id: add what is selected
        return self.actions.spawn(self.nova.add_item(_req(a, "id"), qty, options, speak=False))

    def cmd_view_cart(self, a):
        return self.actions.start("viewCart")

    def cmd_set_line_qty(self, a):
        return self.actions.start("lineQty", _req(a, "id"), _req(a, "qty", int))

    def cmd_remove_line(self, a):
        return self.actions.spawn(self.nova.remove_line(line_id=_req(a, "id")))

    def cmd_back(self, a):
        return self.actions.start("back")

    # ---- checkout
    def cmd_checkout(self, a):
        return self.actions.start("checkout")

    def cmd_choose_auth(self, a):
        method = AUTH_METHOD_IN.get(_req(a, "method"))
        if method is None:
            raise CommandError("unknown auth method")
        return self.actions.start("auth", method)

    def cmd_otp_code(self, a):
        return self.actions.spawn(self.nova.submit_otp(_req(a, "code")))

    def cmd_cancel_checkout(self, a):
        return self.actions.start("cancelCheckout")

    # ---- after the order
    def cmd_im_here(self, a):
        return self.actions.start("imHere")

    def cmd_drive_to(self, a):
        pid = _req(a, "place_id")
        if self.nova._place_for(place_id=pid) is None:
            raise CommandError(f"unknown place: {pid}")
        return self.actions.start("driveToPlace", pid)

    def cmd_stop_nav(self, a):
        return self.actions.start("stopNav")

    def cmd_navigate_to(self, a):
        query = _req(a, "query").strip()
        if not query:
            raise CommandError("empty query")
        return self.actions.spawn(self.nova.navigate_to_query(query))

    def cmd_find_along_route(self, a):
        category = _req(a, "category")
        return self.actions.spawn(self.nova.find_along_route(category, a.get("query"),
                                                             destination=a.get("destination")))

    def cmd_add_stop(self, a):
        pid = _req(a, "place_id")
        place = self.nova._place_for(place_id=pid)
        if place is None:
            raise CommandError(f"unknown place: {pid}")
        return self.actions.spawn(self.nova.add_stop(place))

    def cmd_sim_detour(self, a):
        hook = getattr(self.nova.vehicle, "inject_detour", None)
        if hook is None or self.nova.vehicle_mode != "sim":
            raise CommandError("detours need the simulated vehicle")
        hook(float(a.get("offset_m", 120.0)))
        return None

    # ---- Voice ID enrollment
    def _enroll(self, call):
        if self.enroll is None:
            raise CommandError("enrollment unavailable")
        try:
            return call(self.enroll)
        except EnrollError as e:
            raise CommandError(str(e)) from e

    def cmd_enroll_start(self, a):
        return self._enroll(lambda e: e.start(str(a.get("name") or ""), a.get("consent")))

    def cmd_enroll_face_start(self, a):
        return self._enroll(lambda e: e.face.start(str(a.get("name") or ""), a.get("consent"),
                                                   a.get("profile_id"), a.get("role")))

    def cmd_face_profile_select(self, a):
        self._enroll(lambda e: e.face.select(a.get("profile_id")))
        return None

    def cmd_face_profile_edit(self, a):
        self._enroll(lambda e: e.face.edit(a.get("profile_id"), a.get("name"), a.get("role")))
        return None

    def cmd_enroll_face_link(self, a):
        return self._enroll(lambda e: e.face.link(a.get("confirm")))

    def cmd_enroll_face_delete(self, a):
        return self._enroll(lambda e: e.face.delete(a.get("confirm"), a.get("profile_id")))

    def cmd_enroll_face_cancel(self, a):
        self._enroll(lambda e: e.face.cancel())
        return None

    def cmd_sim_outcome(self, a):
        verifier = self.nova.face
        if not hasattr(verifier, "set_outcome"):
            raise CommandError("biometrics are not simulated")
        try:
            verifier.set_outcome(str(a.get("modality") or ""), str(a.get("outcome") or ""))
        except ValueError as e:
            raise CommandError(str(e)) from e
        self.nova.publish()
        return None

    def cmd_enroll_take(self, a):
        return self._enroll(lambda e: e.take())

    def cmd_enroll_cancel(self, a):
        self._enroll(lambda e: e.cancel())
        return None
