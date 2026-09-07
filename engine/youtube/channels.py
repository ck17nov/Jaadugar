"""Several brand channels under one Google account.

The plan this supports: one Google account ("Technical Jaadugar") owning a
brand channel per niche - kids, finance, tech, AI, science, programming - so a
kids video goes to the kids channel without ever signing in as a different
person.

THE FACT THAT DECIDES THE DESIGN: a YouTube access token is bound to ONE
channel. When an account owns brand channels, Google shows a channel chooser
during consent and the token it issues acts on the channel that was picked -
there is no per-request "post as this channel" parameter for an ordinary
client. (`onBehalfOfContentOwner` exists but is for CMS content-owner
accounts, which this is not.)

So N channels means N authorisations and N refresh tokens, all from the same
Google account, each obtained by running the consent flow again and choosing a
different channel. That is why the app says "Add channel" rather than "Add
account", and why every stored entry keeps its own client id: the token can
only be refreshed by the client that minted it.

The store is a dict keyed by channel id, with one marked default. The previous
single-token format is migrated on first read rather than discarded, so an
existing connection keeps working without reconnecting.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..core.logging import log_event

# The key the old single-channel format used. Kept for migration only.
LEGACY_KEYS = ("refresh_token", "token", "token_uri", "scopes", "expiry",
               "client_id", "public_client")


@dataclass
class Channel:
    """One authorised channel."""
    channel_id: str
    title: str = ""
    refresh_token: str = ""
    client_id: str = ""
    public_client: bool = True
    token: str | None = None
    token_uri: str = "https://oauth2.googleapis.com/token"
    scopes: list[str] | None = None
    expiry: str | None = None
    added_at: float = 0.0
    niches: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "title": self.title,
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "public_client": self.public_client,
            "token": self.token,
            "token_uri": self.token_uri,
            "scopes": list(self.scopes or []),
            "expiry": self.expiry,
            "added_at": self.added_at,
            "niches": list(self.niches or []),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Channel:
        return cls(
            channel_id=str(data.get("channel_id", "")),
            title=str(data.get("title", "")),
            refresh_token=str(data.get("refresh_token", "")),
            client_id=str(data.get("client_id", "")),
            public_client=bool(data.get("public_client", True)),
            token=data.get("token"),
            token_uri=str(data.get("token_uri")
                          or "https://oauth2.googleapis.com/token"),
            scopes=list(data.get("scopes") or []),
            expiry=data.get("expiry"),
            added_at=float(data.get("added_at", 0.0) or 0.0),
            niches=list(data.get("niches") or []),
        )

    def public(self) -> dict[str, Any]:
        """Everything except the secret. Safe to return from an endpoint."""
        return {"channel_id": self.channel_id, "title": self.title,
                "added_at": self.added_at, "niches": list(self.niches or [])}


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Turn the old single-token record into the multi-channel shape.

    Done on read rather than by a migration script so an existing install
    keeps working with no reconnection. The channel id is not known at this
    point - the old format never stored one - so the entry is filed under
    "default" and renamed the first time the channel identity is fetched.
    """
    if not data:
        return {"channels": {}, "default": ""}
    if "channels" in data:
        return data
    if not data.get("refresh_token"):
        return {"channels": {}, "default": ""}
    channel = Channel(
        channel_id="default",
        refresh_token=str(data.get("refresh_token", "")),
        client_id=str(data.get("client_id", "")),
        public_client=bool(data.get("public_client", bool(data.get("client_id")))),
        token=data.get("token"),
        token_uri=str(data.get("token_uri")
                      or "https://oauth2.googleapis.com/token"),
        scopes=list(data.get("scopes") or []),
        expiry=data.get("expiry"),
        added_at=time.time(),
    )
    log_event("YOUTUBE", "migrated the single-channel token to the "
              "multi-channel store")
    return {"channels": {"default": channel.to_dict()}, "default": "default"}


