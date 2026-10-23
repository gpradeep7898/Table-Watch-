# Demo guide

Everything here is **simulated**. Say so out loud when you present it.

## Start

```bash
./scripts/start-demo.sh
```

- Dashboard: http://127.0.0.1:5173
- Presentation mode: http://127.0.0.1:5173/demo

## Five-minute owner presentation (`/demo`)

1. Click **Start presentation**. The guided scenario starts at 5x. The pink banner says it is a simulation: point at it.
2. **Customers enter / tables occupied.** Dots walk in and sit. After 30 simulated seconds of stable presence the tables turn blue with a waiting timer.
3. **A table waits.** Table 6 never gets a server. Its timer climbs. At 5 minutes (about one minute real time at 5x) it turns red and one alert appears.
4. **Confirm service.** Click *Confirm service* on the alert (or click Table 6). The alert resolves. Explain: the camera can see someone walk up, but only staff confirm service.
5. **A party leaves.** Table 1's guests walk out. After 90 s of confirmed absence it turns amber: needs cleaning.
6. **Confirm cleaning.** A busser visits; click *Confirm cleaning* on Table 1. It turns green.
7. **Analytics.** Open Analytics: the wait, visit length and cleaning time you just created are there. Use *Generate synthetic week* beforehand if you want full charts (labelled synthetic).
8. Open **How it works** for the honest summary: existing cameras *can potentially* be used after a compatibility check, no face recognition, local processing, staff verify uncertain cases, accuracy must be measured in their restaurant.

If you do not click, simulated staff step in after a while so the story always completes.

Things not to say: accuracy percentages, revenue increases, "works with any camera", "never misses a customer".

## Scenarios (Overview → scenario picker)

| Scenario | Watch for |
|---|---|
| A: Normal service | Full lifecycle on six tables; no alerts |
| B: Forgotten customers | One `UNSERVED_TOO_LONG` on Table 4 at the threshold, resolved by service |
| C: Slow cleaning | `CLEANING_OVERDUE` on Table 5; the table stays amber until confirmed |
| D: Peak-hour rush | Up to ten occupied tables, four waiting alerts at once, cleaning backlog, a party waiting at the host stand |
| E: Tracking problems | Camera outage (gray, hatched tables, CAMERA_OFFLINE alert, nothing freed), uncertainty alerts at the booths, no flip-flopping |

Controls: play/pause, 1x/5x/10x, Restart (same seed, same events), +1 min (runs the next minute instantly).

Settings → Simulation: turn off *Simulated staff confirm…* to do every confirmation yourself.

## Headless runs

```bash
cd backend
.venv/bin/python -m tablewatch.cli simulate --scenario tracking
.venv/bin/python -m tablewatch.cli evaluate --events ../demo/generated/sim-tracking-*/events.jsonl --truth ../demo/generated/sim-tracking-*/truth.json
```

The evaluation compares events with the simulator's own ground truth. That checks the plumbing. It is **not** evidence of real-world accuracy.
