# Steps: ring primary, treadmill replaces it during sessions

2026-10-06, agent (Claude). Follows [[2026-10-06-sync-command]].

## User's setup and ask

- Ring (`com.gdjztech.ringconn`) is worn all day and is the primary step source.
- Urevo walking treadmill (`com.urevo.app`). The phone sits on the stand,
  so it doesn't count treadmill steps.
- The ring misses steps when the hand is still (working while walking).
  The user asked for the ring as primary and the treadmill's numbers for the
  stretch while it runs. The user cares about **total steps, not
  distance**, so the Distance column was dropped instead of fixed.

## Evidence (data on disk, 2026-08-24..10-05)

- Ring steps arrive in 30-minute buckets.
- Prorated ring steps inside each treadmill session ran roughly 50–90% of
  the treadmill's count, as low as 10% (10-01: 348 vs 3354).
- The treadmill uploaded one session twice on 10-03: identical counts
  (10050) and durations, starts ~80 min apart, overlapping. The exercise
  record matches only the later one. Ring activity suggests the earlier
  one is the real time.
- Distance had two phone sources mirroring each other
  (`com.google.android.apps.fitness` and a `com.android.healthconnect.phone.*`
  package), which double-counted from 9-28 on. This is one reason distance
  was dropped.

## Algorithm (`aggregate._steps_by_day`)

1. Treadmill step points (`csv_treadmill_packages`, new config key) become
   sessions. Overlapping ones merge (union of intervals, `max` count) =
   the same session counted once.
2. Each primary-source point (`csv_device_packages`) keeps the fraction of
   its interval that falls outside every session. Points without
   timestamps are kept whole, so older tests and fixtures behave as before.
3. Session counts are credited to the session's local start date.

For the 10-03 duplicate, the union removes ring steps across both copies'
windows (~5.3k) and adds 10050 once. Picking the "right" copy by ring
activity was considered and rejected as overfitting one incident.

Also changed: `Exercise (min)` is now the wall-clock union of all
exercise sessions per day (phone and treadmill both log the same walk;
summing durations double-counted, e.g. 10-04: 142 → 105).

## Verified

- 142 tests pass. New tests: splice with partial-bucket proration,
  duplicate-session merge, empty treadmill list, exercise overlap.
- Rebuilt `daily.csv` from data on disk (no network). Every treadmill day
  gained ~1–4.9k steps vs ring-only (e.g. 9-30: 9457 → 13372; 10-03:
  18423 → 23323).

## Not done

- Energy burned is still ring-only during treadmill sessions (the treadmill
  reports none). It probably undercounts for the same reason steps did.