class ChannelStore:
    """Reads and writes the multi-channel record inside a TokenStore."""

    def __init__(self, tokens):
        self.tokens = tokens

    # ------------------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        return migrate(self.tokens.read())

    def _write(self, data: dict[str, Any]) -> None:
        self.tokens.write(data)

    # ------------------------------------------------------------------
    def all(self) -> list[Channel]:
        data = self._read()
        return [Channel.from_dict(v)
                for v in (data.get("channels") or {}).values()]

    def ids(self) -> list[str]:
        return [c.channel_id for c in self.all()]

    def default_id(self) -> str:
        data = self._read()
        wanted = str(data.get("default") or "")
        channels = data.get("channels") or {}
        if wanted and wanted in channels:
            return wanted
        return next(iter(channels), "")

    def get(self, channel_id: str = "") -> Channel | None:
        """A channel by id, or the default when no id is given.

        An unknown id returns None rather than falling back to the default:
        uploading a finance video to the kids channel because a stale id was
        passed is worse than failing and saying so.
        """
        data = self._read()
        channels = data.get("channels") or {}
        wanted = channel_id or self.default_id()
        raw = channels.get(wanted)
        return Channel.from_dict(raw) if raw else None

    def for_niche(self, niche: str) -> Channel | None:
        """The channel mapped to this niche, if one is."""
        target = (niche or "").strip().lower()
        if not target:
            return None
        for channel in self.all():
            if any(target == n.strip().lower() for n in (channel.niches or [])):
                return channel
        return None

    def put(self, channel: Channel, *, make_default: bool = False) -> None:
        data = self._read()
        channels = dict(data.get("channels") or {})
        # A re-authorisation of a channel already stored replaces its token but
        # keeps the niche mapping, which the user set by hand.
        existing = channels.get(channel.channel_id)
        if existing and not channel.niches:
            channel.niches = list(existing.get("niches") or [])
        if not channel.added_at:
            channel.added_at = float(existing.get("added_at", 0.0)
                                     if existing else time.time()) or time.time()
        channels[channel.channel_id] = channel.to_dict()
        # Drop the placeholder once the real channel id is known, so migrating
        # then re-authorising does not leave a phantom "default" entry.
        if channel.channel_id != "default" and "default" in channels:
            placeholder = channels["default"]
            if placeholder.get("refresh_token") == channel.refresh_token:
                channels.pop("default", None)
                if data.get("default") == "default":
                    data["default"] = channel.channel_id
        data["channels"] = channels
        if make_default or not data.get("default"):
            data["default"] = channel.channel_id
        self._write(data)

    def remove(self, channel_id: str) -> bool:
        data = self._read()
        channels = dict(data.get("channels") or {})
        if channel_id not in channels:
            return False
        channels.pop(channel_id)
        data["channels"] = channels
        if data.get("default") == channel_id:
            data["default"] = next(iter(channels), "")
        self._write(data)
        return True

    def set_default(self, channel_id: str) -> bool:
        data = self._read()
        if channel_id not in (data.get("channels") or {}):
            return False
        data["default"] = channel_id
        self._write(data)
        return True

    def set_niches(self, channel_id: str, niches: list[str]) -> bool:
        """Map niches to a channel, so a kids video posts to the kids channel.

        Stored on the channel rather than in config because it is the user's
        editing surface: they add a channel and say what it is for.
        """
        data = self._read()
        channels = dict(data.get("channels") or {})
        raw = channels.get(channel_id)
        if not raw:
            return False
        # A niche can only belong to one channel, or "which channel does this
        # go to" would have two answers.
        cleaned = [n.strip() for n in niches if n and n.strip()]
        lowered = {n.lower() for n in cleaned}
        for other_id, other in channels.items():
            if other_id == channel_id:
                continue
            other["niches"] = [n for n in (other.get("niches") or [])
                               if n.strip().lower() not in lowered]
        raw["niches"] = cleaned
        channels[channel_id] = raw
        data["channels"] = channels
        self._write(data)
        return True
