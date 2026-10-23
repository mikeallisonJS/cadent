"""The ext-data-control clipboard, driven the way KWin drives it.

No socket: a fake connection hands out proxies and records requests, and the
test plays the compositor — it replaces the selection, cancels the source that
lost it, and asks the source that won for its text through a pipe, in the
order KWin does on a real paste.
"""

import os

import pytest

from cadent.platform.linux import wire


class FakeProxy:
    def __init__(self, interface: str) -> None:
        self.interface = interface
        self.handler = None
        self.data: dict = {}
        self.requests: list[tuple] = []

    def request(self, name, *args, fds=None):
        self.requests.append((name, args))


class FakeConnection:
    globals = {"ext_data_control_manager_v1": (1, 1), "wl_seat": (2, 1)}

    def __init__(self) -> None:
        self.created: list[FakeProxy] = []

    def bind(self, interface, version, handler=None):
        return FakeProxy(interface)

    def request_new(self, obj, name, interface, *args, fds=None):
        proxy = FakeProxy(interface)
        self.created.append(proxy)
        obj.request(name, *args)
        return proxy

    def roundtrip(self, timeout=5.0):
        pass

    def sources(self) -> list[FakeProxy]:
        return [p for p in self.created if p.interface == "ext_data_control_source_v1"]


def read_through(source: FakeProxy, mime: str = "text/plain;charset=utf-8") -> bytes:
    """What a pasting client receives when it asks `source` for `mime`."""
    r, w = os.pipe()
    source.handler("send", (mime, w))     # the handler owns and closes `w`
    with os.fdopen(r, "rb") as fh:
        return fh.read()


@pytest.fixture
def clipboard():
    conn = FakeConnection()
    return conn, wire.DataControlClipboard(conn)


def test_the_source_that_lost_the_selection_being_cancelled_leaves_the_new_one_its_text(
        clipboard):
    """The order KWin uses on every paste after the first: `set_selection` on
    the new source, then `cancelled` to the old one, then the pasting client
    reads the new one. A cancel that cleared shared state pasted nothing —
    while the rung reported the dictation inserted."""
    conn, clip = clipboard
    clip.set_text("first transcript", exclude_from_history=True)
    clip.set_text("second transcript", exclude_from_history=True)
    old, new = conn.sources()

    old.handler("cancelled", ())

    assert read_through(new) == b"second transcript"


def test_a_source_answers_with_its_own_text_whichever_is_newest(clipboard):
    """Each source answers for the text it was created with: a late read of a
    replaced source must not hand out the newer transcript, nor the newer
    source the older one."""
    conn, clip = clipboard
    clip.set_text("restored clipboard", exclude_from_history=False)
    clip.set_text("transcript", exclude_from_history=True)
    old, new = conn.sources()

    assert read_through(new) == b"transcript"
    assert read_through(old) == b"restored clipboard"


def test_the_password_hint_is_answered_only_where_it_was_offered(clipboard):
    conn, clip = clipboard
    clip.set_text("transcript", exclude_from_history=True)
    (source,) = conn.sources()

    assert ("offer", (wire.KDE_HINT_MIME,)) in source.requests
    assert read_through(source, wire.KDE_HINT_MIME) == b"secret"


def test_losing_the_selection_to_another_client_forgets_our_text(clipboard):
    """When another client takes the selection, `get_text` must not answer
    from our own copy as if we still owned it."""
    conn, clip = clipboard
    clip.set_text("transcript", exclude_from_history=True)
    (source,) = conn.sources()

    source.handler("cancelled", ())

    assert clip._text is None


def test_the_replaced_source_is_destroyed_once(clipboard):
    """`set_text` destroys the source it replaced; the cancel that follows
    must not destroy it a second time — a second destroy names a dead object
    id and the compositor drops the whole connection."""
    conn, clip = clipboard
    clip.set_text("one", exclude_from_history=True)
    clip.set_text("two", exclude_from_history=True)
    old, _new = conn.sources()

    old.handler("cancelled", ())

    assert [name for name, _ in old.requests].count("destroy") == 1
