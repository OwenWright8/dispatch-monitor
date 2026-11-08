# Recorder: Raspberry Pi 4 + Airspy R2

Records the five Tippecanoe dispatch talkgroups off the air with
[trunk-recorder](https://github.com/robotastic/trunk-recorder) and sends each call to
Dispatch Monitor (`POST /api/call-upload`). No OpenMHz, no Cloudflare.

The county system (RadioReference sid 9099) is P25 Phase I simulcast, 851.050–857.7375 MHz.
The Airspy R2 at 10 MS/s centered on 854.4 MHz covers all of it with one radio.

## Set up

1. **Pi:** Raspberry Pi OS Lite **64-bit**, then Docker:
   `curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER` (log out and back in).
2. **Copy this folder** to the Pi, e.g. `~/recorder`.
3. **Dispatch Monitor:** put a long random key in its `.env`
   (`UPLOAD_API_KEY=...`, e.g. from `openssl rand -hex 24`) and restart it.
4. **config.json:** replace `DISPATCH_MONITOR_HOST` with the IP of the machine running
   Dispatch Monitor, and `PASTE_UPLOAD_API_KEY_HERE` with the same key.
5. Plug in the Airspy (USB 3 port is fine) and start it: `docker compose up -d`.
6. Watch it: `docker compose logs -f`. Within a minute you should see the control channel
   lock and decode rates near 40/sec, then calls on the dispatch talkgroups being uploaded.
   The dashboard header turns **Live**. Once that's steady, set `OPENMHZ_ENABLED=false`.

## Tuning

- **Gain** (`lnaGain`, `mixGain`, `ifGain`, 0–15 each): close to the towers, less is often
  better. If decode rate is low or calls are garbled, try lowering `lnaGain` first, then
  raising it; change one at a time and restart.
- **Decode rate** in the log below ~30/sec usually means weak signal or simulcast distortion:
  move the antenna higher, nearer a window, and away from the Pi and USB cables.
- **CPU:** 10 MS/s is heavy for a Pi 4. Use a fan case. If `top` shows the CPU pinned and
  calls drop, lower `digitalRecorders` to 3.
- **Frequency error:** if the control channel won't lock, set `"error"` (Hz) — the log
  prints the measured offset.

Only the talkgroups in `talkgroups.csv` are recorded (`recordUnknown: false`). Add a row there
and a channel in Dispatch Monitor's county profile to monitor more.
