from dataclasses import dataclass


@dataclass(frozen=True)
class Allowlist:
    """Solo el owner, en el servidor y los canales indicados. Todo lo demás se ignora."""

    owner_id: int
    guild_id: int
    channel_ids: frozenset[int]

    def is_allowed(self, *, user_id: int, guild_id: int | None, channel_id: int) -> bool:
        # guild_id None = mensaje directo; no está permitido salvo que se agregue explícitamente.
        return (
            user_id == self.owner_id
            and guild_id == self.guild_id
            and channel_id in self.channel_ids
        )
