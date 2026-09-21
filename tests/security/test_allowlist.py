from asistente.security.allowlist import Allowlist

OWNER, GUILD, CHAN = 111, 222, 333
allow = Allowlist(owner_id=OWNER, guild_id=GUILD, channel_ids=frozenset({CHAN}))


def test_owner_en_canal_permitido():
    assert allow.is_allowed(user_id=OWNER, guild_id=GUILD, channel_id=CHAN)


def test_otro_usuario_es_ignorado():
    assert not allow.is_allowed(user_id=999, guild_id=GUILD, channel_id=CHAN)


def test_canal_no_permitido():
    assert not allow.is_allowed(user_id=OWNER, guild_id=GUILD, channel_id=444)


def test_otro_servidor():
    assert not allow.is_allowed(user_id=OWNER, guild_id=555, channel_id=CHAN)


def test_mensaje_directo_no_permitido():
    assert not allow.is_allowed(user_id=OWNER, guild_id=None, channel_id=CHAN)
