# Azure launch package — prepared, NOT deployed

One Linux VM runs the public HTTPS proxy, API and a network-disabled converter worker. This avoids a paid AI service, managed database, load balancer and registry at the initial scale. Any visitor can start a private anonymous session; there is no paid signup. The same Docker images can run on other Linux container hosts, but this deployment template targets Azure.

## Budget approval comes before launch

Suggested initial region: Korea Central, subject to subscription quota and SKU availability. Initial resources:

- `Standard_B2als_v2`: 2 vCPU, 4 GiB RAM. Burstable; sustained OCR exhausts CPU credits and slows jobs. Use a steady CPU size for sustained traffic after measuring demand.
- One 64 GiB Standard SSD LRS OS disk, also holding the Docker volumes.
- One Standard static public IPv4 address.
- Outbound bandwidth and disk transactions depend on actual use.
- Azure-provided DNS name and Caddy automated HTTPS; no custom domain purchase is needed.

This is not a free Azure deployment. The converter has zero AI API/token charges, but VM, disk, IP and network charges apply. Budget alerts are notifications, not a hard spending cap. Ask the owner to approve a monthly budget and verify current retail/account pricing before creating anything. No deployment command has been executed by the implementation task.

`python3 deploy/azure/estimate.py` reads Microsoft's public retail price API without Azure credentials or creating resources. It lists matching meters for review rather than silently picking Windows, Spot or low-priority rates. See `cost-estimate.json` for the captured estimate, if present.

## Validate without launching

```sh
az bicep build --file deploy/azure/main.bicep --outfile /tmp/sejong-main.json
```

After choosing the subscription, region and reviewed source revision, copy `parameters.example.json` to an ignored local parameters file. Use a valid lowercase DNS label, full commit SHA and an existing administrator SSH public key. The public repository is cloned at that exact commit; no GitHub credential is stored on the VM. Never put secrets into custom data.

Before launch, inspect availability and policy with your Azure subscription, then run deployment validation and `what-if`. Those require an existing resource group. **Creating the group and deployment below is a post-approval operator step**, not part of preparing this package:

```sh
az group create --name sejong-converter-rg --location koreacentral
az deployment group validate --resource-group sejong-converter-rg --template-file deploy/azure/main.bicep --parameters @deploy/azure/parameters.local.json
az deployment group what-if --resource-group sejong-converter-rg --template-file deploy/azure/main.bicep --parameters @deploy/azure/parameters.local.json
az deployment group create --resource-group sejong-converter-rg --template-file deploy/azure/main.bicep --parameters @deploy/azure/parameters.local.json
```

Only inbound TCP 80 and 443 are exposed. There is no inbound SSH rule. Administration uses Azure Run Command with the operator's Azure identity; the VM has no managed identity or application cloud credentials.

## Launch acceptance

Azure VM provisioning success does not prove cloud-init or the app finished. Inspect `/var/log/cloud-init-output.log` using Run Command, then verify:

1. `docker compose ps` reports healthy API and worker.
2. The HTTPS URL opens without certificate warnings; `/healthz` returns `ok`.
3. Upload a small text PDF and a scanned PDF with OCR. Download and inspect both ZIPs.
4. A second private browser session cannot list or fetch the first session's jobs, pages or ZIPs.
5. Confirm the worker has `NetworkMode=none`, memory/CPU limits, dropped capabilities and a read-only root filesystem.
6. Confirm external port 8000 and SSH are unreachable. The proxy must overwrite client-provided forwarded addresses; do not add another proxy without updating trust settings.
7. Verify deletion and expiry. Run a real load test and set Azure budget alerts before broadly announcing the service.

## Operational limits

This is a bounded, single-node public beta design, not a highly available platform. SQLite and files share a Docker volume on the VM disk. One worker serializes jobs and recovers page checkpoints after restart. Job subprocesses share the worker volume, so this is not per-customer VM isolation. Do not store sensitive regulated documents. Keep parser packages and container base images patched; for stronger adversarial isolation move each job into a disposable container/VM with only its own input and output mounts.

Anonymous cookie possession grants access; clearing cookies loses access. No cross-device account or password recovery exists. Limits are per session and per IP; distributed abuse is still possible. Downloads are not bandwidth-metered. Files expire after 24 hours and the worker removes them; if the worker is down, expiry still blocks reads but physical deletion waits until it returns. Monitor worker health, free disk, oldest queued job and CPU credits. Do not automatically restart an unhealthy worker in a tight loop.

Rebuild patched images regularly with `docker compose build --pull`, then `docker compose --profile public up -d`. Use a reviewed commit for upgrades. Do not use `docker compose down -v` unless deleting all uploaded documents is intended. Old VM disks/IPs can keep billing after deallocation; review all resources during shutdown. No backups are configured for intentionally temporary uploads.
