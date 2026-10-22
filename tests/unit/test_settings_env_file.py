"""Settings must tolerate .env keys this branch does not declare."""

from src.config.settings import Settings


def test_unknown_env_file_keys_are_ignored(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "PLAYWRIGHT_TIMEOUT_MS=12345\nTYPESAFE_API_KEY=key_from_another_branch\nSOME_NEW_FLAG=1\n",
        encoding="utf-8",
    )
    s = Settings(_env_file=env)
    assert s.playwright_timeout_ms == 12345
    assert not hasattr(s, "some_new_flag")  # a key no branch declares


def test_known_env_file_keys_still_apply(tmp_path):
    env = tmp_path / ".env"
    env.write_text("JWT_SECRET=x\nPLAYWRIGHT_BLOCK_RESOURCES=false\n", encoding="utf-8")
    assert Settings(_env_file=env).playwright_block_resources is False
