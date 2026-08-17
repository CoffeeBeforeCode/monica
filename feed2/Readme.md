# Monica Feed 2 — RSS Intelligence Feed Azure Function

A daily Azure Function that fetches curated content from Chatham House, Internet Society, and World Economic Forum, then posts articles as Teams Adaptive Cards.

**Execution:** Daily at 05:20 London time  
**Runtime:** Python 3.11+  
**Deployment:** Azure Functions (Consumption Plan)

---

## Architecture Overview

### Components

| Component | Purpose |
|-----------|---------|
| **function_app.py** | Main orchestration; timer trigger entry point |
| **feed_parser.py** | RSS feed fetching and parsing (Chatham House, Internet Society) |
| **wef_scraper.py** | Web scraping for WEF Emerging Technologies page + keyword filtering |
| **teams_card_builder.py** | Formats articles into Teams Adaptive Cards (JSON) |
| **teams_webhook.py** | POSTs cards to Teams webhook with retry logic |
| **state_manager.py** | Duplicate detection and execution logging |

### Data Flow

```
Timer Trigger (05:20 UTC)
    ↓
Fetch RSS Feeds (Chatham House, Internet Society)
    ↓
Scrape WEF Page + Filter by Keywords
    ↓
Deduplicate (check against posted URLs)
    ↓
Build Teams Adaptive Cards
    ↓
POST to Teams Webhook (Special Interests channel)
    ↓
Log Execution + Update State
```

---

## Prerequisites

### Required

- **Azure Subscription** with permissions to create:
  - Azure Functions (Python 3.11 runtime)
  - Storage Account (for function state)
