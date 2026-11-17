# Grafana for the usage ledger: setup at the box (Stage 4)

This folder holds a local Prometheus and Grafana. Prometheus reads the usage-ledger receiver's
counters on `127.0.0.1:4318/metrics` every 15 seconds and keeps them for 400 days. Grafana shows
them on the "gk-harness usage" dashboard. `gkh report` stays the exact record; Grafana is a live
view.

- Prometheus listens on `127.0.0.1:9090` only.
- Grafana listens on `127.0.0.1:3030` only. Anonymous viewers see the dashboard; only the admin
  can change anything.
- Both containers use host networking, so Prometheus can reach the receiver on `127.0.0.1`.
- Nothing leaves the machine. `compose.yml` switches off every Grafana default that calls
  grafana.com or gravatar.com: usage reporting, update checks, the news feed, plugin
  signature-key retrieval, the plugin catalogue, the preinstalled apps (such as
  `grafana-lokiexplore-app`), Gravatar and external snapshots. Each setting was checked against
  Grafana 11.3.0's `conf/defaults.ini`. Recheck them if you bump the Grafana tag.
- As a guard by construction, Grafana's `HTTP_PROXY` and `HTTPS_PROXY` point at
  `127.0.0.1:9`, where nothing listens, so any outbound HTTP call a setting missed fails.
  `NO_PROXY=127.0.0.1,localhost` keeps the Prometheus data source working.
- `compose.yml` never pulls an image (`pull_policy: never`). The pull is one explicit
  `sudo docker pull` per pinned tag, on Carl's go (step 5).
- Data sits in the Docker named volumes `prometheus-data` and `grafana-data`. On this box,
  Docker's data-root is `/srv/gkdata/docker`, on the LUKS volume, and `docker.service` starts
  only after `gk-data unlock` (by hand, `sudo gk-data unlock`, or by the automatic unlock where
  it is enrolled). So after a reboot the stack comes back by itself (`restart: unless-stopped`)
  only once `gk-data unlock` has run and Docker is up. Until then the dashboard is down.
- The dashboard shows times in the browser's time zone, so its "today" starts at local
  midnight. `gkh report` uses UTC days. In British summer time the two "todays" differ by an
  hour.

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

1. **Run Proof P8.** Check that Docker runs, where its data lives, that ports 3030 and 9090
   are still free, and that the receiver answers.
   ```bash
   sudo gk-data status
   systemctl is-active docker
   sudo docker info --format '{{.ServerVersion}} {{.DockerRootDir}}'
   ss -ltn | grep -E ':(3030|9090)\b' || echo "3030 and 9090 are free"
   gkh ledger status
   curl -s http://127.0.0.1:4318/metrics | head -5
   ```
   `systemctl is-active docker` must say `active`. The data-root is expected to be
   `/srv/gkdata/docker`, on the LUKS volume: Docker, and with it this stack, then runs only
   after `gk-data unlock`. If Docker is not active, run `sudo gk-data unlock` first. Stop here if
   a port is taken or the receiver does not answer. Three parts of P8 are checked later: that
   nothing connects out (step 6), the scrape over host networking (step 7), and the return
   after a reboot (step 7, last check).

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

5. **Pull the images.** This is a download, so it waits for Carl's go. `compose.yml` says
   `pull_policy: never`, so `gkh grafana up` never downloads anything; the pull is this explicit
   step. The sudoers rule does not allow it, so it asks for your password.
   ```bash
   sudo docker pull prom/prometheus:v2.55.1
   sudo docker pull grafana/grafana-oss:11.3.0
   ```
   These are the two tags pinned in `compose.yml`. To bump one, change it in this folder,
   commit, repeat step 2, and pull the new tag the same way.

