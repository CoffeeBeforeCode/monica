#!/bin/bash
#
# Feed 2 Deployment Script
#
# Automates Azure resource creation and function deployment.
# This script creates:
#   - Resource Group
#   - Storage Account
#   - Azure Function App (Python 3.11)
#   - Configures environment variables
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh [--teams-webhook-url <url>] [--region <region>] [--name <prefix>]
#
# Examples:
#   ./deploy.sh --teams-webhook-url "https://outlook.webhook.office.com/..."
#   ./deploy.sh --teams-webhook-url "https://..." --region uksouth --name my-monica
#

set -e  # Exit on error

# ========== CONFIGURATION ==========

# Defaults
REGION="uksouth"
NAME_PREFIX="monica-feed2"
TEAMS_WEBHOOK_URL=""

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --teams-webhook-url)
            TEAMS_WEBHOOK_URL="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --name)
            NAME_PREFIX="$2"
            shift 2
            ;;
        --help)
            echo "Usage: ./deploy.sh [options]"
            echo ""
            echo "Options:"
            echo "  --teams-webhook-url <url>  Teams incoming webhook URL (required)"
            echo "  --region <region>          Azure region (default: uksouth)"
            echo "  --name <prefix>            Resource name prefix (default: monica-feed2)"
            echo "  --help                     Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate required inputs
if [ -z "$TEAMS_WEBHOOK_URL" ]; then
    echo "Error: --teams-webhook-url is required"
    echo "Usage: ./deploy.sh --teams-webhook-url <url>"
    exit 1
fi

# ========== SETUP ==========

echo "=================================================="
echo "  Feed 2 Azure Deployment"
echo "=================================================="
echo ""
echo "Configuration:"
echo "  Region: $REGION"
echo "  Prefix: $NAME_PREFIX"
echo "  Webhook URL: ${TEAMS_WEBHOOK_URL:0:50}..."
echo ""

# Derived names
RG_NAME="${NAME_PREFIX}-rg"
STORAGE_NAME="${NAME_PREFIX}storage"
FUNCTION_NAME="${NAME_PREFIX}"

echo "Resource names:"
echo "  Resource Group: $RG_NAME"
echo "  Storage Account: $STORAGE_NAME"
echo "  Function App: $FUNCTION_NAME"
echo ""

# ========== DEPLOYMENT ==========

echo "Step 1: Checking Azure CLI..."
if ! command -v az &> /dev/null; then
    echo "ERROR: Azure CLI not found. Install from https://learn.microsoft.com/en-us/cli/azure/install-azure-cli"
    exit 1
fi

echo "✓ Azure CLI found"
echo ""

echo "Step 2: Logging in to Azure..."
az login --use-device-code 2>/dev/null || az login
echo "✓ Logged in"
echo ""

echo "Step 3: Creating resource group..."
az group create \
    --name "$RG_NAME" \
    --location "$REGION" \
    --output none
echo "✓ Resource group '$RG_NAME' created"
echo ""

echo "Step 4: Creating storage account..."
# Storage account names must be lowercase, 3-24 chars
# Remove hyphens from storage name
STORAGE_NAME_CLEAN="${NAME_PREFIX//[-_]/}"
STORAGE_NAME_CLEAN="$(echo $STORAGE_NAME_CLEAN | tr 'A-Z' 'a-z')"

az storage account create \
    --name "$STORAGE_NAME_CLEAN" \
    --resource-group "$RG_NAME" \
    --location "$REGION" \
    --sku Standard_LRS \
    --output none
echo "✓ Storage account '$STORAGE_NAME_CLEAN' created"
echo ""

echo "Step 5: Creating Azure Function App..."
az functionapp create \
    --resource-group "$RG_NAME" \
    --consumption-plan-location "$REGION" \
    --runtime python \
    --runtime-version 3.11 \
    --functions-version 4 \
    --name "$FUNCTION_NAME" \
    --storage-account "$STORAGE_NAME_CLEAN" \
    --os-type Linux \
    --output none
echo "✓ Function App '$FUNCTION_NAME' created"
echo ""

echo "Step 6: Configuring environment variables..."
az functionapp config appsettings set \
    --name "$FUNCTION_NAME" \
    --resource-group "$RG_NAME" \
    --settings "TEAMS_WEBHOOK_URL=$TEAMS_WEBHOOK_URL" \
    --output none
echo "✓ Environment variables configured"
echo ""

echo "Step 7: Enabling managed identity (for OneDrive)..."
az functionapp identity assign \
    --name "$FUNCTION_NAME" \
    --resource-group "$RG_NAME" \
    --output none
echo "✓ Managed identity enabled"
echo ""

echo "Step 8: Configuring Python packages..."
# Note: Python packages are installed from requirements.txt during deployment
echo "✓ Function App ready for code deployment"
echo ""

# ========== NEXT STEPS ==========

echo "=================================================="
echo "  DEPLOYMENT COMPLETE"
echo "=================================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Deploy the code:"
echo "   cd $(dirname $0)"
echo "   func azure functionapp publish $FUNCTION_NAME"
echo ""
echo "2. Verify deployment:"
echo "   az functionapp log tail --name $FUNCTION_NAME --resource-group $RG_NAME"
echo ""
echo "3. Test the function:"
echo "   curl -X POST https://${FUNCTION_NAME}.azurewebsites.net/admin/functions/feed2_timer_trigger"
echo ""
echo "4. Verify cards in Teams:"
echo "   Check your #special-interests channel for test cards"
echo ""
echo "5. (Optional) Set up OneDrive for persistent state:"
echo "   See README.md section 'OneDrive Setup'"
echo ""

echo "Function App URL: https://${FUNCTION_NAME}.azurewebsites.net"
echo "Resource Group: $RG_NAME"
echo ""
echo "All resources created successfully! ✓"
