"""
Monica Feed 2 — RSS Intelligence Feed
Azure Function Timer Trigger

This module is the main entry point for the daily RSS feed aggregation system.
It fetches content from Chatham House, Internet Society, and World Economic Forum,
then posts curated articles to Teams as adaptive cards.

Execution: Daily at 05:20 London time (cron: 0 20 5 * * *)
"""

import azure.functions as func
import logging
from datetime import datetime
import pytz
import json
import os

from feed_parser import FeedParser
from wef_scraper import WEFScraper
from teams_card_builder import TeamsCardBuilder
from state_manager import StateManager
from teams_webhook import TeamsWebhook

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create the Azure Function app
app = func.FunctionApp()


@app.timer_trigger(arg_name="myTimer", schedule="0 20 5 * * *")
def feed2_timer_trigger(myTimer: func.TimerRequest) -> None:
    """
    WHY: This is the main orchestration function that runs daily at 05:20 London time.
    It coordinates all the components: feed fetching, scraping, card generation, and Teams posting.
    
    The cron schedule "0 20 5 * * *" means:
    - Minute: 20 (UTC minute, which is 05:20 London = UTC+1 in summer, UTC in winter)
    - Hour: 5 (UTC)
    - Day: * (any day)
    - Month: * (any month)
    - Day of week: * (any day)
    
    We use UTC for the cron schedule and rely on Azure to handle timezone conversion.
    """
    
    start_time = datetime.now(pytz.UTC)
    utc_timestamp = start_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    
    try:
        logger.info(f"[{utc_timestamp}] Feed 2 execution started")
        
        # Initialise dependencies
        state_manager = StateManager()
        feed_parser = FeedParser()
        wef_scraper = WEFScraper()
        card_builder = TeamsCardBuilder()
        teams_webhook = TeamsWebhook()
        
        # Track metrics for logging
        metrics = {
            'chatham_house_new': 0,
            'internet_society_new': 0,
            'wef_new': 0,
            'cards_posted': 0,
            'duplicates_skipped': 0,
            'errors': 0
        }
        
        # Step 1: Fetch and parse RSS feeds
        # WHY: RSS feeds are the primary source. We parse them first because they're
        # more reliable than web scraping. Chatham House and Internet Society both
        # provide well-structured RSS feeds.
        
        logger.info("Fetching Chatham House RSS feed...")
        chatham_house_articles = feed_parser.fetch_feed(
            url="https://www.chathamhouse.org/path/whatsnew.xml",
            source_name="Chatham House"
        )
        metrics['chatham_house_new'] = len(chatham_house_articles)
        logger.info(f"Chatham House: {len(chatham_house_articles)} new items")
        
        logger.info("Fetching Internet Society RSS feed...")
        internet_society_articles = feed_parser.fetch_feed(
            url="https://www.internetsociety.org/feed/",
            source_name="Internet Society"
        )
        metrics['internet_society_new'] = len(internet_society_articles)
        logger.info(f"Internet Society: {len(internet_society_articles)} new items")
        
        # Step 2: Scrape WEF Emerging Technologies page
        # WHY: WEF doesn't provide an RSS feed, so we scrape their web page.
        # We include keyword filtering to reduce noise (WEF covers many topics;
        # we only want technology-related content).
        
        logger.info("Scraping WEF Emerging Technologies page...")
        try:
            wef_articles = wef_scraper.scrape_and_filter()
            metrics['wef_new'] = len(wef_articles)
            logger.info(f"WEF Technology: {len(wef_articles)} new items (after keyword filtering)")
        except Exception as e:
            logger.warning(f"WEF scrape failed: {e}. Continuing with RSS feeds only.")
            wef_articles = []
        
        # Combine all articles
        all_articles = chatham_house_articles + internet_society_articles + wef_articles
        
        # Step 3: Deduplicate and post cards
        # WHY: We check against previously posted URLs to avoid duplicates.
        # This is critical because the same article might appear across multiple feeds,
        # or a feed might include older articles on certain runs.
        
        logger.info(f"Processing {len(all_articles)} total articles for duplicate detection...")
        
        for article in all_articles:
            # Check if this article was already posted
            if state_manager.is_duplicate(article['link']):
                logger.info(f"Skipping duplicate: {article['link']}")
                metrics['duplicates_skipped'] += 1
                continue
            
            # Skip articles without required fields
            if not article.get('headline') or not article.get('link'):
                logger.warning(f"Skipping malformed article: {article}")
                metrics['errors'] += 1
                continue
            
            # Skip articles without featured image
            # WHY: Per spec, cards require images. We don't post blank-image fallback cards.
            if not article.get('image_url'):
                logger.info(f"Skipping article without image: {article['headline']}")
                continue
            
            try:
                # Build Teams adaptive card
                card = card_builder.build_card(article)
                
                # Post to Teams
                success = teams_webhook.post_card(card)
                
                if success:
                    # Mark as posted in state
                    state_manager.mark_posted(article['link'])
                    metrics['cards_posted'] += 1
                    logger.info(f"Posted card: {article['headline'][:80]}...")
                else:
                    metrics['errors'] += 1
                    logger.error(f"Failed to post card: {article['headline']}")
                    
            except Exception as e:
                metrics['errors'] += 1
                logger.error(f"Error processing article {article.get('headline')}: {e}")
        
        # Log execution summary
        end_time = datetime.now(pytz.UTC)
        duration = (end_time - start_time).total_seconds()
        
        summary_message = (
            f"[{end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}] Feed 2 execution completed\n"
            f"Chatham House: {metrics['chatham_house_new']} new items\n"
            f"Internet Society: {metrics['internet_society_new']} new items\n"
            f"WEF Technology: {metrics['wef_new']} new items\n"
            f"Posted cards: {metrics['cards_posted']}\n"
            f"Duplicates skipped: {metrics['duplicates_skipped']}\n"
            f"Errors: {metrics['errors']}\n"
            f"Duration: {duration:.1f} seconds"
        )
        
        logger.info(summary_message)
        
        # Append to execution log file (OneDrive)
        state_manager.log_execution(summary_message)
        
        # Return status
        if myTimer.past_due:
            logger.warning("Feed 2 execution ran past schedule.")
        
        logger.info("Feed 2 execution completed successfully.")
        
    except Exception as e:
        logger.error(f"Fatal error in Feed 2 execution: {e}", exc_info=True)
        raise