- **Microsoft Teams** with:
  - Teams channel (e.g., #special-interests)
  - Incoming webhook configured
- **Python 3.11+** (for local testing)
- **Git** (for deployment)

### Optional but Recommended

- **Azure CLI** (`az` command-line tool)
- **Visual Studio Code** with Azure Functions extension
- **OneDrive** (for persistent state storage; see setup below)

---

## Setup & Deployment

### Step 1: Create Azure Resources

```bash
# Install Azure CLI if needed
# https://learn.microsoft.com/en-us/cli/azure/install-azure-cli

# Login to Azure
az login

# Create Resource Group
az group create --name monica-rg --location uksouth

# Create Storage Account (for function state)
az storage account create \
  --name monicafeed2storage \
  --resource-group monica-rg \
  --location uksouth

# Create Azure Function App
az functionapp create \
  --resource-group monica-rg \
  --consumption-plan-location uksouth \
  --runtime python \
  --runtime-version 3.11 \
  --functions-version 4 \
  --name monica-feed2 \
  --storage-account monicafeed2storage
```

### Step 2: Configure Teams Webhook

1. In Teams, navigate to **#special-interests** channel (or your chosen channel)
2. Click **⋯ More** → **Connectors**
3. Search for **"Incoming Webhook"** and **Configure**
4. Name: `Monica Feed 2`
5. Upload image (optional)
6. Click **Create**
7. **Copy the webhook URL** (secret—treat like a password)

### Step 3: Set Environment Variables

```bash
# In Azure Portal:
# Function App → Configuration → Application settings

# Add new setting:
# Name: TEAMS_WEBHOOK_URL
# Value: <paste webhook URL from Step 2>
```

### Step 4: Deploy Code

#### Option A: Deploy via Git (Recommended)

```bash
# Clone repo or create local directory with all .py files
mkdir monica-feed2
cd monica-feed2
git init

# Copy all .py files and requirements.txt into this directory
# ...

# Commit files
git add .
git commit -m "Initial Feed 2 implementation"

# Deploy to Azure
az functionapp deployment source config-zip \
  --resource-group monica-rg \
  --name monica-feed2 \
  --src-path <path-to-zip-file>
```

#### Option B: Deploy via VS Code

1. Install **Azure Functions** extension in VS Code
2. Open project folder containing all .py files
3. Sign in to Azure (Click Azure icon → Sign in)
4. Right-click project folder → **Deploy to Function App**
5. Select subscription, function app name (monica-feed2)
6. Confirm deployment

#### Option C: Deploy via Azure CLI Direct

```bash
# From project directory with all .py files and requirements.txt

# Install Azure Functions Core Tools
# https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local

# Test locally first
func start

# Deploy
func azure functionapp publish monica-feed2
```

### Step 5: Enable Managed Identity (Optional but Recommended for OneDrive)

If using OneDrive for state storage:

```bash
# Enable system-assigned managed identity
az functionapp identity assign \
  --name monica-feed2 \
  --resource-group monica-rg

# Grant OneDrive permissions via Azure AD
# (Requires admin consent; see OneDrive Setup section below)
```

---

## Configuration

### Environment Variables

| Variable | Required | Example | Purpose |
|----------|----------|---------|---------|
| `TEAMS_WEBHOOK_URL` | Yes | `https://outlook.webhook.office.com/...` | Teams incoming webhook |
| `ONEDRIVE_FOLDER_PATH` | No | `/Projects/Monica/Feeds/` | OneDrive state folder |
| `LOG_LEVEL` | No | `INFO` | Python logging level |

### Feed Sources Configuration

Edit the URLs in `function_app.py` if you want to add/remove feeds:

```python
# In function_app.py feed2_timer_trigger():
chatham_house_articles = feed_parser.fetch_feed(
    url="https://www.chathamhouse.org/path/whatsnew.xml",  # Modify URL here
    source_name="Chatham House"
)
```

### WEF Keyword Filter

Edit the `KEYWORDS` list in `wef_scraper.py` to include/exclude topics:

```python
# In wef_scraper.py
KEYWORDS = [
    "artificial intelligence", "AI", "machine learning",
    # Add more keywords here
]
```

---

## State Management (Duplicate Prevention)

### Option A: Local Storage (Development Only)

Uses `/tmp/` on function instance. State is **not persistent** across restarts.

```python
# In function_app.py
from state_manager import LocalStateManager

state_manager = LocalStateManager()  # Uses local files
```

**Limitations:** Duplicates will reappear if function restarts; OK for testing.

### Option B: OneDrive Storage (Production Recommended)

Stores state in OneDrive folder `/Projects/Monica/Feeds/`. Persistent across restarts.

#### OneDrive Setup

1. **Create folder structure** in your OneDrive:
   ```
   /Projects/Monica/Feeds/
     ├── feed2_posted_urls.txt
     └── feed2_execution_log.txt
   ```

2. **Create Azure AD App Registration:**
   - Azure Portal → Azure AD → App Registrations → New Registration
   - Name: `Monica-Feed2`
   - Redirect URI: `https://<your-function>.azurewebsites.net/auth/callback`
   - Create client secret and **save the secret value**

3. **Grant permissions:**
   - In App Registration → API permissions
   - Add permission → Microsoft Graph → Files.ReadWrite.All
   - Click "Grant admin consent"

4. **Configure function with app credentials:**
   ```bash
   az functionapp config appsettings set \
     --name monica-feed2 \
     --resource-group monica-rg \
     --settings \
     AZURE_TENANT_ID="<tenant-id>" \
     AZURE_CLIENT_ID="<app-id>" \
     AZURE_CLIENT_SECRET="<secret-value>"
   ```

5. **Update function code** to use Azure identity:
   ```python
   from azure.identity import ClientSecretCredential
   from state_manager import StateManager
   
   credential = ClientSecretCredential(...)
   onedrive_client = OneDriveClient(credential)
   state_manager = StateManager(onedrive_client=onedrive_client)
   ```

---

## Local Testing

### Prerequisites

```bash
pip install -r requirements.txt
```

### Test Individual Components

#### Test RSS Parser

```python
from feed_parser import FeedParser

parser = FeedParser()
articles = parser.fetch_feed(
    "https://www.chathamhouse.org/path/whatsnew.xml",
    "Chatham House"
)
print(f"Found {len(articles)} articles")
for article in articles:
    print(f"  - {article['headline']}")
```

#### Test WEF Scraper

```python
from wef_scraper import WEFScraper

scraper = WEFScraper()
articles = scraper.scrape_and_filter()
print(f"Found {len(articles)} technology articles")
```

#### Test Teams Card Building

```python
from teams_card_builder import TeamsCardBuilder
import json

builder = TeamsCardBuilder()
article = {
    'headline': 'AI Breakthroughs in Quantum Computing',
    'summary': 'Researchers announce major progress...',
    'link': 'https://example.com/article',
    'image_url': 'https://example.com/image.jpg',
    'publish_date': '17 Aug 2026',
    'source': 'Test Source'
}

card = builder.build_card(article)
print(json.dumps(card, indent=2))  # Pretty-print card JSON
```

#### Test Full Execution (Dry Run)

```bash
# Set environment variable
export TEAMS_WEBHOOK_URL="https://outlook.webhook.office.com/..."  # Use real webhook

# Run timer trigger
func start

# In another terminal, invoke the function manually
curl -X POST http://localhost:7071/admin/functions/feed2_timer_trigger
```

---

## Monitoring & Troubleshooting

### View Execution Logs

**In Azure Portal:**

1. Function App → feed2_timer_trigger → Monitor
2. Scroll through execution logs in real-time
3. Click individual runs for detailed error messages

**Or via CLI:**

```bash
az functionapp log tail --name monica-feed2 --resource-group monica-rg
```

### Check Execution Log File (OneDrive)

Navigate to `/Projects/Monica/Feeds/feed2_execution_log.txt` in your OneDrive. Each execution appends a summary:

```
[2026-08-17 05:20:00] Feed 2 execution started
[2026-08-17 05:20:02] Chatham House: 3 new items
[2026-08-17 05:20:05] Internet Society: 1 new item
[2026-08-17 05:20:08] WEF Technology: 2 new items
[2026-08-17 05:20:10] Posted cards: 6
[2026-08-17 05:20:12] Feed 2 execution completed (12 seconds)
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| **403 Forbidden on feed fetch** | Server blocks requests without User-Agent | Already handled in code (sends browser UA) |
| **"TEAMS_WEBHOOK_URL not found"** | Environment variable not set | Check Azure Portal → Function App → Configuration |
| **Cards not appearing in Teams** | Webhook URL invalid or expired | Recreate webhook in Teams channel |
| **Duplicates reappearing** | Local state manager being used | Switch to OneDrive state manager (production) |
| **Timeout errors** | Feed server slow or unresponsive | Already has retry logic; check if feed is down |
| **"No articles found"** | Feed structure changed | Check HTML selectors in feed_parser.py or wef_scraper.py |

### Test Webhook Directly

```bash
# Test Teams webhook with curl
curl -X POST https://outlook.webhook.office.com/... \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "message",
    "attachments": [{
      "contentType": "application/vnd.microsoft.card.adaptive",
      "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
          {"type": "TextBlock", "text": "Test Card", "weight": "bolder", "size": "large"}
        ]
      }
    }]
  }'

