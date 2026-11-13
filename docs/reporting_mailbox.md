# Moving report mail from agentmail to reporting@newsweek.com

The pipeline's email — incoming vendor reports (DV Attention, DV IVT, TTD
Chumba) and outgoing alerts/digests (health check, sweep alert, Confiant
summary / weekly / outreach) — ran through a third-party inbox,
`newsweek@agentmail.to`. `graph_mail.py` moves all of it onto Newsweek's own
Microsoft 365 shared mailbox **reporting@newsweek.com**, through the Microsoft
Graph API with an app-only login, so nothing depends on a personal account.

## How the switch works
Every reader and sender checks `graph_mail.enabled()`. It is True only when
`GRAPH_MAILBOX` **and** the app credentials are set; otherwise the old
agentmail code runs unchanged. So:

- **On**: add the `GRAPH_*` repo secrets below. Every workflow already passes
  them through.
- **Off / roll back**: delete the `GRAPH_MAILBOX` secret. Agentmail resumes.

The existing `ENTRA_*` secrets alone never turn it on (pinned by
`tests/test_graph_mail.py`).

Reads never change the mailbox — nothing is moved, marked read, or deleted.
Messages are found by subject anywhere in the mailbox (including forwarded
copies), newest first. Sent mail lands in reporting@'s Sent Items.

## 1. Request to Newsweek IT (M365 admin)
> Please set up app-only Microsoft Graph access to the shared mailbox
> **reporting@newsweek.com** for the yield-reporting automation:
>
> 1. An Entra app registration (new, or the existing one used for the seller
>    report) with **Application** permissions **Mail.Read** and **Mail.Send**
>    on Microsoft Graph, with admin consent granted, plus a client secret.
> 2. Restrict that app to reporting@ only, so it cannot access any other
>    mailbox:
>    ```powershell
>    New-DistributionGroup -Name "yield-reporting-app-scope" -Type Security `
>        -Members reporting@newsweek.com
>    New-ApplicationAccessPolicy -AppId <app-client-id> `
>        -PolicyScopeGroupId yield-reporting-app-scope@newsweek.com `
>        -AccessRight RestrictAccess `
>        -Description "Yield reporting automation: reporting@ only"
>    Test-ApplicationAccessPolicy -AppId <app-client-id> -Identity reporting@newsweek.com   # Granted
>    Test-ApplicationAccessPolicy -AppId <app-client-id> -Identity <any other user>         # Denied
>    ```
> 3. Send back the tenant id, client id and client secret (securely).

(Microsoft is moving from Application Access Policies to Exchange **RBAC for
Applications**; either is fine — the point is "this app, this one mailbox".)

## 2. Repo secrets (GitHub → Settings → Secrets → Actions)
| Secret | Value |
|---|---|
| `GRAPH_MAILBOX` | `reporting@newsweek.com` — **the on-switch; set this last** |
| `GRAPH_TENANT_ID` | from IT |
| `GRAPH_CLIENT_ID` | from IT |
| `GRAPH_CLIENT_SECRET` | from IT (note its expiry date — rotate before it lapses) |

The Confiant jobs run outside Actions (launchd, `.env`); add the same four
keys to that `.env` to switch them.

## 3. Point the vendors at reporting@
- **DoubleVerify** — add reporting@newsweek.com as a recipient on both
  scheduled Pinnacle reports ("Unified Analytics Report: Attention Metrics"
  and "…: IVT"). Keep agentmail on them until cutover is verified.
- **The Trade Desk** — add reporting@ to the "Newsweek Chumba Casino
  Performance report" schedule (and any future flight's report).
- Alternatively, during the overlap, forward the reports from reporting@ to
  the agentmail inbox — no vendor change needed on day one.

## 4. Cutover check
1. Wait until reporting@ has at least one of each report.
2. Set `GRAPH_MAILBOX`, then dispatch `refresh_dv.yml` and `refresh_ttd.yml`.
   The logs should read `Graph search: … match …` and `Parsed N rows … (Graph)`.
3. Dispatch `health_check.yml`: all green, and the digest arrives **from
   reporting@newsweek.com**.
4. After a week of green runs, remove the vendors' agentmail recipients and
   delete the `AGENTMAIL_*` secrets.