6. **Start the stack.**
   ```bash
   gkh grafana up
   gkh grafana status
   ```
   `status` must show both containers running, `grafana: up` and `prometheus: up`.

   On the first `up`, check that neither container connects out. First prove that the checks
   can see the two processes at all, so that an empty result means something:
   ```bash
   sudo ss -ltnp | grep -E '"(grafana|prometheus)"'
   sudo ss -tunp state connected | grep -E '"(grafana|prometheus)"'
   ```
   - The first command lists every listener of both processes. It must show `grafana` on
     `127.0.0.1:3030` and `prometheus` on `127.0.0.1:9090`, and nothing that is not
     `127.0.0.1`. If a name is missing, the process is called something else here: fix the
     names in these commands before going on.
   - The second must list at least one `prometheus` line to `127.0.0.1:4318` (the scrape). A
     `grafana` line to `127.0.0.1:9090` shows once the dashboard has been opened.

   Then run the outbound filter a few times in the first two minutes:
   ```bash
   sudo ss -tunp state connected | grep -E '"(grafana|prometheus)"' \
     | grep -vE '127\.0\.0\.1:[0-9]+ +127\.0\.0\.1:|\[::1\]:[0-9]+ +\[::1\]:' \
     || echo "no outbound connections"
   ```
   Every run must print `no outbound connections`. `state connected` includes connections still
   being opened and connected UDP sockets (DNS). A line with another address means Grafana or
   Prometheus is calling out. Run `gkh grafana down` and find the setting before going on.

7. **Run the live checks.**
   ```bash
   ss -ltn | grep -E ':(3030|9090)\b'
   curl -s 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22gk-ledger%22%7D'
   ```
   - `ss` must show 3030 and 9090 on `127.0.0.1` only. Repeat `sudo ss -ltnp` from step 6: every
     listener of both processes must be on `127.0.0.1`.
   - Repeat the outbound check from step 6, the positive control first. It must still print
     `no outbound connections`.
   - The query must return the value `1` while the receiver runs. That proves the scrape over
     host networking, the last part of P8.
   - Make a call in a session. It must show in Grafana within a minute.
   - Compare one day's spend in Grafana with `gkh report --source ledger --since <that day>`.
     They must agree within 1%. `gkh report` counts UTC days and the dashboard the browser's
     time zone, so set the dashboard's time zone to UTC for the comparison.
   - `gkh grafana down` must stop both containers. `gkh grafana up` must then bring back the
     same data.
   - **Reboot check**, at the next planned reboot. Before `gk-data unlock` has run, Docker is not
     running and `gkh grafana status` fails: that is expected. Once the unlock has run (by hand,
     `sudo gk-data unlock`, or automatically), `systemctl is-active docker` must say `active`,
     and `gkh grafana status` must then show `grafana: up` and `prometheus: up` without a
     `gkh grafana up`. The charts must still hold the data from before the reboot.

8. **Open the dashboard from the Mac.** Forward the port over SSH, then open the page.
   ```bash
   ssh -L 3030:127.0.0.1:3030 <box>
   ```
   Open `http://localhost:3030`. The dashboard is in the `gk-harness` folder.

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
- The receiver exits after 30 minutes with no request and no live session. Prometheus's scrape
  of `/metrics` every 15 seconds counts as a request, so while Prometheus runs the receiver does
  not idle out, and "Receiver up" stays Up. That is on purpose: fewer restarts mean fewer
  counter resets. "Receiver up" shows Down when the receiver crashed or was stopped (for
  example by `gkh ledger off`), or before the first session after a reboot starts it.
- The dashboard uses the browser's time zone; `gkh report` uses UTC days. Compare a day's
  spend between them with the dashboard's time zone set to UTC (dashboard settings), or in
  winter, when UK time is UTC.
- "Spend by source" groups spend by Claude Code's `query_source`. It stands in for a
  background-spend panel, because the receiver cannot tell a background call from a transcript
  one.
- To remove the stack: `gkh grafana down`, then `sudo rm /etc/sudoers.d/gk-grafana` and
  `sudo rm -r /etc/gk-grafana`. The data stays in the named volumes until you remove them with
  `sudo docker volume rm gk-grafana_prometheus-data gk-grafana_grafana-data`.