# Should return 1 if successful
```

---

## Performance & Scaling

### Execution Time Budget

- **Timeout:** 90 seconds (per spec, adequate for web scraping)
- **Typical execution:** 12–20 seconds (3 feeds + 6–8 cards posted)
- **Worst case:** ~60 seconds if WEF scraper is slow

### Scaling Considerations

If running multiple instances:
- Teams webhook is stateless; concurrent POSTs are fine
- Duplicate detection uses in-memory set (small footprint)
- State file (OneDrive) uses last-write-wins; race conditions unlikely with one daily run

---

## Maintenance

### Update Feed URLs

Edit `function_app.py`, modify feed URLs, and redeploy:

```bash
git commit -am "Update feed URLs"
func azure functionapp publish monica-feed2
```

### Update Keyword Filter

Edit `wef_scraper.py` KEYWORDS list and redeploy:

```bash
git commit -am "Expand keyword filter for renewable energy"
func azure functionapp publish monica-feed2
```

### Manual Execution (Force Run)

If you want to run the function outside the schedule:

```bash
# In Azure Portal:
# Function App → feed2_timer_trigger → Code + Test → Test/Run

# Or via CLI:
curl -X POST https://monica-feed2.azurewebsites.net/admin/functions/feed2_timer_trigger
```

---

## Implementation Checklist

- [ ] Create Azure Function App (Python 3.11)
- [ ] Create Teams incoming webhook
- [ ] Set TEAMS_WEBHOOK_URL environment variable
- [ ] Deploy code (function_app.py + modules)
- [ ] Install dependencies (`pip install -r requirements.txt`)
- [ ] Test locally with `func start`
- [ ] Set up OneDrive folder structure (optional but recommended)
- [ ] Configure OneDrive state manager (optional)
- [ ] Verify execution logs appear in Azure Portal
- [ ] Wait for next scheduled run (05:20 UTC) or manually trigger
- [ ] Check #special-interests Teams channel for test cards
- [ ] Enable monitoring/alerts for function failures
- [ ] Document webhook URL (keep it secret!)
- [ ] Review and approve keyword filter for WEF

---

## Future Enhancements

**Not in MVP, but possible:**

- Filter articles by keyword across all feeds (not just WEF)
- Add "Save article" reaction handler (stores to OneDrive)
- Thread articles by topic in Teams
- Summarise article with Claude AI before posting
- Weigh sources (prioritise certain publications)
- Add configurable feed sources (Teams message/UI)
- Daily digest mode (single message with all articles)

---

## Support & Troubleshooting

**For issues:**

1. Check Azure Portal logs (Function App → Monitor)
2. Review OneDrive execution log (`feed2_execution_log.txt`)
3. Test individual components locally (see Local Testing section)
4. Verify Teams webhook still valid (recreate if > 1 month old)
5. Check feed URLs are still active (visit in browser)

**For questions:**

- Refer to spec document: `2026-08-17_Monica_Feed2_RSS_Intelligence_Prompt_v1_0.md`
- Azure Functions docs: https://learn.microsoft.com/en-us/azure/azure-functions/
- Teams Adaptive Cards: https://adaptivecards.io/

---

**Implementation Date:** August 2026  
**Specification Version:** 1.0  
**Python Version:** 3.11+  
**Status:** Ready for Production
