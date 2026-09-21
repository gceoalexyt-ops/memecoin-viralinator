"""Publishing backend: Buffer.

Why Buffer rather than posting to X directly: X's API is pay-per-use, and this
account is not paying it. Buffer is an official X API partner whose free plan
includes 3 channels, a 10-post queue, and 3,000 API requests a month. The cron
tops the queue up; Buffer does the actual posting on schedule. Net cost to the
operator: nothing.

    Confirmed: endpoint https://api.buffer.com, `Authorization: Bearer <key>`,
    mutation `createPost(input: CreatePostInput!): PostActionPayload!`, input
    carries `text` / `channelId` / `schedulingType` / `mode` (`addToQueue`), and
    every mutation should select `... on MutationError`.

    NOT confirmed: the exact success-branch type name, and the query for
    listing channels — developers.buffer.com is unreachable from this
    environment, so those are written from the documented shape above rather
    than read off the schema.

Run `viralinator verify` before enabling cron. It exercises every call this
module makes and prints what came back, so a wrong field name surfaces as a
setup failure instead of a silently empty queue.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

BUFFER_ENDPOINT = "https://api.buffer.com"
TIMEOUT = 30

# Kept as editable module constants so a field-name correction is a one-line
# change rather than a hunt through call sites.

CHANNELS_QUERY = """
query Channels {
  channels {
    id
    service
    name
  }
}
"""

QUEUE_QUERY = """
query Queue($channelId: String!) {
  posts(channelId: $channelId, status: pending) {
    id
    status
  }
}
"""

CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on MutationError {
      message
    }
  }
}
"""


# Introspection used by `verify --introspect`. The build environment cannot
# reach Buffer, so this exists to have the *runner* report the real schema back
# in its logs — one dispatch of the verify workflow tells you whether the
# queries above are right, and if not, exactly what the correct shape is.
# Kept deliberately narrow. A full type dump overruns the log tail that can be
# read back from a workflow run, which is the only channel this build has for
# seeing Buffer's schema at all.
INTROSPECT_QUERY = """
query Introspect {
  channelsInput: __type(name: "ChannelsInput") {
    inputFields { name type { kind name ofType { kind name } } }
  }
  postsInput: __type(name: "PostsInput") {
    inputFields { name type { kind name ofType { kind name } } }
  }
  shareMode: __type(name: "ShareMode") { enumValues { name } }
  schedulingType: __type(name: "SchedulingType") { enumValues { name } }
  postStatus: __type(name: "PostStatus") { enumValues { name } }
  service: __type(name: "Service") { enumValues { name } }
  postActionSuccess: __type(name: "PostActionSuccess") { fields { name } }
}
"""


class PublishError(Exception):
    """Raised when Buffer rejects a call or the response is not understood."""


@dataclass
class Channel:
    id: str
    service: str
    name: str


class BufferPublisher:
    def __init__(self, token: str | None = None, channel_id: str | None = None) -> None:
        self.token = token or os.environ.get("BUFFER_ACCESS_TOKEN", "")
        if not self.token:
            raise PublishError(
                "BUFFER_ACCESS_TOKEN is not set. Add it as a GitHub Actions secret."
            )
        self.channel_id = channel_id or os.environ.get("BUFFER_CHANNEL_ID", "")

    # --- transport -------------------------------------------------------------

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        resp = requests.post(
            BUFFER_ENDPOINT,
            json={"query": query, "variables": variables or {}},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            timeout=TIMEOUT,
        )

        if resp.status_code == 401:
            raise PublishError("Buffer rejected the token (401). Check BUFFER_ACCESS_TOKEN.")
        if resp.status_code == 429:
            raise PublishError(
                "Buffer rate limit hit (429). Free plan allows 3,000 requests/month."
            )
        if resp.status_code >= 400:
            raise PublishError(f"Buffer HTTP {resp.status_code}: {resp.text[:400]}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise PublishError(f"Buffer returned non-JSON: {resp.text[:200]}") from exc

        if body.get("errors"):
            msgs = "; ".join(e.get("message", str(e)) for e in body["errors"])
            raise PublishError(f"Buffer GraphQL error: {msgs}")

        data = body.get("data")
        if data is None:
            raise PublishError(f"Buffer response had no data: {body}")
        return data

    # --- operations ------------------------------------------------------------

    def introspect(self) -> dict:
        """Ask Buffer what its schema actually looks like.

        Returns the raw introspection payload rather than interpreting it — the
        point is to get the truth into the workflow log so the queries above can
        be corrected against it.
        """
        return self._gql(INTROSPECT_QUERY)

    def channels(self) -> list[Channel]:
        data = self._gql(CHANNELS_QUERY)
        return [
            Channel(id=c["id"], service=c.get("service", ""), name=c.get("name", ""))
            for c in data.get("channels", [])
        ]

    def resolve_channel(self) -> str:
        """The X channel to post into, from config or by discovery."""
        if self.channel_id:
            return self.channel_id
        x_channels = [
            c for c in self.channels() if c.service.lower() in ("twitter", "x")
        ]
        if not x_channels:
            raise PublishError(
                "No X channel connected to this Buffer account. Connect one at "
                "buffer.com, then re-run `viralinator verify`."
            )
        if len(x_channels) > 1:
            ids = ", ".join(f"{c.name}={c.id}" for c in x_channels)
            raise PublishError(
                f"Multiple X channels found; set BUFFER_CHANNEL_ID to one of: {ids}"
            )
        return x_channels[0].id

    def queue_depth(self) -> int:
        """How many posts are already waiting. Free plan caps this at 10."""
        data = self._gql(QUEUE_QUERY, {"channelId": self.resolve_channel()})
        return len(data.get("posts", []))

    def add_to_queue(self, text: str) -> str:
        """Append a post to Buffer's queue. Buffer posts it on its own schedule."""
        data = self._gql(
            CREATE_POST_MUTATION,
            {
                "input": {
                    "text": text,
                    "channelId": self.resolve_channel(),
                    "schedulingType": "auto",
                    "mode": "addToQueue",
                }
            },
        )
        result = data.get("createPost", {})
        typename = result.get("__typename", "")
        if typename == "MutationError":
            raise PublishError(f"Buffer refused the post: {result.get('message')}")
        return typename or "queued"
