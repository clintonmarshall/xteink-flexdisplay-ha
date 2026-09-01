# FlexHub async health soak

This runbook validates the FlexHub compact health contract and the separation of Bridge fleet HTTP work from the
main receiver loop. It is read-only: the collector only performs authenticated `GET /api/flexhub/health` requests.
It does not install firmware, change policy, send content, or restart the hub.

## Preconditions

Before any canary installation, identify the exact SenseCAP Indicator by stable hardware evidence, record its current
firmware and configuration, verify a durable recovery artifact and checksum, and obtain the separate firmware-write
authorization required by the FlexHub repository. A source build or an available IP address is not device identity or
installation authorization.

After an authorized canary is running the reviewed firmware, rediscover its current URL rather than relying on a
remembered address. If the API uses a PIN, use `--prompt-pin`; never put the PIN in the URL, command arguments, output,
or repository.

## Collection phases

Create a new output file for each phase. The collector refuses to overwrite an existing file.

```bash
python3 scripts/flexhub_health_soak.py http://FLEXHUB-HOST \
  --duration 1800 --interval 5 --phase idle-30m \
  --output flexhub-idle-30m.jsonl --prompt-pin
```

Run the same command during representative receiver discovery, delivery, storage, and Bridge fleet-refresh workloads,
changing `--phase` and the output filename. Once the 30-minute phases are clean, run a 24-hour mixed-workload soak:

```bash
python3 scripts/flexhub_health_soak.py http://FLEXHUB-HOST \
  --duration 86400 --interval 5 --phase mixed-24h \
  --output flexhub-mixed-24h.jsonl --prompt-pin
```

Workload operations are separate actions and require their normal authorization. The health collector itself does not
generate those operations.

## Interpretation

Each JSONL file contains `sample`, `error`, and final `summary` events. Review:

- request and contract errors;
- uptime regressions, reset-reason changes, and unexpected sample-clock regressions;
- maximum cached-sample age;
- internal free heap, boot-minimum heap, and largest free internal block by activity;
- PSRAM separately from internal memory; and
- whether the API remains responsive while `activity` is `fleet`.

A 32-bit millisecond rollover is recorded separately and is not classified as a regression. Do not set warning or
critical memory thresholds from one short run. Establish thresholds only after the exact-hardware 30-minute and
24-hour evidence is reviewed, with particular attention to the smallest internal largest block under repeated fleet
refresh and content-delivery cycles.

The command exits `0` when all requests returned valid samples, `1` when at least one request or contract error was
recorded, and `2` when no valid samples were collected. A clean collector exit is evidence for the observed interval,
not proof of a release, fleet rollout, or physical display behavior.
