"""
Teams Webhook — Microsoft Teams Message Posting

This module handles POSTing Adaptive Cards to Microsoft Teams via incoming webhooks.
It manages retries, timeout handling, and error logging.

WHY webhooks: Teams incoming webhooks are the simplest way to post messages from
an Azure Function. They don't require authentication libraries or Teams SDK setup.
They're also reliable—Teams infrastructure is built to handle webhook traffic.
"""

import requests
import logging
import os
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class TeamsWebhook:
    """
    Posts Adaptive Cards to Teams via webhook.
    """
    
    def __init__(self, timeout: int = 10, max_retries: int = 3):
        """
        Initialise Teams webhook poster.
        
        Args:
            timeout: HTTP request timeout in seconds
            max_retries: Number of retry attempts before failing
        
        WHY store webhook URL on init: The URL is sensitive (it's a webhook token).
        Reading it once at init time, rather than on every call, is cleaner and
        ensures we're using a consistent URL throughout execution.
        """
        
        # Get webhook URL from environment variable
        self.webhook_url = os.getenv('TEAMS_WEBHOOK_URL')
        
        if not self.webhook_url:
            logger.error("TEAMS_WEBHOOK_URL environment variable not set!")
            raise ValueError("TEAMS_WEBHOOK_URL environment variable required")
        
        self.timeout = timeout
        self.max_retries = max_retries
    
    def post_card(self, card: Dict) -> bool:
        """
        Post a single Adaptive Card to Teams.
        
        Args:
            card: Adaptive Card message (from TeamsCardBuilder)
        
        Returns:
            True if POST succeeded, False otherwise
        
        WHY boolean return: Callers need to know success/failure to update state
        (mark article as posted only on success). This allows them to retry or
        skip appropriately.
        """
        
        return self._post_with_retry(card)
    
    def _post_with_retry(self, card: Dict) -> bool:
        """
        POST card to Teams with exponential backoff retry.
        
        WHY exponential backoff: If Teams is temporarily overloaded, a sudden
        retry storm makes it worse. Exponential backoff (1s, 2s, 4s) gives
        the service time to recover.
        
        Returns:
            True if any attempt succeeded, False if all failed
        """
        
        last_error = None
        
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug(f"Posting card to Teams (attempt {attempt}/{self.max_retries})...")
                
                # POST to webhook
                # WHY JSON payload: Adaptive Cards are JSON. Teams expects application/json.
                response = requests.post(
                    self.webhook_url,
                    json=card,
                    timeout=self.timeout,
                    headers={'Content-Type': 'application/json'}
                )
                
                # Check response status
                if response.status_code == 200:
                    logger.info("Card posted successfully to Teams")
                    return True
                
                # 4xx errors (client errors) won't benefit from retry
                if 400 <= response.status_code < 500:
                    logger.error(
                        f"Teams webhook returned {response.status_code}: {response.text}"
                    )
                    logger.error("This is a client error. Not retrying.")
                    return False
                
                # 5xx errors or other issues might be transient
                logger.warning(
                    f"Teams webhook returned {response.status_code}: {response.text}"
                )
                last_error = f"HTTP {response.status_code}"
                
            except requests.Timeout as e:
                last_error = f"Timeout: {e}"
                logger.warning(f"Attempt {attempt}: Request timeout")
            
            except requests.ConnectionError as e:
                last_error = f"Connection error: {e}"
                logger.warning(f"Attempt {attempt}: Connection error")
            
            except Exception as e:
                last_error = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {e}")
                return False
            
            # Retry logic
            if attempt < self.max_retries:
                wait_time = 2 ** (attempt - 1)  # 1s, 2s, 4s
                logger.info(f"Retrying in {wait_time}s...")
                import time
                time.sleep(wait_time)
        
        # All retries exhausted
        logger.error(f"Failed to post card after {self.max_retries} attempts: {last_error}")
        return False
    
    def post_batch(self, cards: list) -> Dict[str, int]:
        """
        Post multiple cards to Teams.
        
        Args:
            cards: List of Adaptive Card messages
        
        Returns:
            Dictionary with success/failure counts
        
        WHY batch method: If we're posting many articles (common for Monday morning
        after the weekend), batch posting keeps the code DRY and makes metrics
        easier to collect.
        """
        
        results = {
            'success': 0,
            'failed': 0
        }
        
        for i, card in enumerate(cards, 1):
            logger.info(f"Posting card {i}/{len(cards)}...")
            if self.post_card(card):
                results['success'] += 1
            else:
                results['failed'] += 1
        
        logger.info(f"Batch post complete: {results['success']} success, {results['failed']} failed")
        
        return results
