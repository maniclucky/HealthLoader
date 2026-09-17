from pathlib import Path

from fitthing.config import load_config


def test_load_config_defaults_when_missing(tmp_path, capsys):
    missing = tmp_path / "config.toml"
    config = load_config(missing)

    assert config.client_secret_path == Path("secrets/client_secret.json")
    assert config.token_path == Path("secrets/token.json")
    assert config.output_dir == Path("data")
    assert config.start_date is None
    assert config.data_types == "all"
    assert config.window_days == 14
    assert config.request_delay_seconds == 0.5

    err = capsys.readouterr().err
    assert "config.example.toml" in err


def test_load_config_reads_overrides(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'client_secret_path = "secrets/other.json"\n'
        'data_types = ["steps", "weight"]\n'
        "window_days = 7\n"
        "request_delay_seconds = 1.5\n"
    )

    config = load_config(config_path)

    assert config.client_secret_path == Path("secrets/other.json")
    assert config.data_types == ["steps", "weight"]
    assert config.window_days == 7
    assert config.request_delay_seconds == 1.5
    # untouched keys keep their defaults
    assert config.token_path == Path("secrets/token.json")


def test_derived_paths():
    config = load_config(Path("/nonexistent/config.toml"))
    assert config.token_meta_path == Path("secrets/token_meta.json")
    assert config.state_path == Path("data/state.json")
    assert config.raw_dir == Path("data/raw")
