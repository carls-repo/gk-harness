# Grafana for the usage ledger: setup at the box (Stage 4)

This folder holds a local Prometheus and Grafana. Prometheus reads the usage-ledger receiver's
counters on `127.0.0.1:4318/metrics` every 15 seconds and keeps them for 400 days. Grafana shows
them on the "gk-harness usage" dashboard. `gkh report` stays the exact record; Grafana is a live
view.

- Prometheus listens on `127.0.0.1:9090` only.
- Grafana listens on `127.0.0.1:3300` only. Anonymous viewers see the dashboard; only the admin
  can change anything.
- Both containers use host networking, so Prometheus can reach the receiver on `127.0.0.1`.
- Nothing leaves the machine. `compose.yml` switches off every Grafana default that calls
  grafana.com or gravatar.com: usage reporting, update checks, the news feed, plugin
  signature-key retrieval, the plugin catalogue, the preinstalled apps (such as
  `grafana-lokiexplore-app`), Gravatar and external snapshots. Each setting was checked against
  Grafana 11.3.0's `conf/defaults.ini`. Recheck them if you bump the Grafana tag.
- Data sits in the Docker named volumes `prometheus-data` and `grafana-data`. The stack comes
  back after a reboot without the LUKS unlock.

Your user is not in the `docker` group, and joining it would amount to root. So the files are
installed root-owned in `/etc/gk-grafana/`, and the sudoers rule `/etc/sudoers.d/gk-grafana`
allows exactly three commands: `docker compose -f /etc/gk-grafana/compose.yml` with `up -d`,
`down` or `ps`. `gkh grafana up|down|status` runs those three.

| File | What it is |
|---|---|
| `compose.yml` | the two services, with pinned images |
| `prometheus.yml` | the scrape of the receiver |
| `provisioning/datasources/prometheus.yml` | the data source, uid `gk-prometheus` |
| `provisioning/dashboards/gk.yml` | loads the dashboards in `/var/lib/grafana/dashboards` |
| `dashboards/gk-harness-usage.json` | the dashboard, uid `gk-harness-usage` |
| `sudoers-gk-grafana` | the sudoers rule |

## Setup

Carl does every step, at the box, in a plain terminal. Every `sudo` step is his. Run the steps
from the top of the gk-harness checkout.

1. **Run Proof P8.** Check that Docker runs, that ports 3300 and 9090 are still free, and that
   the receiver answers.
   ```bash
   sudo docker info --format '{{.ServerVersion}}'
   ss -ltn | grep -E ':(3300|9090)\b' || echo "3300 and 9090 are free"
   gkh ledger status
   curl -s http://127.0.0.1:4318/metrics | head -5
   ```
   Stop here if a port is taken or the receiver does not answer. Two parts of P8 are checked
   later: the scrape over host networking (step 7), and that nothing connects out (step 6).

2. **Install the files root-owned in `/etc/gk-grafana/`.**
   ```bash
   sudo install -d -m 755 -o root -g root /etc/gk-grafana
   sudo cp -r deploy/grafana/compose.yml deploy/grafana/prometheus.yml \
     deploy/grafana/provisioning deploy/grafana/dashboards /etc/gk-grafana/
   sudo chown -R root:root /etc/gk-grafana
   sudo chmod -R go-w /etc/gk-grafana
   ls -lR /etc/gk-grafana
   ```
   Every file must be owned by root and not writable by your user. The sudoers rule is safe only
   while that holds.

3. **Write the admin password.** The command writes it into `grafana.env` and never prints it.
   ```bash
   sudo install -m 600 -o root -g root /dev/null /etc/gk-grafana/grafana.env
   python3 -c 'import secrets;print("GF_SECURITY_ADMIN_PASSWORD="+secrets.token_urlsafe(24))' | sudo tee /etc/gk-grafana/grafana.env >/dev/null
   sudo stat -c '%U %a %n' /etc/gk-grafana/grafana.env
   ```
   The last line must show `root 600`. Grafana reads the password only on its first start, when
   it creates the admin user. To read it later, run `sudo cat /etc/gk-grafana/grafana.env` in a
   terminal that no session records.

