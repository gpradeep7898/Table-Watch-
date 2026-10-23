# Restaurant pilot readiness

Nothing here has been deployed in a restaurant. This document prepares a first supervised pilot and defines how to measure it honestly.

## 1. Qualification questions

- How many tables, how many on the floor at peak, how many servers per shift?
- Do you already have cameras over the dining room? Brand, resolution, where they point, who installed them?
- Can the cameras be reached on the local network (RTSP)? Who manages the recorder/NVR?
- Is there a spare computer on site (or can we place a small one) that can stay on during service?
- What hurts most today: forgotten tables, slow bussing, no data on turnover?
- Who would confirm service and cleaning, and on what device?
- Is there a manager willing to review the dashboard for two weeks and correct it?

## 2. Camera compatibility

Use the checklist in [camera-integration.md](camera-integration.md). For each table record: camera, visible fully / partly / not, expected occlusions, lighting notes.

## 3. Viewing angles, lighting, obstruction

- Best: overhead or steep angle covering seats. Anchor = body centre (overhead) or feet (angled).
- Risky: shallow angles where people at a near table hide a far table; window backlight; dim dinner lighting; mirrors; screens or TV motion in view.
- Draw zones on a frame captured **during service** at the darkest hour.

## 4. Local hardware

- MVP baseline: any recent laptop or mini PC. A Mac with Apple Silicon is the development target.
- Rough budget: per camera at 4 analysed frames/s, YOLOX-Nano needs ≈ 15–20 ms/frame on a modern CPU core. Measure on site with the Cameras page; do not assume.
- The machine must not sleep during service. Wired network to the cameras preferred.

## 5. Staff workflow

- Hosts/servers confirm service (one tap) and bussers confirm cleaning.
- The manager watches alerts, acknowledges them, and corrects statuses with a reason.
- Uncertain tables (gray, hatched, "?") need a quick human look; the system will not guess.
- Fallback: if the dashboard or a camera fails, the restaurant runs exactly as before. Nothing in service depends on TableWatch.

## 6. Privacy, consent and legal

- No face recognition, no identification, no audio, no cloud upload, no raw video storage (defaults).
- Get legal review for the jurisdiction: CCTV notices, purpose limitation, employee monitoring rules (staff are filmed too), data protection (e.g. GDPR/UK GDPR, CCPA). Biometric laws such as Illinois BIPA should not apply because no biometric identifiers are processed; confirm this with counsel.
- Signage at the entrance stating that cameras are used to monitor table occupancy, without identification.
- Inform and, where required, consult staff representatives before the pilot.
- Written authorization from the owner of the camera system (the *I am authorized* checkbox is not a substitute).
- Data handling: events only, stored locally, retention set in Settings (default 90 days), deletion per dataset. Annotated debug video only if explicitly enabled, local, deleted after the agreed hours.

## 7. Pilot success metrics

Measured against human-annotated ground truth from authorized recordings (format below):

| Measure | Definition | How |
|---|---|---|
| Occupancy transition accuracy | precision / recall of seatings and departures within ±60 s | `evaluate` → `seating`, `departure` |
| False occupancy alerts | predicted seatings with no real party | `seating.false_positives` |
| Missed guest departures | real departures not detected | `departure.missed_departures` |
| False departure detections | departures while guests still seated | `departure.false_departures` |
| Service visit identification | recall/precision of possible staff visits vs annotated visits | `staff_visits` |
| Median detection delay | event time − annotated time | `median_detection_delay_s` |
| Occupancy agreement | share of table-seconds where occupied/free matches | `occupancy_state_agreement` |
| Camera uptime | observed ÷ scheduled time | Analytics → Camera uptime |
| Operator correction rate | corrections ÷ sessions | `operator_correction_rate`, Analytics |
| Cleaning turnaround quality | confirmed cleaning times vs annotated bussing | compare `CLEANING_CONFIRMED` with annotation |

Suggested go/no-go thresholds must be agreed with the restaurant **before** the pilot. Synthetic results in this repo do not count toward any of them.

## 8. Repeatable evaluation process

1. Get written permission. Record 1–2 hours of normal service from the pilot camera(s) to `media/` (local only).
2. Annotate: watch the video and log seatings, departures and staff visits with timestamps in seconds from the start of the file.
3. Draw zones in the UI on a frame from the same video; export them to a zones JSON file (or copy from `GET /api/zones/<camera>`).
4. Run:
   ```bash
   cd backend
   .venv/bin/python -m tablewatch.cli analyze --video ../media/pilot.mp4 --zones ../configs/zones/pilot.json --out ../demo/generated/pilot-1
   .venv/bin/python -m tablewatch.cli evaluate --events ../demo/generated/pilot-1/events.jsonl --truth ../media/pilot.truth.json --tolerance 60
   ```
5. Change one threshold or zone at a time, rerun, and keep the summaries. Delete the recording when the agreed retention ends.

### Ground-truth annotation format (`tablewatch-groundtruth/v1`)

```json
{
  "format": "tablewatch-groundtruth/v1",
  "source": "Manual annotation of pilot.mp4 by J. Doe, 2026-11-02",
  "events": [
    { "type": "seated",      "table": "T3", "t": 125.0 },
    { "type": "staff_visit", "table": "T3", "start": 300.0, "end": 331.0 },
    { "type": "departed",    "table": "T3", "t": 2410.5 }
  ]
}
```

`seated` = the moment the party sits; `departed` = the last guest leaves the seating area; `staff_visit` = a staff member at the table. The simulator writes this same format (`cli simulate`), which is how the evaluator itself is tested.

## 9. Operational fallback

- Camera offline → tables freeze and a critical alert appears; staff run the floor as usual.
- Server down → the dashboard shows "Reconnecting"; no data is lost on restart (events are replayed).
- Wrong status → *Correct status* with a reason; corrections are counted so the pilot measures them.
