# CAOS API runtime configuration (RUNTIME-CONFIG-0.1)

How the development API container (`docker-compose.yml`, service `api`) starts, which
time zone it uses and how it is restarted. Status: implemented in source and verified in
disposable infrastructure; the live container adopts it only through the controlled
recreation below.

## Governed runtime versus developer hot reload

- The API starts with `python -m src.base.system.serve` (Compose `command`), from the
  mounted `src`, so this needs no image rebuild. `entrypoint.py`, the image's default
  command, calls the same module for future image builds.
- **Hot reload is off unless explicitly enabled.** `CAOS_API_RELOAD` accepts only `true`
  or `false` (any case, surrounding spaces ignored); unset or empty means `false`, and
  any other value refuses to start. `ENVIRONMENT` never enables it, because
  `ENVIRONMENT=development` also selects local authentication, the local Genesis policy,
  Nataraja's development routes and local storage modes.
- With reload off, Uvicorn serves in the container's main process (pid 1): there is no
  reload supervisor, `docker stop` delivers SIGTERM directly to the server, and lifespan
  shutdown (including the CPU executors) runs before the process exits. Editing the
  mounted source never restarts the API.
- **Developers who want hot reload** set `CAOS_API_RELOAD=true` in a local, uncommitted
  Compose override. Never on a runtime that holds live authority: every change to the
  mounted source would then reload it outside any controlled procedure.

## Time zone

- The container uses the image's own time zone data: `TZ=Europe/Stockholm`, and no host
  `/etc/localtime` or `/etc/timezone` mount.
- The former host mounts tied the container to the physical path of macOS's versioned
  time zone database (a macOS update made the container unstartable), and, because the
  image's `/etc/localtime` is a symlink to `Etc/UTC`, laid the host's Stockholm file over
  the container's UTC zone: `ZoneInfo("UTC")` and `ZoneInfo("Etc/UTC")` returned +01:00.
- Persisted and authority-relevant timestamps (UTU, OPS V2, leases, Activity Context,
  drafts, Manuscripts, Seshat) use `datetime.now(UTC)`, epoch time or Redis TTLs and are
  unaffected by the container's zone. Local time is presentation only: log timestamps,
  the date Tool and crawler report metadata.
- Zone rules change only when the image is rebuilt (the image's `tzdata`).

## Crawler CPU workers

`CpuExecutor` (`tools/web_crawler_tool/execution/crawler_cpu_executor.py`) creates its
`ProcessPoolExecutor` with `multiprocessing.get_context("spawn")`. Workers never fork the
threaded API process, whether or not Uvicorn's reload supervisor started the server
(before, they inherited `spawn` only from the reload supervisor). Worker counts (4 and 6),
submission, error propagation and shutdown are unchanged.

## Restart policy

`api` declares `restart: "no"`: the governed API is started and stopped only by the
controlled procedures (stop, change, pre-start Nataraja `inspect`, start, verify). The
data services keep `unless-stopped`.

## Applying a configuration change to the live container

Compose bakes `command`, environment, mounts and restart policy into the container when it
is created, so these changes take effect only by recreating the API container:

1. Stop `caos-api`; integrate the commit (fast-forward); run `docker compose config` and
   check `api` (command, `restart: "no"`, `TZ`, `CAOS_API_RELOAD`, no time zone mounts);
   run the read-only Nataraja `inspect` before start.
2. Recreate only the API service from the checked-in file:
   `docker compose -p caos-api -f docker-compose.yml up --no-deps --no-build --pull never -d api`
   (dry-run first). Never `down`, `--volumes` or a dependency recreation.
3. Verify health, `UTU policy pin verified`, Nataraja `passed`, the process tree (pid 1 is
   the server; no reload supervisor), the time zone and unchanged authority and state.

**Retiring the temporary override.** After the 2026-11-04 time zone outage the live
container was recreated with an external, uncommitted override (`restart: "no"`). Once the
checked-in file itself says `restart: "no"`, step 2 above recreates the container from the
checked-in file alone; its Compose labels then list only `docker-compose.yml`, and the
override file can be deleted.