4. **Check and install the sudoers rule.**
   ```bash
   sudo visudo -cf deploy/grafana/sudoers-gk-grafana
   sudo install -m 440 -o root -g root deploy/grafana/sudoers-gk-grafana /etc/sudoers.d/gk-grafana
   sudo -n -l | grep gk-grafana
   ```
   The first line must say `parsed OK`. Do not install the file if it does not.

5. **Pull the images.** This is a download, so it waits for Carl's go. The sudoers rule does not
   allow `pull`, so this step asks for your password.
   ```bash
   sudo /usr/bin/docker compose -f /etc/gk-grafana/compose.yml pull
   ```
   The tags are pinned in `compose.yml` (`prom/prometheus:v2.55.1` and
   `grafana/grafana-oss:11.3.0`). To bump one, change it in this folder, commit, and repeat
   step 2.

6. **Start the stack.**
   ```bash
   gkh grafana up
   gkh grafana status
   ```
   `status` must show both containers running, `grafana: up` and `prometheus: up`.

   On the first `up`, check that neither container connects out. Run this a few times in the
   first two minutes:
   ```bash
   sudo ss -tnp state established | grep -E '"(grafana|prometheus)"' \
     | grep -vE '127\.0\.0\.1:[0-9]+ +127\.0\.0\.1:|\[::1\]:[0-9]+ +\[::1\]:' \
     || echo "no outbound connections"
   ```
   Every run must print `no outbound connections`. A line with another address means Grafana or
   Prometheus is calling out. Run `gkh grafana down` and find the setting before going on.

7. **Run the live checks.**
   ```bash
   ss -ltn | grep -E ':(3300|9090)\b'
   curl -s 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22gk-ledger%22%7D'
   ```
   - `ss` must show 3300 and 9090 on `127.0.0.1` only.
   - Repeat the outbound check from step 6. It must still print `no outbound connections`.
   - The query must return the value `1` while the receiver runs. That proves the scrape over
     host networking, the last part of P8.
   - Make a call in a session. It must show in Grafana within a minute.
   - Compare one day's spend in Grafana with `gkh report --source ledger --since <that day>`.
     They must agree within 1%.
   - `gkh grafana down` must stop both containers. `gkh grafana up` must then bring back the
     same data.

8. **Open the dashboard from the Mac.** Forward the port over SSH, then open the page.
   ```bash
   ssh -L 3300:127.0.0.1:3300 <box>
   ```
   Open `http://localhost:3300`. The dashboard is in the `gk-harness` folder.

## Day to day

| Command | What it does |
|---|---|
| `gkh grafana up` | starts both containers (`docker compose up -d`) |
| `gkh grafana down` | stops and removes both containers; the named volumes and their data stay |
| `gkh grafana status` | shows the containers (`docker compose ps`), then checks Grafana's `/api/health` and Prometheus's `/-/ready`, 2 seconds each |

Each command runs only on Linux. It exits 0 on success and 1 when a compose command or a health
check fails.

## Notes

- The receiver's counters start at zero each time it starts. Prometheus treats that as a counter
  reset, so the charts lose only the calls between the last scrape and a restart.
- The receiver exits after 30 minutes with no calls and no live session. "Receiver up" then
  shows Down. That is normal while nothing runs.
- "Spend by source" groups spend by Claude Code's `query_source`. It stands in for a
  background-spend panel, because the receiver cannot tell a background call from a transcript
  one.
- To remove the stack: `gkh grafana down`, then `sudo rm /etc/sudoers.d/gk-grafana` and
  `sudo rm -r /etc/gk-grafana`. The data stays in the named volumes until you remove them with
  `sudo docker volume rm gk-grafana_prometheus-data gk-grafana_grafana-data`.
