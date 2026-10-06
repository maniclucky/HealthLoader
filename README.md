# HealthLoader

A Python command-line tool (`fitthing`) that downloads your own health data to
your computer:

- **Google Health API** — steps, heart rate, sleep, weight, body fat, exercise,
  and 30+ other data types, saved as raw JSON.
- **Cronometer** (optional) — daily calories, protein, carbs, and fat.

It also builds a day-level `data/health/daily.csv` combining weight, body fat,
steps (ring, with treadmill counts during treadmill sessions), energy burned,
heart rate, SpO2, sleep, exercise, and every
Cronometer nutrient, with small gaps filled in.

Reruns are incremental: each run catches up from where the last one stopped
without duplicating data.

## Quick start

Requires Python 3.11+ and a Google Cloud OAuth client (see the setup guide).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config.example.toml config.toml

fitthing auth                 # sign in with Google
fitthing export               # download Google Health data (last 30 days on first run)

fitthing cronometer-auth      # optional: sign in to Cronometer
fitthing cronometer-export    # optional: download daily nutrition

fitthing sync                 # both pulls in one go, then rebuild daily.csv
```

Full setup, command flags, and output formats: [docs/SETUP.md](docs/SETUP.md).

## Privacy

`config.toml`, `secrets/`, `data/`, `targetSettings.json`, and local CSV and
notebook files are gitignored. Credentials and health data stay on your
machine.

## Development

```bash
pytest
```
