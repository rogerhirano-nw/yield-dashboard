# Seller communications templates

## Chumba (VGW) — delivery issue / impression goal sync

**When to use:** Line items show as Delivering in GAM but are not actually delivering.
First step is to ask the client (JC, AM for Chumba) whether impression goals on their
(TTD) side match what's set in GAM.

**How to pull current LI goals + deal IDs:**
```bash
cd ~/code/yield-dashboard
source .venv/bin/activate

# Step 1 — get active LIs and their impression goals
python3 -c "
import os
for line in open('.env'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip()
from gam_client import GAMClient
c = GAMClient()
df = c.get_active_line_items()
chumba = df[df['order_name'].str.contains('Chumba|VGW', case=False, na=False)]
print(chumba[['line_item_id','line_item_name','impressions_goal','status','end_date']].to_string())
"

# Step 2 — get deal IDs for those LIs (replace dates and LI IDs as needed)
python3 -c "
import os
from datetime import date
for line in open('.env'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip()
from gam_client import GAMClient
c = GAMClient()
df = c.run_li_deal_map_report(date(2026, 6, 1), date.today())
target_lis = ['7261985472','7261985475','7265669234','7265669240','7328197875']
print(df[df['line_item_id'].isin(target_lis)].to_string())
"
```

**Email template (June 2026 instance — JC / Chumba):**

Subject: Chumba June — Delivery Issue / Impression Goal Sync

> Hi JC,
>
> Hope you're well. I wanted to flag that most of the Chumba June line items have
> not been delivering over the past few days on our end, despite showing as active
> in GAM.
>
> Before digging deeper, I wanted to check whether the impression goals on the deal
> have been updated on your side to reflect what we have on ours. Here's what we
> currently have set:
>
> | Audience | Size | Imp Goal | Deal ID |
> |---|---|---|---|
> | Casino-Gamblers | 300x250 / 728x90 / 970x250 | 2,730,519 | 4149263 |
> | Casino-Gamblers | 320x50 | 6,965,000 | 4211124 |
> | Social-Gamblers | 300x250 / 728x90 / 970x250 | 2,728,144 | 4138162 |
> | Social-Gamblers | 320x50 | 2,800,000 | 4138066 |
>
> Could you confirm whether the impression goals on your end match ours? A mismatch
> there would be the most likely cause of the delivery drop.
>
> Happy to jump on a call if easier. Let me know.
>
> Thanks,
> Roger
