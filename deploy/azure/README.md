# Azure Container Apps deployment

The public API scales to zero; a Storage Queue starts a one-shot Container Apps Job for conversion. Private Blob Storage holds uploads, page checkpoints and exports. Managed identities authenticate storage access. No AI service, paid converter, VM, registry subscription or Log Analytics workspace is required.

## Budget and limits

Owner-approved target: **US$25/month**, not a guaranteed bill or spending cap. A resource-group Azure budget notifies Owners at 80% and 100%; its amount is denominated in the subscription's billing currency. This subscription bills in KRW. The deployed alert budget is **₩33,000/month**, conservatively below the US$25 target using September 23 indicative exchange rates (roughly ₩1,350–1,365/USD). It does not automatically track exchange rates. [Rate reference](https://www.exchangerates.org.uk/USD-KRW-exchange-rate-history.html).

Japan East retail compute rates checked 2026-09-23: $0.000024 per active vCPU-second and $0.000003 per GiB-second. Monthly subscription-wide grants are 180,000 vCPU-seconds and 360,000 GiB-seconds. Ten daily processing starts × 30 days × the 2,100-second platform timeout × 1 vCPU/2 GiB would be approximately **$13.50 in worker compute after unused free grants**, or $18.90 without grants. This is an illustrative conversion-compute estimate, not a total-bill cap: API usage, startup/duplicate executions, storage, queue/blob operations, downloads, taxes and other subscription usage are additional. See [Azure pricing](https://azure.microsoft.com/pricing/details/container-apps/) and [retail pricing API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices).

Defaults: ten accepted conversions/retries and ten processing starts per rolling day globally, one processing slot, 200 MiB input, 500 pages, 1 GiB output, 30-minute conversion timeout (35-minute platform timeout), three attempts per job, three retained jobs per browser, ten uploads per IP per day. Downloads have a shared 1 GiB/day and 10,000-request/day allowance. These conservative public-beta quotas can refuse new work; they do not replace Azure cost monitoring. Abuse can still generate API and storage operation charges.

Access expires 24 hours after upload. Storage lifecycle cleanup is asynchronous after objects are older than one day; physical deletion is not guaranteed at exactly 24 hours. Delete revokes access immediately and queues cleanup. Closing the browser does not cancel queued conversion. A failed queue dispatch is repaired on the next job-list request.

## Publish and deploy

1. Run the repository's **Publish reviewed converter images** GitHub Actions workflow. It publishes `sejong-api` and `sejong-cloud-worker` to GHCR. Make those public-source packages public, then obtain their immutable digests. No user documents belong in images.
2. Sign in with Azure CLI and select the subscription. Register `Microsoft.App`, `Microsoft.Storage`, `Microsoft.ManagedIdentity` and `Microsoft.Consumption`.
3. Create `sejong-converter-rg` in a supported Consumption region. Validate the template and inspect its what-if before deploying:

```sh
az deployment group validate -g sejong-converter-rg -f deploy/azure/main.bicep \
  -p location=japaneast apiImage=ghcr.io/becko0312/sejong-api@sha256:REPLACE \
     workerImage=ghcr.io/becko0312/sejong-cloud-worker@sha256:REPLACE \
     monthlyBudget=33000 budgetStart=2026-09-01 budgetEnd=2027-10-01
# Use the same arguments with `what-if`, then `create`.
```

The template outputs the public HTTPS URL. Check `/healthz`, upload a synthetic PDF, wait for its queue-triggered job, download and inspect the ZIP, and verify another browser session cannot access it. Do not publish the supplied textbook as a shared sample.

The API uses 0.25 vCPU/0.5 GiB and zero-to-one replicas; the job uses 1 vCPU/2 GiB. A blob lease serializes actual conversion even when the scaler starts duplicate executions. Queue visibility renewals and fenced job leases support interruption recovery. Checkpoints allow page reuse on retry. The converter subprocess has resource limits, no identity variables, and a seccomp rule denying new network sockets. The supervisor must retain network access to private Azure storage. This is a low-traffic beta, not a hardened multi-tenant isolation guarantee.

## Local cloud-path verification

```sh
docker compose -f compose.azure-local.yaml up --build -d api
# Open http://127.0.0.1:8002, upload a PDF, then run one queued job:
docker compose -f compose.azure-local.yaml --profile job run --rm job
RUN_AZURE_EMULATOR_TESTS=1 .venv/bin/python -m pytest -q
```

Azurite is bound to localhost with a deliberately non-secret development key. Production disables shared-key storage authentication. Browser sessions are anonymous and private; clearing cookies loses access. No account recovery or billing integration is implemented.

The older VM deployment remains under `legacy-vm/` for reference and is not the selected deployment.

## Daily credit-usage email

`scripts/azure_credit_report.py` queries `Microsoft.CostManagement/query` for month-to-date and daily cost, then emails a short summary. The GitHub Actions workflow `.github/workflows/azure-credit-report.yml` runs it once a day (08:00 UTC) and on manual dispatch, so the private recipient sees each day's Azure spend without opening the portal.

Set these in the repository's GitHub settings, then run the workflow once from the Actions tab to confirm delivery:

- **Secrets**: `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (service principal with **Cost Management Reader** on the subscription or resource group), `REPORT_SMTP_PASSWORD` (Gmail App Password for the sender account).
- **Variables**: `REPORT_EMAIL_TO=becko0312@gmail.com`, `REPORT_EMAIL_FROM=<gmail address>`, optionally `REPORT_SMTP_USERNAME`, `REPORT_SMTP_HOST` (defaults to `smtp.gmail.com`), `REPORT_SMTP_PORT` (defaults to `587`), `REPORT_SCOPE` (subscription by default; set to `/subscriptions/<id>/resourceGroups/sejong-converter-rg` to scope to this deployment), `REPORT_BUDGET_AMOUNT` (e.g. `33000` to match the KRW alert budget), `REPORT_BUDGET_CURRENCY` (e.g. `KRW`).

Locally, populate the same names in `.env`, then `REPORT_DRY_RUN=1 python scripts/azure_credit_report.py` prints the exact message without contacting SMTP.
